"""Data layer for the Sports Meet app — supports SQLite (local dev) and MySQL (production).

Engine is selected by the ``SPORTS_DB_ENGINE`` environment variable (default: ``sqlite``).
Set ``SPORTS_DB_ENGINE=mysql`` and provide ``SPORTS_MYSQL_HOST``, ``SPORTS_MYSQL_USER``,
``SPORTS_MYSQL_PASSWORD``, ``SPORTS_MYSQL_DB`` for MySQL (requires PyMySQL and MySQL 8.0+).

SQLite mode uses the standard-library ``sqlite3`` — no extra dependencies, runs
unchanged on local machines. MySQL mode requires ``pip install pymysql``.
"""
import json
import os
import re
import sqlite3
import threading
import time

# ---- engine detection --------------------------------------------------------

_ENGINE = os.environ.get("SPORTS_DB_ENGINE", "sqlite").lower()
_IS_MYSQL = _ENGINE == "mysql"

# SQLite path (only used when _IS_MYSQL is False)
DB_PATH = os.environ.get(
    "SPORTS_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sportsmeet.db"),
)

# MySQL connection config (only used when _IS_MYSQL is True)
_MY_HOST = os.environ.get("SPORTS_MYSQL_HOST", "localhost")
_MY_USER = os.environ.get("SPORTS_MYSQL_USER", "")
_MY_PASS = os.environ.get("SPORTS_MYSQL_PASSWORD", "")
_MY_DB   = os.environ.get("SPORTS_MYSQL_DB", "")
_MY_PORT = int(os.environ.get("SPORTS_MYSQL_PORT", "3306"))

SCHEMA_VERSION = 10

# Tables that carry a created/modified audit trail (who + when).
AUDIT_TABLES = ["sports_teams", "sports_admins", "sports_sport_categories", "sports_sports",
                "sports_sport_age_categories", "sports_participants", "sports_announcements"]

_local = threading.local()


def get_conn():
    conn = getattr(_local, "conn", None)
    if conn is not None:
        if _IS_MYSQL:
            conn.ping(reconnect=True)
        return conn
    if _IS_MYSQL:
        import pymysql
        import pymysql.cursors
        conn = pymysql.connect(
            host=_MY_HOST, user=_MY_USER, password=_MY_PASS,
            database=_MY_DB, port=_MY_PORT,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )
    else:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
    _local.conn = conn
    return conn


def _sql(sql):
    """Translate SQLite-dialect SQL to the active engine's dialect."""
    if not _IS_MYSQL:
        return sql
    # Escape literal % (e.g. LIKE '%x%' patterns) before introducing %s placeholders,
    # since PyMySQL uses Python's % string formatting for parameter substitution.
    sql = sql.replace("%", "%%")
    sql = sql.replace("?", "%s")
    sql = sql.replace(
        'ON CONFLICT("key") DO UPDATE SET value = excluded.value',
        "ON DUPLICATE KEY UPDATE value=VALUES(value)",
    )
    sql = sql.replace('"user"', "`user`")
    sql = sql.replace('"read"', "`read`")
    sql = sql.replace('"key"', "`key`")
    sql = sql.replace("INSERT OR IGNORE INTO", "INSERT IGNORE INTO")
    return sql


# --- low level helpers -------------------------------------------------------

def query(sql, params=()):
    conn = get_conn()
    if _IS_MYSQL:
        with conn.cursor() as cur:
            cur.execute(_sql(sql), params)
            return list(cur.fetchall())
    cur = conn.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    return rows


def query_one(sql, params=()):
    conn = get_conn()
    if _IS_MYSQL:
        with conn.cursor() as cur:
            cur.execute(_sql(sql), params)
            row = cur.fetchone()
            return row  # DictCursor already returns dict or None
    cur = conn.execute(sql, params)
    row = cur.fetchone()
    cur.close()
    return dict(row) if row else None


def execute(sql, params=()):
    conn = get_conn()
    if _IS_MYSQL:
        with conn.cursor() as cur:
            cur.execute(_sql(sql), params)
            rid = cur.lastrowid
        conn.commit()
        return rid
    cur = conn.execute(sql, params)
    conn.commit()
    rid = cur.lastrowid
    cur.close()
    return rid


def executemany(sql, seq):
    conn = get_conn()
    if _IS_MYSQL:
        with conn.cursor() as cur:
            cur.executemany(_sql(sql), seq)
        conn.commit()
        return
    conn.executemany(sql, seq)
    conn.commit()


def _table_columns(table):
    """Return the set of column names for *table*. Abstracts PRAGMA vs information_schema."""
    if _IS_MYSQL:
        rows = query(
            "SELECT COLUMN_NAME AS name FROM information_schema.COLUMNS "
            "WHERE table_schema=DATABASE() AND table_name=?", (table,)
        )
    else:
        rows = query("PRAGMA table_info({})".format(table))
    return {r["name"] for r in rows}


def now_ts():
    return int(time.time())


def count(table, where="", params=()):
    sql = "SELECT COUNT(*) AS n FROM {}".format(table)
    if where:
        sql += " WHERE " + where
    return query_one(sql, params)["n"]


# --- schema ----------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS sports_programs (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT,
    has_teams    INTEGER NOT NULL DEFAULT 1,
    status       TEXT NOT NULL DEFAULT 'active',
    start_date   TEXT,
    end_date     TEXT,
    created_by   TEXT,
    created_at   INTEGER,
    modified_by  TEXT,
    modified_at  INTEGER,
    sample       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sports_teams (
    id     TEXT PRIMARY KEY,
    name   TEXT NOT NULL,
    colour TEXT,
    created_by  TEXT,
    created_at  INTEGER,
    modified_by TEXT,
    modified_at INTEGER,
    sample INTEGER NOT NULL DEFAULT 0
);
-- Admin login accounts ONLY. Players & captains live in `sports_participants` (which
-- carries its own login). The read-only `sports_users` VIEW (created in _migrate) unions
-- both so the rest of the app can treat them as one account set.
CREATE TABLE IF NOT EXISTS sports_admins (
    id           TEXT PRIMARY KEY,
    username     TEXT UNIQUE NOT NULL,
    name         TEXT NOT NULL,
    roles        TEXT NOT NULL DEFAULT '["admin"]',
    email        TEXT,
    password     TEXT NOT NULL,
    security_question TEXT,
    security_answer   TEXT,
    must_change_pw    INTEGER NOT NULL DEFAULT 0,
    disabled     INTEGER NOT NULL DEFAULT 0,
    last_login   INTEGER,
    created_at   INTEGER,
    created_by   TEXT,
    modified_by  TEXT,
    modified_at  INTEGER,
    notify_prefs TEXT NOT NULL DEFAULT '{}',
    sample       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sports_sport_categories (
    id     TEXT PRIMARY KEY,
    name   TEXT NOT NULL,
    sort   INTEGER NOT NULL DEFAULT 0,
    created_by  TEXT,
    created_at  INTEGER,
    modified_by TEXT,
    modified_at INTEGER,
    sample INTEGER NOT NULL DEFAULT 0
);
-- Master catalogue: the sports_sports we plan to organise (category + name).
-- `name` is intentionally NOT unique here on its own - the same sport name is
-- allowed in different programs (program_id, added below); uniqueness within
-- a program is enforced by the sports_sports_name_program index (see
-- _migrate_sport_name_scope) plus the app-level check in _sport_form().
CREATE TABLE IF NOT EXISTS sports_sports (
    id          TEXT PRIMARY KEY,
    category_id TEXT,
    name        TEXT NOT NULL,
    archived    INTEGER NOT NULL DEFAULT 0,
    created_by  TEXT,
    created_at  INTEGER,
    modified_by TEXT,
    modified_at INTEGER,
    sample      INTEGER NOT NULL DEFAULT 0
);
-- Detail: which sport is allowed for which age category + gender -- carries its
-- own scoring/points, single schedule (date/time/location) and lifecycle status.
-- This is the unit that gets scored. (Replaces the old `events` table.)
CREATE TABLE IF NOT EXISTS sports_sport_age_categories (
    id              TEXT PRIMARY KEY,
    sport_id        TEXT NOT NULL,
    age_category    TEXT,
    gender          TEXT,
    scoring_mode    TEXT NOT NULL DEFAULT 'placement',
    event_format    TEXT NOT NULL DEFAULT 'individual',
    rounds          INTEGER NOT NULL DEFAULT 3,
    points          TEXT,
    places          INTEGER NOT NULL DEFAULT 3,
    date            TEXT,
    slot            TEXT,
    location        TEXT,
    specifics       TEXT,
    finalised       INTEGER NOT NULL DEFAULT 0,
    approval_status TEXT NOT NULL DEFAULT 'draft',
    status          TEXT NOT NULL DEFAULT 'new',
    archived        INTEGER NOT NULL DEFAULT 0,
    created_by      TEXT,
    created_at      INTEGER,
    modified_by     TEXT,
    modified_at     INTEGER,
    sample          INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sports_score_votes (
    sac_id     TEXT NOT NULL,
    captain_id TEXT NOT NULL,
    decision   TEXT NOT NULL DEFAULT 'pending',
    ts         INTEGER,
    PRIMARY KEY (sac_id, captain_id)
);
-- Every participant (player or captain). Carries roster info AND its own login:
-- `user` is the stable login id (referenced by sports_notifications/sports_audit/votes), with
-- username/password/roles on the row. `house` (VA/VB/VC/A/B/C/D) + `number`
-- (3 digits) form a per-house identifier (unique within a house). `roster`=1 for
-- real competitors, 0 for login-only rows (e.g. a captain with no roster entry).
CREATE TABLE IF NOT EXISTS sports_participants (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    team       TEXT,
    division   TEXT,
    birth_year INTEGER,
    category   TEXT,
    user_id    TEXT,
    "user"     TEXT,
    username   TEXT,
    password   TEXT,
    roles      TEXT,
    email      TEXT,
    security_question TEXT,
    security_answer   TEXT,
    must_change_pw    INTEGER NOT NULL DEFAULT 0,
    disabled   INTEGER NOT NULL DEFAULT 0,
    last_login INTEGER,
    notify_prefs TEXT NOT NULL DEFAULT '{}',
    house      TEXT,
    number     TEXT,
    roster     INTEGER NOT NULL DEFAULT 1,
    volunteer  INTEGER NOT NULL DEFAULT 0,
    archived   INTEGER NOT NULL DEFAULT 0,
    pending_team TEXT,
    created_by  TEXT,
    created_at  INTEGER,
    modified_by TEXT,
    modified_at INTEGER,
    sample     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sports_signups (
    participant_id TEXT NOT NULL,
    sport_id       TEXT NOT NULL,
    PRIMARY KEY (participant_id, sport_id)
);
-- For team/doubles events: the captain-chosen competing line-up for one org-team
-- in one SAC. `members` is a JSON list of participant ids (all from that team).
-- No row => default to every eligible signed-up player of the team.
CREATE TABLE IF NOT EXISTS sports_event_lineups (
    sac_id  TEXT NOT NULL,
    team    TEXT NOT NULL,
    members TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (sac_id, team)
);
CREATE TABLE IF NOT EXISTS sports_results (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sac_id       TEXT NOT NULL,
    participant  TEXT NOT NULL,
    place        INTEGER,
    points       INTEGER NOT NULL DEFAULT 0,
    best         REAL,
    rounds       TEXT,
    participated INTEGER NOT NULL DEFAULT 0,
    history      TEXT
);
CREATE TABLE IF NOT EXISTS sports_announcements (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      INTEGER,
    title   TEXT,
    body    TEXT,
    visible INTEGER NOT NULL DEFAULT 1,
    created_by  TEXT,
    created_at  INTEGER,
    modified_by TEXT,
    modified_at INTEGER,
    sample  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sports_notifications (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    ts      INTEGER,
    type    TEXT,
    message TEXT,
    link    TEXT,
    "read" INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sports_audit (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      INTEGER,
    message TEXT
);
CREATE TABLE IF NOT EXISTS sports_settings (
    "key" TEXT PRIMARY KEY,
    value TEXT
);
-- Indexes on the foreign-key columns the app filters/joins on most.
CREATE INDEX IF NOT EXISTS sports_ix_signups_sport      ON sports_signups(sport_id);
CREATE INDEX IF NOT EXISTS sports_ix_results_sac        ON sports_results(sac_id);
CREATE INDEX IF NOT EXISTS sports_ix_results_part       ON sports_results(participant);
CREATE INDEX IF NOT EXISTS sports_ix_sac_sport          ON sports_sport_age_categories(sport_id);
CREATE INDEX IF NOT EXISTS sports_ix_sac_date           ON sports_sport_age_categories(date);
CREATE INDEX IF NOT EXISTS sports_ix_sac_status         ON sports_sport_age_categories(status);
CREATE INDEX IF NOT EXISTS sports_ix_participants_team  ON sports_participants(team);
CREATE INDEX IF NOT EXISTS sports_ix_participants_user  ON sports_participants(user_id);
CREATE INDEX IF NOT EXISTS sports_ix_votes_sac          ON sports_score_votes(sac_id);
CREATE INDEX IF NOT EXISTS sports_ix_notif_user         ON sports_notifications(user_id);
"""


def init_db():
    if _IS_MYSQL:
        _init_db_mysql()
    else:
        conn = get_conn()
        _migrate_rename_tables()   # must run before SCHEMA so sports_* tables don't exist yet
        conn.executescript(SCHEMA)
        conn.commit()
        _migrate()


def _init_db_mysql():
    """Create tables on a fresh MySQL database and run additive migrations."""
    for stmt in _mysql_ddl_statements():
        try:
            execute(stmt, ())
        except Exception as exc:
            msg = str(exc).lower()
            # Ignore "table already exists" and "duplicate key name" (index exists)
            if "already exists" in msg or "duplicate key name" in msg:
                continue
            raise
    _migrate_view_mysql()
    _migrate()


def _mysql_ddl_statements():
    """Transform SQLite SCHEMA DDL into a list of MySQL-compatible statements."""
    stmts = []
    for raw in SCHEMA.split(";"):
        stmt = raw.strip()
        # Strip leading comment lines and blank lines
        lines = [l for l in stmt.splitlines() if not l.strip().startswith("--") and l.strip()]
        stmt = "\n".join(lines).strip()
        if not stmt:
            continue
        stmt = stmt.replace('"user"', "`user`")
        stmt = stmt.replace('"read"', "`read`")
        stmt = stmt.replace('"key"', "`key`")
        # TEXT PRIMARY KEY → VARCHAR(191) NOT NULL PRIMARY KEY
        stmt = re.sub(r"\bTEXT\s+PRIMARY\s+KEY\b", "VARCHAR(191) NOT NULL PRIMARY KEY", stmt, flags=re.IGNORECASE)
        # INTEGER PRIMARY KEY AUTOINCREMENT → INT NOT NULL AUTO_INCREMENT PRIMARY KEY
        stmt = re.sub(r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
                      "INT NOT NULL AUTO_INCREMENT PRIMARY KEY", stmt, flags=re.IGNORECASE)
        # Composite PRIMARY KEY (a, b) on TEXT columns needs an explicit key length in MySQL
        stmt = re.sub(
            r"PRIMARY KEY\s*\(([^)]+)\)",
            lambda m: "PRIMARY KEY (" + ", ".join(c.strip() + "(191)" for c in m.group(1).split(",")) + ")",
            stmt, flags=re.IGNORECASE,
        )
        # Partial WHERE on indexes not supported by MySQL
        stmt = re.sub(r"\s+WHERE\s+\w+\s+IS\s+NOT\s+NULL(\s+AND\s+\w+\s+IS\s+NOT\s+NULL)?", "", stmt)
        if re.match(r"\s*CREATE\s+TABLE", stmt, re.IGNORECASE):
            stmt += " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
        stmts.append(stmt)
    return stmts


def _migrate():
    """Forward-compatible additive migrations (ADD COLUMN where missing).

    NOTE: v2 -> v3 (split `events` into `sports_sports` + `sports_sport_age_categories`, re-keyed
    sports_signups/sports_results/sports_score_votes) is NOT additive — it requires deleting the DB file
    and reseeding. These entries only cover forward-additive columns on the new tables.
    """
    wanted = {
        "sports_participants": [("birth_year", "INTEGER"), ("user_id", "TEXT"),
                         ("pending_team", "TEXT"),
                         ("user", "TEXT"), ("username", "TEXT"), ("password", "TEXT"),
                         ("roles", "TEXT"), ("email", "TEXT"),
                         ("security_question", "TEXT"), ("security_answer", "TEXT"),
                         ("must_change_pw", "INTEGER NOT NULL DEFAULT 0"),
                         ("disabled", "INTEGER NOT NULL DEFAULT 0"), ("last_login", "INTEGER"),
                         ("notify_prefs", "TEXT NOT NULL DEFAULT '{}'"),
                         ("house", "TEXT"), ("number", "TEXT"),
                         ("roster", "INTEGER NOT NULL DEFAULT 1"),
                         ("sample", "INTEGER NOT NULL DEFAULT 0")],
        "sports_sports": [("archived", "INTEGER NOT NULL DEFAULT 0"),
                   ("sample", "INTEGER NOT NULL DEFAULT 0")],
        "sports_sport_age_categories": [("status", "TEXT NOT NULL DEFAULT 'new'"),
                                 ("approval_status", "TEXT NOT NULL DEFAULT 'draft'"),
                                 ("archived", "INTEGER NOT NULL DEFAULT 0"),
                                 ("event_format", "TEXT NOT NULL DEFAULT 'individual'"),
                                 ("sample", "INTEGER NOT NULL DEFAULT 0")],
        "sports_teams": [("sample", "INTEGER NOT NULL DEFAULT 0"),
                  ("program_id", "TEXT")],
        "sports_sport_categories": [("sample", "INTEGER NOT NULL DEFAULT 0"),
                             ("program_id", "TEXT")],
        "sports_sports": [("program_id", "TEXT")],
        "sports_announcements": [("visible", "INTEGER NOT NULL DEFAULT 1"),
                          ("program_id", "TEXT")],
    }
    # v3 -> v4: created/modified audit columns on the admin-managed tables.
    for table in AUDIT_TABLES:
        cols = wanted.setdefault(table, [])
        for col in ("created_by", "created_at", "modified_by", "modified_at"):
            cols.append((col, "INTEGER" if col.endswith("_at") else "TEXT"))
    for table, cols in wanted.items():
        existing = _table_columns(table)
        for name, decl in cols:
            if name not in existing:
                col = '`{}`'.format(name) if (_IS_MYSQL and name == "user") else ('"{}"'.format(name) if name == "user" else name)
                execute("ALTER TABLE {} ADD COLUMN {} {}".format(table, col, decl))
    _migrate_account_split()
    _migrate_program_split()
    _migrate_sport_name_scope()
    set_setting("schema_version", SCHEMA_VERSION)


# Read-only unified view so the rest of the app can query one "users" set across
# the two real tables (admins + login-bearing participants). `id` is the stable
# login id (admins.id, or participants.user) referenced by notifications/audit/votes.
USERS_VIEW_SQL = """
CREATE VIEW sports_users AS
  SELECT id, username, name, roles, NULL AS team, email, password, disabled, last_login,
         must_change_pw, security_question,
         created_at, created_by, modified_by, modified_at, notify_prefs, sample
    FROM sports_admins
  UNION ALL
  SELECT "user" AS id, username, name, roles, team, email, password, disabled, last_login,
         must_change_pw, security_question,
         created_at, created_by, modified_by, modified_at, notify_prefs, sample
    FROM sports_participants WHERE "user" IS NOT NULL
"""

_MYSQL_USERS_VIEW_SQL = """
CREATE OR REPLACE VIEW sports_users AS
  SELECT id, username, name, roles, NULL AS team, email, password, disabled, last_login,
         must_change_pw, security_question,
         created_at, created_by, modified_by, modified_at, notify_prefs, sample
    FROM sports_admins
  UNION ALL
  SELECT `user` AS id, username, name, roles, team, email, password, disabled, last_login,
         must_change_pw, security_question,
         created_at, created_by, modified_by, modified_at, notify_prefs, sample
    FROM sports_participants WHERE `user` IS NOT NULL
"""


def _migrate_view_mysql():
    execute(_MYSQL_USERS_VIEW_SQL, ())


def _migrate_rename_tables():
    """v8 -> v9: add sports_ prefix to all table/index names. Idempotent. SQLite only."""
    if _IS_MYSQL:
        return
    objs = query("SELECT name, type FROM sqlite_master")
    obj_types = {r["name"]: r["type"] for r in objs}

    old_tables = [
        "programs", "teams", "admins", "sport_categories", "sports",
        "sport_age_categories", "score_votes", "participants", "signups",
        "event_lineups", "results", "announcements", "notifications",
        "audit", "settings",
    ]
    new_tables = ["sports_" + t if t != "sports" else "sports_sports" for t in old_tables]
    new_tables = [
        "sports_programs", "sports_teams", "sports_admins", "sports_sport_categories",
        "sports_sports", "sports_sport_age_categories", "sports_score_votes",
        "sports_participants", "sports_signups", "sports_event_lineups",
        "sports_results", "sports_announcements", "sports_notifications",
        "sports_audit", "sports_settings",
    ]
    for old, new in zip(old_tables, new_tables):
        if old not in obj_types:
            continue
        if new in obj_types:
            # sports_* already exists (data split): drop the old table (it is superseded)
            execute("DROP TABLE IF EXISTS {}".format(old))
        else:
            execute("ALTER TABLE {} RENAME TO {}".format(old, new))

    # Drop old un-prefixed named indexes; SCHEMA recreates them as sports_ix_*
    old_indexes = [
        "ix_signups_sport", "ix_results_sac", "ix_results_part",
        "ix_sac_sport", "ix_sac_date", "ix_sac_status",
        "ix_participants_team", "ix_participants_user", "ix_votes_sac",
        "ix_notif_user", "ix_participants_login", "ux_participants_house_number",
    ]
    for idx in old_indexes:
        if idx in obj_types:
            execute("DROP INDEX IF EXISTS {}".format(idx))

    # Drop old users VIEW; drop old unprefixed users TABLE if it somehow exists.
    if "users" in obj_types:
        if obj_types["users"] == "view":
            execute("DROP VIEW IF EXISTS users")
        else:
            execute("DROP TABLE IF EXISTS users")


def _migrate_account_split():
    """v6 -> v7: split legacy sports_users table into admins + participant login columns.
    SQLite: full migration from old schema. MySQL: only ensures view + indexes exist."""
    if _IS_MYSQL:
        _migrate_view_mysql()
        try:
            execute("CREATE INDEX IF NOT EXISTS sports_ix_participants_login ON sports_participants(`user`)", ())
        except Exception as exc:
            if "duplicate key name" not in str(exc).lower():
                raise
        try:
            execute("CREATE UNIQUE INDEX IF NOT EXISTS sports_ux_participants_house_number "
                    "ON sports_participants(house, number)", ())
        except Exception as exc:
            if "duplicate key name" not in str(exc).lower():
                raise
        return
    kinds = {r["name"]: r["type"] for r in
             query("SELECT name, type FROM sqlite_master WHERE name IN ('sports_users','sports_admins','sports_participants')")}
    if kinds.get("sports_users") == "table":
        for r in query("SELECT * FROM sports_users"):
            roles = loads(r.get("roles"), []) or []
            if "admin" in roles:
                if not query_one("SELECT 1 FROM sports_admins WHERE id=?", (r["id"],)):
                    execute("INSERT INTO sports_admins(id, username, name, roles, email, password, disabled, "
                            "last_login, created_at, created_by, modified_by, modified_at, notify_prefs, sample) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (r["id"], r["username"], r["name"], r.get("roles") or '["admin"]',
                             r.get("email"), r["password"], r.get("disabled") or 0, r.get("last_login"),
                             r.get("created_at"), r.get("created_by"), r.get("modified_by"),
                             r.get("modified_at"), r.get("notify_prefs") or "{}", r.get("sample") or 0))
            else:
                p = query_one("SELECT * FROM sports_participants WHERE user_id=?", (r["id"],))
                if p:
                    execute('UPDATE sports_participants SET "user"=?, username=?, password=?, roles=?, email=?, '
                            "disabled=?, last_login=?, notify_prefs=? WHERE id=?",
                            (r["id"], r["username"], r["password"], r.get("roles") or '["player"]',
                             r.get("email"), r.get("disabled") or 0, r.get("last_login"),
                             r.get("notify_prefs") or "{}", p["id"]))
                else:
                    pid = next_id("sports_participants", "R")
                    execute('INSERT INTO sports_participants(id, name, team, "user", username, password, roles, '
                            "email, disabled, last_login, notify_prefs, roster, archived, sample, created_at) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,0,0,?,?)",
                            (pid, r["name"], r.get("team"), r["id"], r["username"], r["password"],
                             r.get("roles") or '["captain"]', r.get("email"), r.get("disabled") or 0,
                             r.get("last_login"), r.get("notify_prefs") or "{}",
                             r.get("sample") or 0, r.get("created_at")))
        execute("DROP TABLE sports_users")
    if kinds.get("sports_users") != "view":
        execute("DROP VIEW IF EXISTS sports_users")
        execute(USERS_VIEW_SQL)
    execute('CREATE INDEX IF NOT EXISTS sports_ix_participants_login ON sports_participants("user")')
    execute("CREATE UNIQUE INDEX IF NOT EXISTS sports_ux_participants_house_number "
            "ON sports_participants(house, number) WHERE house IS NOT NULL AND number IS NOT NULL")


def _migrate_program_split():
    """v7 -> v8: introduce sports_programs table; backfill existing data into a default program.
    Idempotent: only runs when no sports_programs exist yet."""
    if count("sports_programs") > 0:
        return
    event_name = get_setting("event_name", "Community Sports Meet 2026")
    execute(
        "INSERT OR IGNORE INTO sports_programs(id, name, status, has_teams, sample) VALUES(?,?,?,?,?)",
        ("default", event_name, "active", 1, 1),
    )
    for table in ("sports_sport_categories", "sports_sports", "sports_teams", "sports_announcements"):
        execute("UPDATE {} SET program_id='default' WHERE program_id IS NULL".format(table))


def _migrate_sport_name_scope():
    """v9 -> v10: sports_sports.name carried a single-column UNIQUE constraint from
    before multi-program support existed, so the same sport name couldn't be used
    in two different programs even though sport_categories/teams already allowed
    it. Drop that constraint and replace it with a composite (name, program_id)
    unique index. Idempotent on both engines."""
    if _IS_MYSQL:
        by_index = {}
        for r in query("SHOW INDEX FROM sports_sports"):
            by_index.setdefault(r["Key_name"], []).append(r["Column_name"])
        for idx_name, cols in by_index.items():
            if idx_name != "PRIMARY" and cols == ["name"]:
                execute("ALTER TABLE sports_sports DROP INDEX `{}`".format(idx_name))
        if "sports_sports_name_program" not in by_index:
            execute("CREATE UNIQUE INDEX sports_sports_name_program ON sports_sports(name, program_id)")
    else:
        has_bad_constraint = False
        for idx in query("PRAGMA index_list(sports_sports)"):
            if idx["unique"] and idx["name"].startswith("sqlite_autoindex"):
                cols = query("PRAGMA index_info({})".format(idx["name"]))
                if [c["name"] for c in cols] == ["name"]:
                    has_bad_constraint = True
        if has_bad_constraint:
            execute(
                "CREATE TABLE sports_sports_new ("
                "id TEXT PRIMARY KEY, category_id TEXT, name TEXT NOT NULL, "
                "archived INTEGER NOT NULL DEFAULT 0, created_by TEXT, created_at INTEGER, "
                "modified_by TEXT, modified_at INTEGER, sample INTEGER NOT NULL DEFAULT 0, "
                "program_id TEXT)"
            )
            execute(
                "INSERT INTO sports_sports_new SELECT id, category_id, name, archived, "
                "created_by, created_at, modified_by, modified_at, sample, program_id "
                "FROM sports_sports"
            )
            execute("DROP TABLE sports_sports")
            execute("ALTER TABLE sports_sports_new RENAME TO sports_sports")
        existing_idx = {i["name"] for i in query("PRAGMA index_list(sports_sports)")}
        if "sports_sports_name_program" not in existing_idx:
            execute("CREATE UNIQUE INDEX sports_sports_name_program ON sports_sports(name, program_id)")


def next_id(table, prefix, pad=3):
    rows = query("SELECT id FROM {} WHERE id LIKE ?".format(table), (prefix + "%",))
    max_n = 0
    for r in rows:
        rid = str(r["id"])
        try:
            max_n = max(max_n, int(rid[len(prefix):]))
        except ValueError:
            pass
    return "{}{:0{}d}".format(prefix, max_n + 1, pad)


# --- JSON helpers ----------------------------------------------------------

def loads(value, default=None):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def dumps(value):
    return json.dumps(value)


# --- settings --------------------------------------------------------------

def get_setting(key, default=None):
    row = query_one('SELECT value FROM sports_settings WHERE "key"=?', (key,))
    if not row:
        return default
    return loads(row["value"], default)


def set_setting(key, value):
    execute(
        'INSERT INTO sports_settings("key", value) VALUES(?, ?) '
        'ON CONFLICT("key") DO UPDATE SET value = excluded.value',
        (key, dumps(value)),
    )  # _sql() translates ON CONFLICT → ON DUPLICATE KEY for MySQL
