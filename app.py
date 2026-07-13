"""Sports Meet Management — Flask application (SQLite-backed, dynamic site).

Users have one or more roles (player / captain / admin); permissions
are additive. Players self-register (with birth year + gender) and land
unassigned until a captain claims them onto a team. Notifications keep captains
informed; players see only admin-curated sports_announcements. Sports define their own
points/places. Admins maintain everything from separate maintenance pages and
can wipe the sample data once their real data is in.
"""
import functools
import os
import secrets
import time
from datetime import date, datetime, timedelta

from flask import (
    Flask, render_template, request, redirect, url_for, session,
    flash, abort, Response, g, send_from_directory,
)

import db
import domain
import security

app = Flask(__name__)
app.secret_key = os.environ.get("SPORTS_SECRET_KEY", "dev-change-me-in-production")
app.permanent_session_lifetime = 30 * 60

# --- Security / production config ------------------------------------------
_DEBUG = os.environ.get("SPORTS_DEBUG", "1") not in ("0", "false", "False", "")
# Secure cookie flags. Secure-only cookies in production (HTTPS); HttpOnly always;
# SameSite=Lax blocks cross-site form posts (defence-in-depth alongside CSRF tokens).
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=not _DEBUG,
)
# Refuse to boot in production with the throwaway dev secret key.
if not _DEBUG and app.secret_key == "dev-change-me-in-production":
    raise RuntimeError(
        "SPORTS_SECRET_KEY must be set to a strong random value in production "
        "(running with SPORTS_DEBUG=0). Generate one with: "
        "python -c \"import secrets; print(secrets.token_urlsafe(48))\"")

_login_failures = {}
MAX_FAILURES = 5
FAILURE_WINDOW = 15 * 60
UPCOMING_DAYS = 7

NOTIFY_TYPES = [
    ("new_player", "New players needing a team"),
    ("roster", "Roster requests & decisions"),
    ("assignment", "Your own team add / removal"),
    ("approval", "Result approvals & published results"),
    ("admin", "Admin & configuration changes"),
    ("announcement", "New announcements"),
]

# Which notification types matter to each role. A user gets a notification only if
# its type is relevant to at least one role they hold (and they haven't muted it).
ROLE_NOTIFY = {
    "player": {"assignment", "approval", "announcement"},
    "captain": {"new_player", "roster", "approval", "announcement"},
    "admin": {"roster", "admin", "approval", "announcement"},
}


def role_relevant(roles, ntype):
    wanted = set()
    for r in roles or []:
        wanted |= ROLE_NOTIFY.get(r, set())
    return ntype in wanted


# --------------------------------------------------------------------------
# User / role helpers
# --------------------------------------------------------------------------
def decode_user(u):
    if u is None:
        return None
    u["roles"] = db.loads(u.get("roles"), []) or []
    u["notify_prefs"] = db.loads(u.get("notify_prefs"), {}) or {}
    u["is_admin"] = "admin" in u["roles"]
    u["is_captain"] = "captain" in u["roles"]
    u["is_player"] = "player" in u["roles"]
    u["is_staff"] = domain.is_staff(u)
    return u


def current_user():
    # Cache per request: this is hit by the context processor, decorators and views.
    if "cur_user" in g:
        return g.cur_user
    uid = session.get("uid")
    u = None
    if uid:
        u = decode_user(db.query_one("SELECT * FROM sports_users WHERE id=? AND disabled=0", (uid,)))
        if u:
            act = session.get("active_role")
            if act not in u["roles"]:
                act = domain.default_active_role(u["roles"])
                session["active_role"] = act
            u["acting"] = act
    g.cur_user = u
    return u


# --- account layer (admins table + login-bearing participants) -------------
# Accounts physically live in two tables: `admins` and `participants` (which now
# carry their own login). The read-only `users` view unions them for queries; the
# helpers below route WRITES to the correct real table, keyed by the stable login
# id (admins.id, or participants."user").

def account_table_of(login_id):
    """Return 'admins', 'participants', or None for a login id."""
    if not login_id:
        return None
    if db.query_one("SELECT 1 FROM sports_admins WHERE id=?", (login_id,)):
        return "sports_admins"
    if db.query_one('SELECT 1 FROM sports_participants WHERE "user"=?', (login_id,)):
        return "sports_participants"
    return None


def _account_where(login_id):
    """(table, key-column) to address an account row by its login id."""
    t = account_table_of(login_id)
    if t == "sports_admins":
        return "sports_admins", "id"
    if t == "sports_participants":
        return "sports_participants", '"user"'
    return None, None


def update_account(login_id, **fields):
    """Update arbitrary columns on whichever real table holds this login."""
    table, key = _account_where(login_id)
    if not table or not fields:
        return
    cols = ", ".join("{}=?".format(c) for c in fields)
    db.execute("UPDATE {} SET {} WHERE {}=?".format(table, cols, key),
               (*fields.values(), login_id))


def touch_last_login(login_id):
    update_account(login_id, last_login=db.now_ts())


def _norm_answer(ans):
    return (ans or "").strip().lower()


def set_account_password(login_id, password, must_change=0):
    update_account(login_id, password=security.hash_password(password),
                   must_change_pw=must_change)


def verify_security_answer(login_id, answer):
    table, key = _account_where(login_id)
    if not table:
        return False
    row = db.query_one("SELECT security_answer FROM {} WHERE {}=?".format(table, key), (login_id,))
    if not row or not row.get("security_answer"):
        return False
    return security.verify_password(_norm_answer(answer), row["security_answer"])


def hash_answer(answer):
    """Hash a (normalised) security answer, or None if blank."""
    a = _norm_answer(answer)
    return security.hash_password(a) if a else None


def apply_security_question(u, current, sq, sa):
    """Verify current password and persist a security-question change on its own,
    independent of a password change. Returns (errors, changed) - errors is a list of
    flash-ready strings; changed is False when sq was blank (nothing to update)."""
    if not sq:
        return [], False
    row = db.query_one("SELECT password FROM sports_users WHERE id=?", (u["id"],))
    if not u.get("must_change_pw") and not security.verify_password(current, row["password"]):
        return ["Your current password is incorrect."], False
    fields = {"security_question": sq}
    if sa.strip():
        fields["security_answer"] = hash_answer(sa)
    update_account(u["id"], **fields)
    return [], True


def config():
    if "cfg" not in g:
        g.cfg = domain.get_config()
    return g.cfg


def current_program():
    if "cur_prog" in g:
        return g.cur_prog
    pid = session.get("active_program")
    p = db.query_one("SELECT * FROM sports_programs WHERE id=?", (pid,)) if pid else None
    g.cur_prog = p
    return p


def teams():
    if "sports_teams" not in g:
        p = current_program()
        if p:
            g.teams = db.query("SELECT * FROM sports_teams WHERE program_id=? ORDER BY name", (p["id"],))
        else:
            g.teams = []
    return g.teams


def sport_categories():
    if "scats" not in g:
        p = current_program()
        if p:
            g.scats = db.query(
                "SELECT * FROM sports_sport_categories WHERE program_id=? ORDER BY sort, name", (p["id"],))
        else:
            g.scats = []
    return g.scats


@app.template_filter("dmy")
def _dmy(value):
    """Format a date / ISO date string as dd/mmm/yy (e.g. 16/Jun/26)."""
    if not value:
        return "—"
    try:
        d = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return value
    return d.strftime("%d/%b/%y")


@app.template_filter("ts_dmy")
def _ts_dmy(value):
    """Format a unix timestamp as '16 Jun 2026' (used by the audit line)."""
    if not value:
        return "—"
    try:
        return datetime.fromtimestamp(int(value)).strftime("%d %b %Y")
    except (TypeError, ValueError, OSError):
        return "—"


# --- Sports (master catalogue) -------------------------------------------
def decode_sport(s):
    if s is None:
        return None
    s["category_name"] = domain.sport_category_name(s.get("category_id"), sport_categories())
    return s


def all_sports(include_archived=False):
    p = current_program()
    pid = p["id"] if p else None
    if not pid:
        return []
    cond = "program_id=?" + ("" if include_archived else " AND archived=0")
    return [decode_sport(s) for s in db.query(
        "SELECT * FROM sports_sports WHERE {} ORDER BY name".format(cond), (pid,))]


def get_sport(sid):
    return decode_sport(db.query_one("SELECT * FROM sports_sports WHERE id=?", (sid,)))


def sport_in_use(sid):
    """A master sport is 'in use' once any sport-age-category references it."""
    return db.count("sports_sport_age_categories", "sport_id=?", (sid,)) > 0


# --- Sport-Age-Categories (the scored/scheduled detail) ------------------
_SAC_SELECT = (
    "SELECT sac.*, s.name AS sport_name, s.category_id AS category_id, "
    "s.archived AS sport_archived FROM sports_sport_age_categories sac "
    "JOIN sports_sports s ON s.id = sac.sport_id"
)


def decode_sac(sac):
    if sac is None:
        return None
    sac["points_map"] = domain.event_points_map(sac)
    sac["category_name"] = domain.sport_category_name(sac.get("category_id"), sport_categories())
    sac["name"] = sac.get("sport_name")  # alias so templates reading e.name keep working
    return sac


def load_sacs(where="", params=()):
    sql = _SAC_SELECT
    if where:
        sql += " WHERE " + where
    return [decode_sac(r) for r in db.query(sql, params)]


def get_sac(sid):
    return decode_sac(db.query_one(_SAC_SELECT + " WHERE sac.id=?", (sid,)))


def sac_label(sac):
    """Readable label like '100m Sprint · U18 · Male'."""
    bits = [sac.get("sport_name") or "?", sac.get("age_category") or "All ages",
            sac.get("gender") or "Mixed"]
    return " · ".join(bits)


def eligible_participants(sac):
    """Participants signed up for this SAC's sport whose age category & gender match.

    A NULL age_category ('All ages') or gender ('Mixed') on the SAC acts as a
    wildcard so those entries still match everyone signed up for the sport.
    """
    where = "su.sport_id=? AND p.archived=0"
    params = [sac["sport_id"]]
    if sac.get("age_category"):
        where += " AND p.category=?"
        params.append(sac["age_category"])
    if sac.get("gender"):
        where += " AND p.division=?"
        params.append(sac["gender"])
    return db.query("SELECT p.* FROM sports_participants p JOIN sports_signups su ON su.participant_id=p.id "
                    "WHERE " + where + " ORDER BY p.name", params)


def team_lineup_ids(sac_id, team_id):
    """The captain-chosen line-up (set of participant ids) for a team in a SAC,
    or None if no line-up has been saved (meaning: default to all eligible)."""
    row = db.query_one("SELECT members FROM sports_event_lineups WHERE sac_id=? AND team=?",
                       (sac_id, team_id))
    if row is None:
        return None
    return set(db.loads(row["members"], []) or [])


def eligible_teams(sac):
    """For team/doubles events: the teams with at least one eligible signed-up
    player. Each row carries the team, its full eligible roster (`players`) and the
    captain-chosen competing line-up (`members`, defaulting to the full roster)."""
    by_team = {}
    for p in eligible_participants(sac):
        if p.get("team"):
            by_team.setdefault(p["team"], []).append(p)
    rows = []
    for t in teams():
        if t["id"] in by_team:
            all_players = by_team[t["id"]]
            ids = team_lineup_ids(sac["id"], t["id"])
            members = all_players if ids is None else [p for p in all_players if p["id"] in ids]
            rows.append({"team": t, "players": all_players, "members": members,
                         "lineup_set": ids is not None})
    return rows


def team_standings():
    """All teams ranked by total points: members' individual results plus any
    team/doubles event results keyed to the team id."""
    rows = []
    for t in teams():
        ind = db.query_one("SELECT COALESCE(SUM(r.points),0) AS s FROM sports_results r "
                           "JOIN sports_participants p ON p.id=r.participant WHERE p.team=?",
                           (t["id"],))["s"]
        tm = db.query_one("SELECT COALESCE(SUM(points),0) AS s FROM sports_results WHERE participant=?",
                          (t["id"],))["s"]
        rows.append({"id": t["id"], "name": t["name"], "colour": t.get("colour"),
                     "points": (ind or 0) + (tm or 0)})
    rows.sort(key=lambda r: (-r["points"], r["name"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def locked_sport_ids(pid):
    """Sports a participant has a recorded result in (in a completed event) —
    these sign-ups can't be removed."""
    return {r["sid"] for r in db.query(
        "SELECT DISTINCT sac.sport_id AS sid FROM sports_results r "
        "JOIN sports_sport_age_categories sac ON sac.id=r.sac_id "
        "WHERE r.participant=? AND (sac.finalised=1 OR sac.status='completed')", (pid,))}


def eligible_sport_ids(p, open_only=False):
    """Sport ids whose admin-defined events match this participant's age & gender
    (NULL age/gender on an event acts as a wildcard). When open_only is set, the
    matching event must also be 'Open to register' (players can't register for
    'New'/scheduled/etc. events)."""
    ids = set()
    for sac in db.query("SELECT sport_id, age_category, gender, status FROM sports_sport_age_categories "
                        "WHERE archived=0"):
        if open_only and sac["status"] != "registration_open":
            continue
        if (sac["age_category"] is None or sac["age_category"] == p.get("category")) and \
           (sac["gender"] is None or sac["gender"] == p.get("division")):
            ids.add(sac["sport_id"])
    return ids


def decode_result(r):
    if r is None:
        return None
    r["rounds"] = db.loads(r.get("rounds"), []) or []
    r["history"] = db.loads(r.get("history"), []) or []
    return r


def participant_sport_ids(pid):
    return [s["sport_id"] for s in
            db.query("SELECT sport_id FROM sports_signups WHERE participant_id=?", (pid,))]


def linked_participant(u):
    if not u:
        return None
    # A player/captain account IS a participant row (linked by participants."user").
    return db.query_one('SELECT * FROM sports_participants WHERE "user"=? AND archived=0', (u["id"],))


# --------------------------------------------------------------------------
# Activity log & notifications
# --------------------------------------------------------------------------
def log_activity(message):
    db.execute("INSERT INTO sports_audit(ts, message) VALUES(?,?)", (db.now_ts(), message))
    db.execute("DELETE FROM sports_audit WHERE id NOT IN "
               "(SELECT id FROM sports_audit ORDER BY id DESC LIMIT 200)")


def audit_stamp(table, row_id, created=False, actor=None):
    """Record who created / last-modified a row, and when. Call right after the
    INSERT (created=True) or UPDATE. `actor` overrides the current user (used by
    self-registration, where there is no logged-in user yet)."""
    if row_id is None:
        return
    if actor is None:
        cu = current_user()
        actor = cu["id"] if cu else None
    ts = db.now_ts()
    if created:
        db.execute("UPDATE {} SET created_by=?, created_at=? WHERE id=?".format(table),
                   (actor, ts, row_id))
    else:
        db.execute("UPDATE {} SET modified_by=?, modified_at=? WHERE id=?".format(table),
                   (actor, ts, row_id))


def display_name(uid):
    """User id -> display name for the audit line."""
    if not uid or uid == "system":
        return "system"
    row = db.query_one("SELECT name FROM sports_users WHERE id=?", (uid,))
    return row["name"] if row else "(removed user)"


def display_username(uid):
    """User id -> login username for audit columns."""
    if not uid or uid == "system":
        return "system"
    row = db.query_one("SELECT username FROM sports_users WHERE id=?", (uid,))
    return row["username"] if row else "(removed)"


# Expose as Jinja globals so imported macros (e.g. _audit.html) can use them
# without needing `with context`.
app.jinja_env.globals["user_name"] = display_name
app.jinja_env.globals["display_username"] = display_username


def _muted(user_row, ntype):
    prefs = db.loads(user_row.get("notify_prefs"), {}) or {}
    if prefs.get("mute_all"):
        return True
    return ntype in (prefs.get("muted") or [])


def notify(user_ids, ntype, message, link=""):
    ts = db.now_ts()
    for uid in user_ids:
        row = db.query_one("SELECT roles, notify_prefs FROM sports_users WHERE id=?", (uid,))
        if not row or _muted(row, ntype):
            continue
        # Only deliver if the type is relevant to a role this user holds.
        if not role_relevant(db.loads(row["roles"], []) or [], ntype):
            continue
        db.execute('INSERT INTO sports_notifications(user_id, ts, type, message, link, "read") '
                   "VALUES(?,?,?,?,?,0)", (uid, ts, ntype, message, link))


def notify_roles(roles, ntype, message, link=""):
    # One full scan regardless of how many roles are matched (was previously one
    # scan *per role*, e.g. 3x for domain.ALL_ROLES) - same result, fewer round-trips.
    role_set = set(roles)
    ids = {row["id"] for row in db.query("SELECT id, roles FROM sports_users WHERE disabled=0")
           if role_set & set(db.loads(row["roles"], []) or [])}
    notify(list(ids), ntype, message, link)


def unread_count(u):
    if not u:
        return 0
    return db.count("sports_notifications", 'user_id=? AND "read"=0', (u["id"],))


# --------------------------------------------------------------------------
# Request lifecycle
# --------------------------------------------------------------------------
@app.before_request
def enforce_session_timeout():
    if session.get("uid"):
        now = time.time()
        last = session.get("last_seen", now)
        max_idle = 24 * 3600 if session.get("remember") else \
            app.permanent_session_lifetime.total_seconds()
        if now - last > max_idle:
            session.clear()
            flash("Session expired. Please log in again.", "warning")
            return redirect(url_for("login"))
        session["last_seen"] = now


@app.before_request
def force_password_change():
    """Users created by an admin (default password) must set their own password
    before doing anything else."""
    if not session.get("uid"):
        return
    allowed = {"change_password", "logout", "static", "switch_role"}
    if request.endpoint in allowed:
        return
    u = current_user()
    if u and u.get("must_change_pw"):
        return redirect(url_for("change_password"))


@app.before_request
def require_program():
    """Logged-in sports_users must select a program before accessing any page."""
    if not session.get("uid"):
        return
    _program_exempt = {
        "change_password", "logout", "static", "switch_role",
        "forgot_password", "register", "login",
        "select_program",
        "admin_programs", "admin_program_new", "admin_program_edit",
        "admin_program_switch", "admin_program_archive",
    }
    if request.endpoint in _program_exempt:
        return
    pid = session.get("active_program")
    if pid:
        p = db.query_one("SELECT * FROM sports_programs WHERE id=?", (pid,))
        _is_admin = bool(db.query_one("SELECT 1 FROM sports_admins WHERE id=?", (session["uid"],)))
        if p and (_is_admin or p["status"] in domain.PROGRAM_VISIBLE_STATUSES):
            return
    session.pop("active_program", None)
    # Auto-select if only one active program exists
    active = db.query("SELECT id FROM sports_programs WHERE status='active' ORDER BY name")
    if len(active) == 1:
        session["active_program"] = active[0]["id"]
        return
    return redirect(url_for("select_program"))


@app.before_request
def csrf_protect():
    """Lightweight CSRF: a per-session token must accompany every mutating request.
    The token is auto-injected into POST forms by a small script in base.html, and
    also accepted via the X-CSRFToken header."""
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("_csrf") or request.headers.get("X-CSRFToken")
        if not sent or not secrets.compare_digest(str(sent), str(session["_csrf"])):
            abort(400, "CSRF token missing or invalid — please reload and try again.")


@app.after_request
def set_csrf_cookie(resp):
    """Mirror the CSRF token in a readable cookie so scripts/tests can fetch it."""
    token = session.get("_csrf")
    if token:
        resp.set_cookie("csrf_token", token, samesite="Lax",
                        secure=app.config.get("SESSION_COOKIE_SECURE", False))
    return resp


UI_VARIANT = os.environ.get("SPORTS_UI", "classic")


@app.context_processor
def inject_globals():
    u = current_user()
    return {"user": u, "cfg": config(), "unread": unread_count(u),
            "ALL_ROLES": domain.ALL_ROLES, "ui": UI_VARIANT,
            "STATUS_META": domain.STATUS_META, "user_name": display_name,
            "csrf_token": session.get("_csrf", ""),
            "program": current_program()}


def login_required(view):
    @functools.wraps(view)
    def wrapped(*a, **kw):
        if not current_user():
            return redirect(url_for("login", next=request.path))
        return view(*a, **kw)
    return wrapped


def roles_required(*allowed):
    def deco(view):
        @functools.wraps(view)
        def wrapped(*a, **kw):
            u = current_user()
            if not u:
                return redirect(url_for("login", next=request.path))
            if not domain.has_role(u, *allowed):
                abort(403)
            return view(*a, **kw)
        return wrapped
    return deco


def _who(u):
    if u["is_captain"] and u.get("team"):
        return "Captain ({})".format(domain.team_name(u["team"], teams()))
    if u["is_admin"]:
        return "Admin"
    return u["name"]


# --------------------------------------------------------------------------
# PWA — service worker must be served from root scope
# --------------------------------------------------------------------------
@app.route("/sw.js")
def service_worker():
    resp = send_from_directory(app.static_folder, "sw.js",
                               mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        remember = bool(request.form.get("remember"))
        fails = [t for t in _login_failures.get(username, []) if time.time() - t < FAILURE_WINDOW]
        _login_failures[username] = fails
        if len(fails) >= MAX_FAILURES:
            flash("Too many failed attempts. Try again in a few minutes.", "danger")
            return render_template("login.html")
        user = db.query_one("SELECT * FROM sports_users WHERE lower(username)=? OR lower(email)=?",
                            (username, username))
        if user and not user["disabled"] and security.verify_password(password, user["password"]):
            session.clear()
            session.permanent = True
            session["uid"] = user["id"]
            session["remember"] = remember
            session["last_seen"] = time.time()
            _login_failures.pop(username, None)
            touch_last_login(user["id"])
            if user.get("must_change_pw"):
                flash("Please set a new password to finish setting up your account.", "warning")
                return redirect(url_for("change_password"))
            flash("Welcome, {}.".format(user["name"]), "success")
            next_url = request.args.get("next") or ""
            if next_url and next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect(url_for("dashboard"))
        _login_failures.setdefault(username, []).append(time.time())
        flash("Invalid username or password.", "danger")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("dashboard"))
    form = {"role": "player"}
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        name = (request.form.get("name") or "").strip()
        role = request.form.get("role")
        team = request.form.get("team") or None
        password = request.form.get("password") or ""
        role_password = request.form.get("role_password") or ""
        email = (request.form.get("email") or "").strip()
        birth_year = request.form.get("birth_year")
        division = request.form.get("division")
        house = request.form.get("house") or None
        number = (request.form.get("number") or "").strip()
        sec_q = (request.form.get("security_question") or "").strip()
        sec_a = request.form.get("security_answer") or ""
        form = {"username": username, "name": name, "role": role, "team": team,
                "email": email, "birth_year": birth_year, "division": division,
                "house": house, "number": number, "security_question": sec_q}

        errors = []
        if not username or not name:
            errors.append("Username and display name are required.")
        if role not in domain.SELF_REGISTER_ROLES:
            errors.append("You can register as player or captain only.")
        if db.query_one("SELECT 1 FROM sports_users WHERE lower(username)=?", (username.lower(),)):
            errors.append("That username is already taken.")
        errors += ["Password: " + e for e in security.password_errors(password)]
        if not sec_q or not _norm_answer(sec_a):
            errors.append("Pick a security question and answer it (used to recover your password).")

        if role == "player":
            try:
                by = int(birth_year)
                if by < 1900 or by > domain.current_year():
                    raise ValueError
            except (TypeError, ValueError):
                errors.append("Enter a valid birth year.")
            if division not in domain.DIVISIONS:
                errors.append("Select your gender.")
            if house not in domain.HOUSES:
                errors.append("Pick your house.")
            if not (number.isdigit() and len(number) == 3):
                errors.append("Enter your 3-digit house number.")
            elif house in domain.HOUSES and db.query_one(
                    "SELECT 1 FROM sports_participants WHERE house=? AND number=?", (house, number)):
                errors.append("That number is already taken in house {}.".format(house))
        if role == "captain":
            stored = db.get_setting("role_pw_" + role)
            if not stored or not security.verify_password(role_password, stored):
                errors.append("Incorrect common {} password. Ask the admin for it.".format(role))
            if not team:
                errors.append("Captains must choose the team they manage.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("register.html", form=form, teams=teams(),
                                   roles=domain.SELF_REGISTER_ROLES, year=domain.current_year(),
                                   houses=domain.HOUSES, security_questions=domain.SECURITY_QUESTIONS)

        uid = db.next_id("sports_users", "U")
        pid = db.next_id("sports_participants", "R")
        cats = config()["categories"]
        by = int(birth_year) if role == "player" else None
        db.execute(
            'INSERT INTO sports_participants(id, name, team, division, birth_year, category, "user", '
            "username, password, roles, email, security_question, security_answer, must_change_pw, "
            "house, number, roster, volunteer, archived, sample) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,0,0,0)",
            (pid, name, team if role == "captain" else None,
             division if role == "player" else None, by,
             domain.category_for_birth_year(by, cats) if by else None,
             uid, username, security.hash_password(password), db.dumps([role]), email,
             sec_q, hash_answer(sec_a),
             house if role == "player" else None, number if role == "player" else None,
             1 if role == "player" else 0))
        audit_stamp("sports_participants", pid, created=True, actor=uid)

        if role == "player":
            notify_roles(["captain", "admin"], "new_player",
                         "New player '{}' ({}) registered and needs a team.".format(name, division),
                         url_for("participants_list"))
            log_activity("Player '{}' self-registered (awaiting team)".format(username))
        else:
            log_activity("New {} account '{}' self-registered".format(role, username))
        flash("Account created. You can log in now.", "success")
        return redirect(url_for("login"))
    return render_template("register.html", form=form, teams=teams(),
                           roles=domain.SELF_REGISTER_ROLES, year=domain.current_year(),
                           houses=domain.HOUSES, security_questions=domain.SECURITY_QUESTIONS)


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("login"))


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    u = current_user()

    def _render():
        acct = db.query_one("SELECT security_question FROM sports_users WHERE id=?", (u["id"],))
        return render_template(
            "change_password.html",
            forced=bool(u.get("must_change_pw")),
            security_questions=domain.SECURITY_QUESTIONS,
            current_sq=(acct or {}).get("security_question") or "",
        )

    if request.method == "POST":
        section = request.form.get("section", "password")

        if section == "security":
            sq = (request.form.get("security_question") or "").strip()
            sa = request.form.get("security_answer") or ""
            current = request.form.get("current") or ""
            errors, changed = apply_security_question(u, current, sq, sa)
            if errors:
                for e in errors:
                    flash(e, "danger")
                return _render()
            if changed:
                log_activity("{} updated their security question".format(u["name"]))
            flash("Security question updated." if changed else "No changes made.",
                  "success" if changed else "info")
            return redirect(url_for("change_password"))

        current = request.form.get("current") or ""
        new = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        row = db.query_one("SELECT password FROM sports_users WHERE id=?", (u["id"],))
        errors = []
        if not u.get("must_change_pw") and not security.verify_password(current, row["password"]):
            errors.append("Your current password is incorrect.")
        if new != confirm:
            errors.append("The new passwords don't match.")
        errors += ["Password: " + e for e in security.password_errors(new)]
        if errors:
            for e in errors:
                flash(e, "danger")
            return _render()
        set_account_password(u["id"], new, must_change=0)
        log_activity("{} changed their password".format(u["name"]))
        flash("Password updated.", "success")
        return redirect(url_for("dashboard"))
    return _render()


@app.route("/forgot", methods=["GET", "POST"])
def forgot_password():
    """Recover a password by answering the account's security question."""
    if current_user():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        acct = db.query_one("SELECT id, name, security_question FROM sports_users WHERE lower(username)=?",
                            (username.lower(),))
        # Step 1: look up the question.
        if request.form.get("step") == "lookup":
            if not acct or not acct.get("security_question"):
                flash("No account with a security question matches that username.", "danger")
                return render_template("forgot.html", step="lookup")
            return render_template("forgot.html", step="answer", username=username,
                                   question=acct["security_question"])
        # Step 2: verify the answer and set a new password.
        answer = request.form.get("security_answer") or ""
        new = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        if not acct:
            flash("No such account.", "danger")
            return render_template("forgot.html", step="lookup")
        errors = []
        if not verify_security_answer(acct["id"], answer):
            errors.append("That answer doesn't match. Try again.")
        if new != confirm:
            errors.append("The new passwords don't match.")
        errors += ["Password: " + e for e in security.password_errors(new)]
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("forgot.html", step="answer", username=username,
                                   question=acct["security_question"])
        set_account_password(acct["id"], new, must_change=0)
        log_activity("Password recovered via security question for '{}'".format(username))
        flash("Password reset. You can log in now.", "success")
        return redirect(url_for("login"))
    return render_template("forgot.html", step="lookup")


@app.route("/switch/<role>")
@login_required
def switch_role(role):
    u = current_user()
    if role in u["roles"]:
        session["active_role"] = role
        flash("Now acting as {}.".format(role), "info")
    return redirect(request.referrer or url_for("dashboard"))


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
@app.route("/")
@login_required
def dashboard():
    u = current_user()
    acting = u["acting"]
    _pid = (current_program() or {}).get("id")
    announcements = db.query(
        "SELECT * FROM sports_announcements WHERE visible=1 AND program_id=? ORDER BY id DESC LIMIT 10",
        (_pid,))

    # Player participation panel — shown when acting as a player. Players sign up
    # for sports (master); their results come from the matching SACs.
    player_data = None
    if acting == "player" and u["is_player"]:
        p = linked_participant(u)
        if p:
            # Registered events with details (the SAC for the player's age+gender).
            regs = db.query(
                "SELECT sac.*, s.name AS sport_name FROM sports_sport_age_categories sac "
                "JOIN sports_sports s ON s.id=sac.sport_id "
                "JOIN sports_signups su ON su.sport_id=sac.sport_id AND su.participant_id=? "
                "WHERE sac.archived=0 AND (sac.age_category IS NULL OR sac.age_category=?) "
                "AND (sac.gender IS NULL OR sac.gender=?) ORDER BY sac.date, sac.slot",
                (p["id"], p.get("category"), p.get("division")))
            regs = [decode_sac(r) for r in regs]
            results = db.query(
                "SELECT r.*, sp.name AS event_name FROM sports_results r "
                "JOIN sports_sport_age_categories sac ON sac.id=r.sac_id "
                "JOIN sports_sports sp ON sp.id=sac.sport_id "
                "WHERE r.participant=? ORDER BY sp.name", (p["id"],))
            score = sum(int(r["points"] or 0) for r in results)
            # Team total: individual results of teammates + team-event results (keyed by team id).
            team_score = 0
            if p.get("team"):
                r1 = db.query_one("SELECT COALESCE(SUM(r.points),0) AS s FROM sports_results r "
                                  "JOIN sports_participants pp ON pp.id=r.participant WHERE pp.team=?",
                                  (p["team"],))["s"]
                r2 = db.query_one("SELECT COALESCE(SUM(points),0) AS s FROM sports_results WHERE participant=?",
                                  (p["team"],))["s"]
                team_score = (r1 or 0) + (r2 or 0)
            player_data = {"p": p, "regs": regs, "sports_results": results, "score": score,
                           "team_score": team_score, "team_id": p.get("team"),
                           "standings": team_standings(),
                           "team_name": domain.team_name(p.get("team"), teams()),
                           "locked_ids": locked_sport_ids(p["id"])}

    staff = None
    if acting in ("captain", "admin"):
        # Week paging: ?week=<offset> in weeks from the current Monday-started week.
        try:
            week = int(request.args.get("week") or 0)
        except ValueError:
            week = 0
        today = date.today()
        start = today - timedelta(days=today.weekday()) + timedelta(weeks=week)
        end = start + timedelta(days=6)
        window = []
        for e in load_sacs("sac.archived=0"):
            d = _parse_date(e.get("date"))
            if d and start <= d <= end:
                window.append(e)
        window.sort(key=lambda e: (e.get("date") or "", e.get("slot") or ""))
        captain_hub = None
        if acting == "captain" and u.get("team"):
            my_team = u["team"]
            # Filter week view to events where my team has at least one sign-up.
            team_sport_ids = {r["sport_id"] for r in db.query(
                "SELECT DISTINCT su.sport_id FROM sports_signups su "
                "JOIN sports_participants p ON p.id=su.participant_id "
                "WHERE p.team=? AND p.archived=0", (my_team,))}
            window = [e for e in window if e.get("sport_id") in team_sport_ids]
            # Team points (individual members + team-keyed results).
            r1 = (db.query_one(
                "SELECT COALESCE(SUM(r.points),0) AS s FROM sports_results r "
                "JOIN sports_participants pp ON pp.id=r.participant WHERE pp.team=?",
                (my_team,)) or {}).get("s") or 0
            r2 = (db.query_one(
                "SELECT COALESCE(SUM(points),0) AS s FROM sports_results WHERE participant=?",
                (my_team,)) or {}).get("s") or 0
            all_standings = team_standings()
            my_rank = next((t["rank"] for t in all_standings if t["id"] == my_team), None)
            recent = db.query(
                "SELECT r.*, sp.name AS sport_name, sac.age_category, sac.gender "
                "FROM sports_results r "
                "JOIN sports_sport_age_categories sac ON sac.id=r.sac_id "
                "JOIN sports_sports sp ON sp.id=sac.sport_id "
                "LEFT JOIN sports_participants pp ON pp.id=r.participant "
                "WHERE pp.team=? OR r.participant=? "
                "ORDER BY r.id DESC LIMIT 6", (my_team, my_team))
            captain_hub = {
                "team": domain.team_name(my_team, teams()),
                "my_team": my_team,
                "points": r1 + r2,
                "rank": my_rank,
                "total_teams": len(all_standings),
                "standings": all_standings,
                "recent": recent,
            }
        staff = {
            "stats": {
                "sports_participants": db.count("sports_participants", "archived=0"),
                "sports_sports": db.count("sports_sports", "archived=0"),
                "volunteers": db.count("sports_participants", "volunteer=1 AND archived=0"),
                "unassigned": db.count("sports_participants", "team IS NULL AND archived=0"),
            },
            "window": window, "week": week, "start": start, "end": end,
            "prev_week": week - 1, "next_week": week + 1, "is_current": week == 0,
            "activity": db.query("SELECT * FROM sports_audit ORDER BY id DESC LIMIT 8"),
            "sports_sport_categories": sport_categories(),
            "captain_hub": captain_hub,
        }
    return render_template("dashboard.html", announcements=announcements,
                           player_data=player_data, staff=staff)


def _parse_date(s):
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _parse_slot_time(slot):
    """Best-effort HH:MM out of a free-text time slot like '09:30' or '9:30 AM'."""
    if not slot:
        return None
    head = slot.strip().split()[0] if slot.strip() else ""
    parts = head.split(":")
    if len(parts) < 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if 0 <= h <= 23 and 0 <= m <= 59:
        return datetime.min.time().replace(hour=h, minute=m)
    return None


def event_is_over(date_str, slot):
    """True if the event's scheduled date (and time, if parseable) is in the past.

    An undated event is never 'over'. A dated event with no usable time is over
    only after its whole day has passed (end-of-day cutoff)."""
    d = _parse_date(date_str)
    if not d:
        return False
    t = _parse_slot_time(slot) or datetime.min.time().replace(hour=23, minute=59)
    return datetime.combine(d, t) < datetime.now()


# --------------------------------------------------------------------------
# Player self-service (X04)
# --------------------------------------------------------------------------
@app.route("/profile/edit", methods=["GET", "POST"])
@login_required
def profile_edit():
    u = current_user()
    p = linked_participant(u)
    if not p:
        flash("Your account isn't linked to a roster entry yet.", "warning")
        return redirect(url_for("dashboard"))

    def _render(p):
        p["age"] = domain.age_from_birth_year(p.get("birth_year"))
        acct = db.query_one("SELECT security_question FROM sports_users WHERE id=?", (u["id"],))
        cap_names = [r["name"] for r in db.query(
            "SELECT name FROM sports_users WHERE team=? AND roles LIKE '%captain%' AND disabled=0",
            (p.get("team"),))] if p.get("team") else []
        captain_contact = ("Contact your captain ({})".format(", ".join(cap_names))
                            if cap_names else "Contact your captain or admin")
        return render_template(
            "profile_edit.html", p=p,
            security_questions=domain.SECURITY_QUESTIONS,
            current_sq=(acct or {}).get("security_question") or "",
            forced=bool(u.get("must_change_pw")),
            captain_contact=captain_contact,
        )

    if request.method == "POST":
        section = request.form.get("section", "profile")

        if section == "security":
            sq = (request.form.get("security_question") or "").strip()
            sa = request.form.get("security_answer") or ""
            current = request.form.get("current") or ""
            errors, changed = apply_security_question(u, current, sq, sa)
            if errors:
                for e in errors:
                    flash(e, "danger")
                return _render(p)
            if changed:
                log_activity("{} updated their security question".format(u["name"]))
            flash("Security question updated." if changed else "No changes made.",
                  "success" if changed else "info")
            return redirect(url_for("profile_edit"))

        if section == "password":
            cur = request.form.get("current") or ""
            new = request.form.get("password") or ""
            confirm = request.form.get("confirm") or ""
            row = db.query_one("SELECT password FROM sports_users WHERE id=?", (u["id"],))
            errors = []
            if not u.get("must_change_pw") and not security.verify_password(cur, row["password"]):
                errors.append("Current password is incorrect.")
            if new != confirm:
                errors.append("New passwords don't match.")
            errors += ["Password: " + e for e in security.password_errors(new)]
            if errors:
                for e in errors:
                    flash(e, "danger")
                return _render(p)
            set_account_password(u["id"], new, must_change=0)
            log_activity("{} updated their password".format(u["name"]))
            flash("Password updated.", "success")
            return redirect(url_for("profile_edit"))

    return _render(p)


@app.route("/profile/withdraw/<sport_id>", methods=["POST"])
@login_required
def profile_withdraw(sport_id):
    u = current_user()
    p = linked_participant(u)
    if not p:
        abort(403)
    locked = locked_sport_ids(p["id"])
    if sport_id in locked:
        flash("Cannot withdraw — a result has been recorded for this event.", "warning")
        return redirect(url_for("dashboard"))
    db.execute("DELETE FROM sports_signups WHERE participant_id=? AND sport_id=?",
               (p["id"], sport_id))
    flash("Withdrawn from event.", "success")
    return redirect(url_for("dashboard"))


# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------
@app.route("/notifications")
@login_required
def notifications():
    u = current_user()
    items = db.query("SELECT * FROM sports_notifications WHERE user_id=? ORDER BY id DESC LIMIT 100",
                     (u["id"],))
    my_types = [(t, lbl) for (t, lbl) in NOTIFY_TYPES if role_relevant(u["roles"], t)]
    return render_template("notifications.html", items=items, prefs=u["notify_prefs"],
                           notify_types=my_types)


@app.route("/notifications/read", methods=["POST"])
@login_required
def notifications_read():
    u = current_user()
    nid = request.form.get("id")
    if nid == "all":
        db.execute('UPDATE sports_notifications SET "read"=1 WHERE user_id=?', (u["id"],))
    else:
        db.execute('UPDATE sports_notifications SET "read"=1 WHERE user_id=? AND id=?', (u["id"], nid))
    return redirect(request.referrer or url_for("sports_notifications"))


@app.route("/notifications/settings", methods=["POST"])
@login_required
def notifications_settings():
    u = current_user()
    mute_all = bool(request.form.get("mute_all"))
    muted = [t for (t, _label) in NOTIFY_TYPES if not request.form.get("recv_" + t)]
    prefs = {"mute_all": mute_all, "muted": muted}
    update_account(u["id"], notify_prefs=db.dumps(prefs))
    flash("Notification preferences saved.", "success")
    return redirect(url_for("sports_notifications"))


# --------------------------------------------------------------------------
# Participants (staff)
# --------------------------------------------------------------------------
@app.route("/participants")
@roles_required("admin", "captain")
def participants_list():
    u = current_user()
    cats = config()["categories"]
    # Show archived participants too (dimmed, with an Unarchive action), like Sports.
    rows = db.query("SELECT * FROM sports_participants")

    q = (request.args.get("q") or "").strip().lower()
    f_team = request.args.get("team") or ""
    if not f_team and u["is_captain"] and not u["is_admin"]:
        f_team = u.get("team") or ""
    f_cat = request.args.get("category") or ""
    f_div = request.args.get("division") or ""
    f_event = request.args.get("event") or ""

    signups_by_pid = {}
    for s in db.query("SELECT participant_id, sport_id FROM sports_signups"):
        signups_by_pid.setdefault(s["participant_id"], []).append(s["sport_id"])
    # "In use" = has a recorded result (so deleting archives instead of removing).
    results_by_pid = {r["participant"]: r["n"] for r in
                      db.query("SELECT participant, COUNT(*) AS n FROM sports_results GROUP BY participant")}

    def matches(p):
        ev = signups_by_pid.get(p["id"], [])
        if q and q not in p["name"].lower() and q not in p["id"].lower():
            return False
        if f_team and (p.get("team") or "") != f_team:
            return False
        if f_cat and p.get("category") != f_cat:
            return False
        if f_div and p.get("division") != f_div:
            return False
        if f_event and f_event not in ev:
            return False
        return True

    items = [p for p in rows if matches(p)]
    for p in items:
        p["events"] = signups_by_pid.get(p["id"], [])
        p["age"] = domain.age_from_birth_year(p.get("birth_year"))
        p["n_results"] = results_by_pid.get(p["id"], 0)
        p["team_name"] = domain.team_name(p.get("team"), teams()) if p.get("team") else ""
        p["pending_team_name"] = (domain.team_name(p.get("pending_team"), teams())
                                  if p.get("pending_team") else "")

    # Captain: set awaiting flag for their pending members; show unassigned pool.
    my_team = u.get("team") if u["is_captain"] and not u["is_admin"] else None
    if my_team:
        for p in items:
            p["awaiting"] = (not p.get("team")) and p.get("pending_team") == my_team
    # Players still free to claim: no team, not requested, and not archived.
    # Show the unassigned pool from the full unfiltered list so captains can always claim.
    all_rows_simple = [p for p in rows]
    unassigned = [p for p in all_rows_simple if not p.get("team") and not p.get("pending_team")
                  and not p.get("archived")]
    pending_approvals = []  # admin-only: claims/new players awaiting a decision
    if not my_team:
        # Admin: surface everything pending approval at the top.
        pending_approvals = [p for p in items if p.get("pending_team") and not p.get("archived")]
        pending_approvals.sort(key=lambda p: p["id"])
    # Active rows first, archived dimmed at the bottom.
    items.sort(key=lambda p: (p.get("archived") or 0, p["id"]))
    unassigned.sort(key=lambda p: p["id"])
    return render_template(
        "participants.html", participants=items, unassigned=unassigned,
        pending_approvals=pending_approvals,
        teams=teams(), categories=cats, events=all_sports(),
        divisions=domain.DIVISIONS, my_team=my_team,
        filters={"q": q, "team": f_team, "category": f_cat, "division": f_div, "event": f_event})


def _can_edit_participant(u, p):
    if u["is_admin"]:
        return True
    return u["is_captain"] and p.get("team") == u.get("team")


@app.route("/participants/new", methods=["GET", "POST"])
@roles_required("admin", "captain")
def participant_new():
    u = current_user()
    cats = config()["categories"]
    if request.method == "POST":
        # Admin sets the team directly; a captain's new player is held pending approval.
        cap_pending = not u["is_admin"] and u["is_captain"]
        team = request.form.get("team") if u["is_admin"] else None
        by = request.form.get("birth_year")
        record = {
            "name": (request.form.get("name") or "").strip(),
            "team": team or None,
            "division": request.form.get("division"),
            "birth_year": int(by) if by else None,
        }
        record["category"] = domain.category_for_birth_year(record["birth_year"], cats)
        errors = _validate_participant(record, require_team=False if cap_pending else True)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("participant_form.html", p=record, teams=teams(),
                                   divisions=domain.DIVISIONS, new=True, year=domain.current_year())
        pid = db.next_id("sports_participants", "R")
        pend = u.get("team") if cap_pending else None
        db.execute(
            "INSERT INTO sports_participants(id, name, team, division, birth_year, category, "
            "volunteer, archived, pending_team, sample) VALUES(?,?,?,?,?,?,0,0,?,0)",
            (pid, record["name"], record["team"], record["division"],
             record["birth_year"], record["category"], pend))
        audit_stamp("sports_participants", pid, created=True)
        if cap_pending:
            tname = domain.team_name(pend, teams())
            log_activity("{} added participant {} ({}) for {} (awaiting admin)".format(
                _who(u), record["name"], pid, tname))
            notify_roles(["admin"], "roster",
                         "{} added new player '{}' for {} — needs your approval.".format(
                             u["name"], record["name"], tname),
                         url_for("participants_list"))
            flash("Player {} added — an admin will approve them onto your team.".format(
                record["name"]), "success")
        else:
            log_activity("{} added participant {} ({})".format(_who(u), record["name"], pid))
            flash("Participant {} added.".format(pid), "success")
        return redirect(url_for("participants_list"))
    blank = {"team": u.get("team") if (u["is_captain"] and not u["is_admin"]) else ""}
    return render_template("participant_form.html", p=blank, teams=teams(),
                           divisions=domain.DIVISIONS, new=True, year=domain.current_year())


@app.route("/participants/<pid>/edit", methods=["GET", "POST"])
@roles_required("admin", "captain")
def participant_edit(pid):
    u = current_user()
    cats = config()["categories"]
    p = db.query_one("SELECT * FROM sports_participants WHERE id=?", (pid,))
    if not p:
        abort(404)
    if not _can_edit_participant(u, p):
        abort(403)
    if request.method == "POST":
        by = request.form.get("birth_year")
        p["name"] = (request.form.get("name") or "").strip()
        if u["is_admin"]:
            p["team"] = request.form.get("team") or None
        p["division"] = request.form.get("division")
        p["birth_year"] = int(by) if by else None
        p["category"] = domain.category_for_birth_year(p["birth_year"], cats)
        errors = _validate_participant(p, require_team=False)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("participant_form.html", p=p, teams=teams(),
                                   divisions=domain.DIVISIONS, new=False, year=domain.current_year())
        db.execute("UPDATE sports_participants SET name=?, team=?, division=?, birth_year=?, "
                   "category=? WHERE id=?",
                   (p["name"], p["team"], p["division"], p["birth_year"], p["category"], pid))
        audit_stamp("sports_participants", pid)
        log_activity("{} edited participant {}".format(_who(u), pid))
        flash("Participant updated.", "success")
        return redirect(url_for("participants_list"))
    p["age"] = domain.age_from_birth_year(p.get("birth_year"))
    return render_template("participant_form.html", p=p, teams=teams(),
                           divisions=domain.DIVISIONS, new=False, year=domain.current_year())


@app.route("/participants/<pid>/assign", methods=["POST"])
@roles_required("admin", "captain")
def participant_assign(pid):
    u = current_user()
    p = db.query_one("SELECT * FROM sports_participants WHERE id=?", (pid,))
    if not p:
        abort(404)
    target = request.form.get("team") or None
    if u["is_admin"]:
        # Admin assignment is immediate, and clears any pending request.
        db.execute("UPDATE sports_participants SET team=?, pending_team=NULL WHERE id=?",
                   (target, pid))
        audit_stamp("sports_participants", pid)
        tname = domain.team_name(target, teams()) if target else "Unassigned"
        log_activity("{} moved {} to {}".format(_who(u), p["name"], tname))
        if p.get("user"):
            notify([p["user"]], "assignment",
                   "You have been added to {}.".format(tname) if target
                   else "You have been removed from your team.", url_for("dashboard"))
        flash("{} → {}.".format(p["name"], tname), "success")
        return redirect(request.referrer or url_for("participants_list"))

    # Captains: claiming a player needs admin approval; releasing own is immediate.
    if target == u.get("team"):
        if p.get("team") == u.get("team"):
            flash("{} is already on your team.".format(p["name"]), "warning")
            return redirect(request.referrer or url_for("participants_list"))
        db.execute("UPDATE sports_participants SET pending_team=? WHERE id=?", (u.get("team"), pid))
        audit_stamp("sports_participants", pid)
        tname = domain.team_name(u.get("team"), teams())
        log_activity("{} requested {} for {} (awaiting admin)".format(_who(u), p["name"], tname))
        notify_roles(["admin"], "roster",
                     "{} wants to add '{}' to {} — needs your approval.".format(
                         u["name"], p["name"], tname),
                     url_for("participants_list"))
        flash("Request sent — an admin will approve adding {} to your team.".format(p["name"]),
              "success")
        return redirect(request.referrer or url_for("participants_list"))
    if not target and p.get("team") == u.get("team"):
        db.execute("UPDATE sports_participants SET team=NULL WHERE id=?", (pid,))
        audit_stamp("sports_participants", pid)
        log_activity("{} removed {} from {}".format(
            _who(u), p["name"], domain.team_name(u.get("team"), teams())))
        if p.get("user"):
            notify([p["user"]], "assignment",
                   "You have been removed from your team.", url_for("dashboard"))
        flash("{} removed from your team.".format(p["name"]), "success")
        return redirect(request.referrer or url_for("participants_list"))
    abort(403)


@app.route("/participants/<pid>/roster/<action>", methods=["POST"])
@roles_required("admin")
def roster_decision(pid, action):
    u = current_user()
    p = db.query_one("SELECT * FROM sports_participants WHERE id=?", (pid,))
    if not p:
        abort(404)
    pend = p.get("pending_team")
    if not pend:
        flash("No pending team request for {}.".format(p["name"]), "warning")
        return redirect(request.referrer or url_for("participants_list"))
    tname = domain.team_name(pend, teams())
    cap_ids = [r["id"] for r in db.query(
        "SELECT id FROM sports_users WHERE team=? AND roles LIKE '%captain%' AND disabled=0", (pend,))]
    if action == "approve":
        db.execute("UPDATE sports_participants SET team=?, pending_team=NULL WHERE id=?", (pend, pid))
        audit_stamp("sports_participants", pid)
        log_activity("{} approved {} onto {}".format(_who(u), p["name"], tname))
        notify(cap_ids, "roster", "Approved: '{}' is now on {}.".format(p["name"], tname),
               url_for("participants_list"))
        if p.get("user"):
            notify([p["user"]], "assignment",
                   "You have been added to {}.".format(tname), url_for("dashboard"))
        flash("{} added to {}.".format(p["name"], tname), "success")
    elif action == "reject":
        db.execute("UPDATE sports_participants SET pending_team=NULL WHERE id=?", (pid,))
        audit_stamp("sports_participants", pid)
        log_activity("{} rejected {} for {}".format(_who(u), p["name"], tname))
        notify(cap_ids, "roster", "Rejected: '{}' was not added to {}.".format(p["name"], tname),
               url_for("participants_list"))
        flash("Request to add {} to {} rejected.".format(p["name"], tname), "success")
    else:
        abort(400)
    return redirect(request.referrer or url_for("participants_list"))


@app.route("/participants/<pid>/archive", methods=["POST"])
@roles_required("admin", "captain")
def participant_archive(pid):
    u = current_user()
    p = db.query_one("SELECT * FROM sports_participants WHERE id=?", (pid,))
    if not p:
        abort(404)
    if not _can_edit_participant(u, p):
        abort(403)
    newval = 0 if p["archived"] else 1
    db.execute("UPDATE sports_participants SET archived=? WHERE id=?", (newval, pid))
    audit_stamp("sports_participants", pid)
    verb = "archived" if newval else "unarchived"
    log_activity("{} {} participant {}".format(_who(u), verb, p["name"]))
    flash("{} {}.".format(p["name"], verb), "info")
    return redirect(request.referrer or url_for("participants_list"))


@app.route("/sports_participants/<pid>/delete", methods=["POST"])
@roles_required("admin", "captain")
def participant_delete(pid):
    u = current_user()
    p = db.query_one("SELECT * FROM sports_participants WHERE id=?", (pid,))
    if not p:
        abort(404)
    if not _can_edit_participant(u, p):
        abort(403)
    if db.count("sports_results", "participant=?", (pid,)) > 0:
        db.execute("UPDATE sports_participants SET archived=1 WHERE id=?", (pid,))
        audit_stamp("sports_participants", pid)
        flash("Participant has results — archived instead of deleted.", "warning")
    else:
        db.execute("DELETE FROM sports_participants WHERE id=?", (pid,))
        db.execute("DELETE FROM sports_signups WHERE participant_id=?", (pid,))
        flash("Participant removed.", "info")
    log_activity("{} removed participant {}".format(_who(u), pid))
    return redirect(url_for("participants_list"))


def _validate_participant(p, require_team=True):
    errors = []
    if not p.get("name"):
        errors.append("Full name is required.")
    if not p.get("division"):
        errors.append("Gender is required.")
    if not p.get("birth_year"):
        errors.append("Birth year is required.")
    return errors


# --------------------------------------------------------------------------
# Sports Sign-up (admin: all · captain: team · player: self)
# --------------------------------------------------------------------------
@app.route("/signup", methods=["GET", "POST"])
@login_required
def signup():
    u = current_user()
    # Behaviour follows the ACTIVE role, not roles held: a captain+player acting
    # as a player manages only their own sign-ups, not their team's.
    act = u["acting"]
    act_admin = act == "admin"
    act_captain = act == "captain"
    if act_admin:
        people = db.query("SELECT * FROM sports_participants WHERE archived=0 AND team IS NOT NULL ORDER BY name")
    elif act_captain:
        people = db.query("SELECT * FROM sports_participants WHERE archived=0 AND team=? ORDER BY name",
                          (u.get("team"),))
    else:  # player self
        people = db.query('SELECT * FROM sports_participants WHERE archived=0 AND "user"=?', (u["id"],))

    def allowed(p):
        if act_admin:
            return True
        if act_captain and p.get("team") == u.get("team"):
            return True
        return p.get("user") == u["id"]

    if request.method == "POST":
        pid = request.form.get("pid")
        sel = db.query_one("SELECT * FROM sports_participants WHERE id=?", (pid,))
        if not sel or not allowed(sel):
            abort(403)
        player_only = not (act_admin or act_captain)
        allowed_ids = eligible_sport_ids(sel, open_only=player_only)  # age+gender (+open) server-side
        current = set(participant_sport_ids(pid))
        # Accept newly-checked sports only if still eligible, but never silently drop an
        # already-registered sport just because it lost eligibility since sign-up — that's
        # only for the player to undo by unchecking it.
        chosen = {s for s in request.form.getlist("events") if s in allowed_ids or s in current}
        # A sport with a recorded result in a completed event can't be un-registered.
        locked = locked_sport_ids(pid)
        blocked = [sid for sid in (current - chosen) if sid in locked]
        final = chosen | set(blocked)
        db.execute("DELETE FROM sports_signups WHERE participant_id=?", (pid,))
        for sid in final:
            db.execute("INSERT OR IGNORE INTO sports_signups(participant_id, sport_id) VALUES(?,?)",
                       (pid, sid))
        # Locked sign-ups (have a recorded result) are silently kept — the locked
        # tick + tooltip already convey this, so no yellow warning is shown.
        log_activity("{} updated sport sign-ups for {}".format(_who(u), sel["name"]))
        flash("Sign-ups saved ({} sports).".format(len(final)), "success")
        return redirect(url_for("signup", pid=pid))

    pid = request.args.get("pid") or (people[0]["id"] if people else None)
    selected = db.query_one("SELECT * FROM sports_participants WHERE id=?", (pid,)) if pid else None
    if selected and not allowed(selected):
        abort(403)
    if selected:
        selected["age"] = domain.age_from_birth_year(selected.get("birth_year"))
    chosen = set(participant_sport_ids(pid)) if pid else set()
    # Only offer sports the participant's age category & gender are allowed for
    # (per the admin-defined Sports Events). A NULL age/gender on an event = wildcard.
    player_only = not (act_admin or act_captain)
    eligible_ids = eligible_sport_ids(selected, open_only=player_only) if selected else None
    locked = locked_sport_ids(pid) if pid else set()
    registered_grouped = []
    available_grouped = []
    sports = all_sports()
    cats = sport_categories()
    for c in cats:
        cat_sports = [s for s in sports if s.get("category_id") == c["id"]]
        reg_evs = [s for s in cat_sports if s["id"] in chosen or s["id"] in locked]
        reg_evs.sort(key=lambda s: s["name"])
        if reg_evs:
            registered_grouped.append({"cat": c, "events": reg_evs})
        avail_evs = [s for s in cat_sports if (eligible_ids is None or s["id"] in eligible_ids)
                     and s["id"] not in chosen and s["id"] not in locked]
        avail_evs.sort(key=lambda s: s["name"])
        if avail_evs:
            available_grouped.append({"cat": c, "events": avail_evs})
    return render_template("signup.html", people=people, selected=selected,
                           registered_grouped=registered_grouped, available_grouped=available_grouped,
                           chosen=chosen, locked=locked,
                           self_only=not (act_admin or act_captain),
                           teams=teams(), categories=config()["categories"],
                           divisions=domain.DIVISIONS, show_team_filter=act_admin)


# --------------------------------------------------------------------------
# Volunteers
# --------------------------------------------------------------------------
@app.route("/volunteers")
@roles_required("admin", "captain")
def volunteers():
    u = current_user()
    cats = config()["categories"]
    where, params = "archived=0", ()
    if u["is_captain"] and not u["is_admin"]:
        where += " AND team=?"
        params = (u.get("team"),)
    roster = db.query("SELECT * FROM sports_participants WHERE " + where + " ORDER BY name", params)
    for p in roster:
        p["age"] = domain.age_from_birth_year(p.get("birth_year"))

    q = (request.args.get("q") or "").strip().lower()
    f_team = request.args.get("team") or ""
    f_cat = request.args.get("category") or ""
    f_div = request.args.get("division") or ""

    def matches(p):
        if q and q not in p["name"].lower() and q not in p["id"].lower():
            return False
        if f_team and (p.get("team") or "") != f_team:
            return False
        if f_cat and p.get("category") != f_cat:
            return False
        if f_div and p.get("division") != f_div:
            return False
        return True

    filtered = [p for p in roster if matches(p)]
    vols = [p for p in roster if p["volunteer"]]
    can_edit = u["is_admin"] or u["is_captain"]
    return render_template("volunteers.html", roster=filtered, volunteers=vols,
                           teams=teams(), can_edit=can_edit, categories=cats,
                           divisions=domain.DIVISIONS, show_team_filter=u["is_admin"],
                           filters={"q": q, "team": f_team, "category": f_cat, "division": f_div})


@app.route("/volunteers/<pid>/toggle", methods=["POST"])
@roles_required("admin", "captain")
def volunteer_toggle(pid):
    u = current_user()
    p = db.query_one("SELECT * FROM sports_participants WHERE id=?", (pid,))
    if not p:
        abort(404)
    if not _can_edit_participant(u, p):
        abort(403)
    newval = 0 if p["volunteer"] else 1
    db.execute("UPDATE sports_participants SET volunteer=? WHERE id=?", (newval, pid))
    audit_stamp("sports_participants", pid)
    log_activity("{} {} {} as a volunteer".format(
        _who(u), "enrolled" if newval else "removed", p["name"]))
    flash("{} {} a volunteer.".format(p["name"], "is now" if newval else "is no longer"), "info")
    return redirect(url_for("volunteers"))


# --------------------------------------------------------------------------
# Sports — master catalogue (admin)
# --------------------------------------------------------------------------
@app.route("/admin/sports")
@roles_required("admin")
def sports_master():
    pid = (current_program() or {}).get("id")
    rows = db.query(
        "SELECT s.*, (SELECT COUNT(*) FROM sports_sport_age_categories sac WHERE sac.sport_id=s.id) "
        "AS n_sac FROM sports_sports s WHERE s.program_id=? ORDER BY s.archived, s.name", (pid,))
    for r in rows:
        r["category_name"] = domain.sport_category_name(r.get("category_id"), sport_categories())
        r["in_use"] = r["n_sac"] > 0
        # A parent can only be archived once all its child records are archived.
        r["active_children"] = db.count("sports_sport_age_categories",
                                        "sport_id=? AND archived=0", (r["id"],))
        r["can_archive"] = r["active_children"] == 0
        # Where is this sport used? (per-table counts, shown when "In use" is clicked)
        r["usage"] = {
            "Sport Age Categories": r["n_sac"],
            "Sign-ups": db.count("sports_signups", "sport_id=?", (r["id"],)),
            "Results": db.query_one(
                "SELECT COUNT(*) AS n FROM sports_results WHERE sac_id IN "
                "(SELECT id FROM sports_sport_age_categories WHERE sport_id=?)", (r["id"],))["n"],
        }
    return render_template("sports_master.html", sports=rows)


@app.route("/admin/sports/new", methods=["GET", "POST"])
@roles_required("admin")
def sport_new():
    return _sport_form(None)


@app.route("/admin/sports/<sid>/edit", methods=["GET", "POST"])
@roles_required("admin")
def sport_edit(sid):
    return _sport_form(sid)


def _sport_form(sid):
    u = current_user()
    s = get_sport(sid) if sid else None
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        category_id = request.form.get("category_id") or None
        errors = []
        if not name:
            errors.append("Sport name is required.")
        if db.query_one("SELECT id FROM sports_sports WHERE name=? AND id<>?", (name, s["id"] if s else "")):
            errors.append("A sport named '{}' already exists. Names must be unique.".format(name))
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("sport_form.html", s={**(s or {}), "name": name,
                                   "category_id": category_id}, sport_categories=sport_categories(),
                                   new=s is None)
        if s:
            db.execute("UPDATE sports_sports SET name=?, category_id=? WHERE id=?", (name, category_id, s["id"]))
            audit_stamp("sports_sports", s["id"])
            log_activity("{} edited sport '{}'".format(_who(u), name))
            flash("Sport updated.", "success")
        else:
            pid = (current_program() or {}).get("id")
            nid = db.next_id("sports_sports", "S")
            db.execute("INSERT INTO sports_sports(id, category_id, name, program_id, archived, sample) "
                       "VALUES(?,?,?,?,0,0)", (nid, category_id, name, pid))
            audit_stamp("sports_sports", nid, created=True)
            log_activity("{} added sport '{}'".format(_who(u), name))
            flash("Sport '{}' added.".format(name), "success")
        return redirect(url_for("sports_master"))
    return render_template("sport_form.html", s=s or {}, sport_categories=sport_categories(),
                           new=s is None)


@app.route("/admin/sports_sports/<sid>/delete", methods=["POST"])
@roles_required("admin")
def sport_delete(sid):
    s = get_sport(sid)
    if not s:
        abort(404)
    if sport_in_use(sid):
        # Has child events — let the admin archive / move / delete them first.
        return redirect(url_for("sport_manage", sid=sid))
    db.execute("DELETE FROM sports_sports WHERE id=?", (sid,))
    db.execute("DELETE FROM sports_signups WHERE sport_id=?", (sid,))
    log_activity("Admin deleted sport '{}'".format(s["name"]))
    flash("Sport deleted.", "info")
    return redirect(url_for("sports_master"))


@app.route("/admin/sports/<sid>/manage", methods=["GET", "POST"])
@roles_required("admin")
def sport_manage(sid):
    u = current_user()
    s = get_sport(sid)
    if not s:
        abort(404)
    if request.method == "POST":
        op = request.form.get("op")
        ids = request.form.getlist("child")  # selected SAC ids
        if op in ("move", "archive", "delete") and not ids:
            flash("Select at least one event first.", "warning")
            return redirect(url_for("sport_manage", sid=sid))
        if op == "move":
            target = request.form.get("target_sport") or None
            if not target:
                flash("Choose a sport to move the events to.", "danger")
                return redirect(url_for("sport_manage", sid=sid))
            for cid in ids:
                db.execute("UPDATE sports_sport_age_categories SET sport_id=? WHERE id=? AND sport_id=?",
                           (target, cid, sid))
                audit_stamp("sports_sport_age_categories", cid)
            log_activity("{} moved {} event(s) off '{}'".format(_who(u), len(ids), s["name"]))
            flash("Moved {} event(s).".format(len(ids)), "success")
        elif op == "archive":
            for cid in ids:
                db.execute("UPDATE sports_sport_age_categories SET archived=1 WHERE id=? AND sport_id=?",
                           (cid, sid))
                audit_stamp("sports_sport_age_categories", cid)
            flash("Archived {} event(s).".format(len(ids)), "success")
        elif op == "delete":
            hard = soft = 0
            for cid in ids:
                if db.count("sports_results", "sac_id=?", (cid,)) > 0:
                    db.execute("UPDATE sports_sport_age_categories SET archived=1 WHERE id=? AND sport_id=?",
                               (cid, sid))
                    soft += 1
                else:
                    db.execute("DELETE FROM sports_sport_age_categories WHERE id=? AND sport_id=?", (cid, sid))
                    db.execute("DELETE FROM sports_score_votes WHERE sac_id=?", (cid,))
                    hard += 1
            msg = "Deleted {} event(s).".format(hard)
            if soft:
                msg += " {} had results and were archived instead.".format(soft)
            flash(msg, "info")
        elif op == "delete_sport":
            if db.count("sports_sport_age_categories", "sport_id=?", (sid,)) > 0:
                flash("Resolve the remaining events before deleting the sport.", "danger")
                return redirect(url_for("sport_manage", sid=sid))
            db.execute("DELETE FROM sports_sports WHERE id=?", (sid,))
            db.execute("DELETE FROM sports_signups WHERE sport_id=?", (sid,))
            log_activity("{} deleted sport '{}' and its events".format(_who(u), s["name"]))
            flash("Sport '{}' deleted.".format(s["name"]), "info")
            return redirect(url_for("sports_master"))
        elif op == "archive_sport":
            db.execute("UPDATE sports_sport_age_categories SET archived=1 WHERE sport_id=?", (sid,))
            db.execute("UPDATE sports_sports SET archived=1 WHERE id=?", (sid,))
            audit_stamp("sports_sports", sid)
            flash("Sport '{}' and its events archived.".format(s["name"]), "info")
            return redirect(url_for("sports_master"))
        return redirect(url_for("sport_manage", sid=sid))
    sacs = db.query("SELECT * FROM sports_sport_age_categories WHERE sport_id=? ORDER BY archived, "
                    "age_category, gender", (sid,))
    for sac in sacs:
        sac["n_results"] = db.count("sports_results", "sac_id=?", (sac["id"],))
        sac["status_label"] = domain.STATUS_META.get(sac.get("status"), (sac.get("status"), ""))[0]
    other_sports = [o for o in all_sports(include_archived=True) if o["id"] != sid]
    return render_template("sport_manage.html", s=s, sacs=sacs, other_sports=other_sports)


@app.route("/admin/sports/<sid>/archive", methods=["POST"])
@roles_required("admin")
def sport_archive(sid):
    s = get_sport(sid)
    if not s:
        abort(404)
    newval = 0 if s["archived"] else 1
    # Archiving/unarchiving a sport cascades to its Sport Event (SAC) rows, so the
    # action always works (no need to archive each child by hand first).
    n = db.count("sports_sport_age_categories", "sport_id=? AND archived=?", (sid, 1 - newval))
    db.execute("UPDATE sports_sport_age_categories SET archived=? WHERE sport_id=?", (newval, sid))
    db.execute("UPDATE sports_sports SET archived=? WHERE id=?", (newval, sid))
    audit_stamp("sports_sports", sid)
    verb = "archived" if newval else "unarchived"
    log_activity("{} {} sport '{}'{}".format(_who(current_user()), verb, s["name"],
                 " (+{} event row(s))".format(n) if n else ""))
    flash("Sport {}{}.".format(verb,
          " — also {} {} Sport Event row(s)".format(verb, n) if n else ""), "info")
    return redirect(url_for("sports_master"))


# --------------------------------------------------------------------------
# Sport-Age-Categories — the scored/scheduled hub (admin)
# --------------------------------------------------------------------------
@app.route("/sac")
@roles_required("admin")
def sac_list():
    f_cat = request.args.get("category") or ""      # sport category
    f_age = request.args.get("age") or ""
    f_gender = request.args.get("gender") or ""
    f_status = request.args.get("status") or ""
    f_sport = request.args.get("sport") or ""

    def match(e):
        if f_cat and (e.get("category_id") or "") != f_cat:
            return False
        if f_age and (e.get("age_category") or "") != f_age:
            return False
        if f_gender and (e.get("gender") or "") != f_gender:
            return False
        if f_status and e.get("status") != f_status:
            return False
        if f_sport and (e.get("sport_id") or "") != f_sport:
            return False
        return True

    sacs = [e for e in load_sacs() if match(e)]
    sacs.sort(key=lambda x: (x.get("sport_name") or "", x.get("age_category") or "",
                             x.get("gender") or ""))
    return render_template("sport_age_categories.html", sacs=sacs,
                           sport_categories=sport_categories(), categories=config()["categories"],
                           divisions=domain.DIVISIONS, statuses=domain.EVENT_STATUSES,
                           sports=all_sports(),
                           filters={"category": f_cat, "age": f_age, "gender": f_gender,
                                    "status": f_status, "sport": f_sport})


@app.route("/sac/new", methods=["GET", "POST"])
@roles_required("admin")
def sac_new():
    u = current_user()
    if request.method == "POST":
        sport_id = request.form.get("sport_id")
        age_category = request.form.get("age_category") or None
        gender = request.form.get("gender") or None
        s = get_sport(sport_id)
        if not s:
            flash("Pick a sport.", "danger")
            return redirect(url_for("sac_new"))
        event_format = request.form.get("event_format") or "individual"
        if event_format not in domain.EVENT_FORMATS:
            event_format = "individual"
        # A sport may have several formats for the same age+gender (e.g. badminton
        # singles AND doubles); only an exact sport+age+gender+format repeat is a dupe.
        if db.query_one("SELECT id FROM sports_sport_age_categories WHERE sport_id=? AND age_category IS ? "
                        "AND gender IS ? AND event_format=?",
                        (sport_id, age_category, gender, event_format)):
            flash("That sport + age + gender + format combination already exists.", "danger")
            return redirect(url_for("sac_new"))
        points, places = domain.default_points_for(event_format, "placement")
        nid = db.next_id("sports_sport_age_categories", "SAC")
        db.execute("INSERT INTO sports_sport_age_categories(id, sport_id, age_category, gender, scoring_mode, "
                   "event_format, rounds, points, places, status, sample) "
                   "VALUES(?,?,?,?,?,?,?,?,?,'new',0)",
                   (nid, sport_id, age_category, gender, "placement", event_format, 3,
                    db.dumps(points), places))
        audit_stamp("sports_sport_age_categories", nid, created=True)
        log_activity("{} added sport-age-category {}".format(_who(u), sac_label(get_sac(nid))))
        flash("Added {}.".format(sac_label(get_sac(nid))), "success")
        return redirect(url_for("sac_list"))
    return render_template("sac_new.html", sports=all_sports(),
                           categories=config()["categories"], divisions=domain.DIVISIONS,
                           event_formats=domain.EVENT_FORMATS,
                           format_labels=domain.EVENT_FORMAT_LABELS)


def sac_in_use(sid):
    """A sport-age-category is 'in use' once any result has been recorded for it."""
    return db.count("sports_results", "sac_id=?", (sid,)) > 0


@app.route("/sac/<sid>/edit", methods=["GET", "POST"])
@roles_required("admin")
def sac_edit(sid):
    u = current_user()
    sac = get_sac(sid)
    if not sac:
        abort(404)
    if request.method == "POST":
        sport_id = request.form.get("sport_id") or sac["sport_id"]
        age_category = request.form.get("age_category") or None
        gender = request.form.get("gender") or None
        points, places = domain.parse_points_string(request.form.get("points") or "")
        event_format = request.form.get("event_format") or sac.get("event_format") or "individual"
        if event_format not in domain.EVENT_FORMATS:
            event_format = "individual"
        dupe = db.query_one("SELECT id FROM sports_sport_age_categories WHERE sport_id=? AND "
                            "age_category IS ? AND gender IS ? AND event_format=? AND id<>?",
                            (sport_id, age_category, gender, event_format, sid))
        if dupe:
            flash("That sport + age + gender + format combination already exists.", "danger")
            return redirect(url_for("sac_edit", sid=sid))
        new_date = (request.form.get("date") or "").strip()
        new_slot = (request.form.get("slot") or "").strip()
        new_status = request.form.get("status") or sac.get("status") or "new"
        # An event whose scheduled date/time has already passed can't be marked
        # 'New' or 'Open to register' — that status would contradict the clock.
        if new_status in ("new", "registration_open") and event_is_over(new_date, new_slot):
            flash("This event's scheduled time has already passed — it can't be set to "
                  "'New' or 'Open to register'. Pick a later date/time or a status like "
                  "'Completed' or 'In progress'.", "danger")
            return redirect(url_for("sac_edit", sid=sid))
        db.execute("UPDATE sports_sport_age_categories SET sport_id=?, age_category=?, gender=?, "
                   "scoring_mode=?, event_format=?, rounds=?, points=?, places=?, date=?, slot=?, "
                   "location=?, status=? WHERE id=?",
                   (sport_id, age_category, gender,
                    request.form.get("scoring_mode") or "placement", event_format,
                    int(request.form.get("rounds") or 3), db.dumps(points), places,
                    new_date, new_slot,
                    (request.form.get("location") or "").strip(),
                    new_status, sid))
        domain.recompute_sac_places(get_sac(sid))
        audit_stamp("sports_sport_age_categories", sid)
        log_activity("{} edited {}".format(_who(u), sac_label(get_sac(sid))))
        flash("Saved.", "success")
        return redirect(url_for("sac_list"))
    return render_template("sac_edit.html", sac=sac, sports=all_sports(),
                           categories=config()["categories"], divisions=domain.DIVISIONS,
                           modes=domain.SCORING_MODES, scoring_help=domain.SCORING_MODE_HELP,
                           statuses=domain.EVENT_STATUSES,
                           event_formats=domain.EVENT_FORMATS,
                           format_labels=domain.EVENT_FORMAT_LABELS)



@app.route("/sac/<sid>/delete", methods=["POST"])
@roles_required("admin")
def sac_delete(sid):
    sac = get_sac(sid)
    if not sac:
        abort(404)
    if sac_in_use(sid):
        db.execute("UPDATE sports_sport_age_categories SET archived=1 WHERE id=?", (sid,))
        audit_stamp("sports_sport_age_categories", sid)
        log_activity("Admin archived {} (has results)".format(sac_label(sac)))
        flash("Has results — archived instead of deleted.", "warning")
    else:
        db.execute("DELETE FROM sports_sport_age_categories WHERE id=?", (sid,))
        db.execute("DELETE FROM sports_score_votes WHERE sac_id=?", (sid,))
        log_activity("Admin deleted {}".format(sac_label(sac)))
        flash("Removed.", "info")
    return redirect(url_for("sac_list"))


@app.route("/sac/<sid>/archive", methods=["POST"])
@roles_required("admin")
def sac_archive(sid):
    sac = get_sac(sid)
    if not sac:
        abort(404)
    newval = 0 if sac["archived"] else 1
    db.execute("UPDATE sports_sport_age_categories SET archived=? WHERE id=?", (newval, sid))
    audit_stamp("sports_sport_age_categories", sid)
    flash("{} {}.".format(sac_label(sac), "archived" if newval else "unarchived"), "info")
    return redirect(url_for("sac_list"))


@app.route("/sports-status")
@login_required
def sports_status():
    """Weekly calendar of what's on (read-only, visible to all roles).

    Two views: 'all' (the week calendar, with an Unscheduled row + player
    colour key) and 'mine' (a player's signed-up vs. can-sign-up columns)."""
    try:
        week = int(request.args.get("week") or 0)
    except ValueError:
        week = 0
    today = date.today()

    # Optional custom date-range view ("calendar" jump): overrides the week
    # paging below when a From date is supplied.
    range_from = request.args.get("from") or ""
    range_to = request.args.get("to") or ""
    use_range = False
    if range_from:
        try:
            d_from = date.fromisoformat(range_from)
            d_to = date.fromisoformat(range_to) if range_to else d_from
            if d_to < d_from:
                d_from, d_to = d_to, d_from
            if (d_to - d_from).days > 60:  # cap the range so the page stays sane
                d_to = d_from + timedelta(days=60)
            use_range = True
        except ValueError:
            use_range = False

    if use_range:
        start = d_from
        days = [d_from + timedelta(days=i) for i in range((d_to - d_from).days + 1)]
    else:
        start = today - timedelta(days=today.weekday()) + timedelta(weeks=week)  # Monday
        days = [start + timedelta(days=i) for i in range(7)]

    u = current_user()
    me = linked_participant(u) if (u and u["is_player"]) else None
    my_sports = set(participant_sport_ids(me["id"])) if me else set()
    # "Team events" tab uses the viewer's team: a player's own team, or (for a
    # captain) the team they manage. Players & captains get the same read-only view.
    my_team = (me.get("team") if me else None) or \
              (u.get("team") if (u and u["is_captain"]) else None)
    # Captains land on the All-events view (team-filtered); Mine/Team tabs are
    # player-oriented and add noise when managing a whole team.
    if u and u.get("acting") == "captain":
        has_mine = False
        has_team = False
    else:
        has_mine = bool(me)
        has_team = bool(my_team)
    default_view = "mine" if has_mine else ("team" if has_team else "all")
    view = request.args.get("view") or default_view
    common = dict(sac_label=sac_label, has_mine=has_mine, has_team=has_team)

    # Filters (shared by the All-events and Team-events views).
    f_age = request.args.get("age") or ""
    f_gender = request.args.get("gender") or ""
    f_sched = request.args.get("sched") or ""   # "", "scheduled", "unscheduled"
    f_team = request.args.get("team") or ""
    if not f_team and u and u["is_captain"] and not u["is_admin"]:
        f_team = u.get("team") or ""

    # SAC IDs that have at least one participant from the selected team signed up.
    _team_sacs = None
    if f_team:
        _team_sacs = {r["sac_id"] for r in db.query(
            "SELECT DISTINCT sac.id AS sac_id FROM sports_sport_age_categories sac "
            "JOIN sports_signups su ON su.sport_id=sac.sport_id "
            "JOIN sports_participants p ON p.id=su.participant_id "
            "WHERE p.team=? AND p.archived=0 AND sac.archived=0", (f_team,))}

    def passes(e):
        if f_age and (e.get("age_category") or "") != f_age:
            return False
        if f_gender and (e.get("gender") or "") != f_gender:
            return False
        if f_sched == "scheduled" and not e.get("date"):
            return False
        if f_sched == "unscheduled" and e.get("date"):
            return False
        if _team_sacs is not None and e["id"] not in _team_sacs:
            return False
        return True

    def eligible(e):
        return bool(me) and (e.get("age_category") in (None, me.get("category"))) and \
               (e.get("gender") in (None, me.get("division")))

    def viewer_class(e):
        if not eligible(e):
            return ""
        return "reg" if e.get("sport_id") in my_sports else "elig"

    all_events = load_sacs("sac.archived=0")

    # ---- My Events view: two columns of the player's events ----------------
    if view == "mine" and me:
        signed, can_sign = [], []
        for e in all_events:
            if not eligible(e):
                continue
            if e.get("sport_id") in my_sports:
                signed.append(e)
            elif e.get("status") == "registration_open":
                can_sign.append(e)
        keyf = lambda e: (e.get("date") or "9999", e.get("slot") or "~", e.get("sport_name") or "")
        signed.sort(key=keyf)
        can_sign.sort(key=keyf)
        return render_template("sports_status.html", view="mine", signed=signed,
                               can_sign=can_sign, **common)

    # ---- Team Events view: events any teammate is signed up for ------------
    if view == "team" and my_team:
        rows = db.query(
            "SELECT sac.id AS sac_id, p.name AS pname FROM sports_sport_age_categories sac "
            "JOIN sports_signups su ON su.sport_id=sac.sport_id "
            "JOIN sports_participants p ON p.id=su.participant_id "
            "WHERE sac.archived=0 AND p.team=? AND p.archived=0 "
            "AND (sac.age_category IS NULL OR sac.age_category=p.category) "
            "AND (sac.gender IS NULL OR sac.gender=p.division)", (my_team,))
        members_by_sac = {}
        for r in rows:
            members_by_sac.setdefault(r["sac_id"], []).append(r["pname"])
        team_rows = []
        for e in all_events:
            mem = members_by_sac.get(e["id"])
            if mem and passes(e):
                e2 = dict(e)
                e2["members"] = sorted(mem)
                team_rows.append(e2)
        team_rows.sort(key=lambda e: (e.get("date") or "9999", e.get("slot") or "~",
                                      e.get("sport_name") or ""))
        return render_template("sports_status.html", view="team", team_rows=team_rows,
                               team_label=domain.team_name(my_team, teams()),
                               categories=config()["categories"], divisions=domain.DIVISIONS,
                               filters={"age": f_age, "gender": f_gender, "sched": f_sched},
                               **common)

    # ---- All events view: filters + week calendar + Unscheduled row --------
    # Filters always default to "All" (blank); the user picks to narrow.
    shown = [e for e in all_events if passes(e)]
    for e in shown:
        e["viewer"] = viewer_class(e)

    by_day = {d.isoformat(): [] for d in days}
    unscheduled = []
    for e in shown:
        if e.get("date") in by_day:
            by_day[e["date"]].append(e)
        elif not e.get("date"):
            unscheduled.append(e)
    for k in by_day:
        by_day[k].sort(key=lambda e: (e.get("slot") or "~", e.get("sport_name") or ""))
    unscheduled.sort(key=lambda e: (e.get("sport_name") or "", e.get("age_category") or ""))
    cal = [{"date": d.isoformat(), "label": d.strftime("%a"), "day": d.day,
            "is_today": d == today, "events": by_day[d.isoformat()]} for d in days]
    return render_template("sports_status.html", view="all", cal=cal, start=start, end=days[-1],
                           week=week, prev_week=week - 1, next_week=week + 1,
                           is_current=(week == 0), show_key=bool(me),
                           unscheduled=unscheduled, use_range=use_range,
                           categories=config()["categories"], divisions=domain.DIVISIONS,
                           teams_list=teams(),
                           filters={"age": f_age, "gender": f_gender, "sched": f_sched,
                                    "team": f_team,
                                    "date_from": days[0].isoformat(), "date_to": days[-1].isoformat()},
                           **common)


@app.route("/callsheet/<sid>")
@login_required
def callsheet(sid):
    sac = get_sac(sid)
    if not sac:
        abort(404)
    cats = config()["categories"]
    enrolled = eligible_participants(sac)
    return render_template("callsheet.html", e=sac, participants=enrolled, categories=cats,
                           teams=teams(), cat_name=lambda c: domain.category_name(c, cats),
                           team_name=lambda t: domain.team_name(t, teams()),
                           sport_cat_name=lambda c: domain.sport_category_name(c, sport_categories()))


# --------------------------------------------------------------------------
# Result entry (captains + admin)
# --------------------------------------------------------------------------
def captain_team_in_sac(team, sac):
    """True if the given team has an eligible participant in this SAC."""
    if not team:
        return False
    return any(p.get("team") == team for p in eligible_participants(sac))


def can_score(u, sac):
    """Admin, or a captain whose team is involved in this SAC."""
    return u["is_admin"] or (u["is_captain"] and captain_team_in_sac(u.get("team"), sac))


@app.route("/score")
@roles_required("captain", "admin")
def score_index():
    u = current_user()
    today = date.today().isoformat()
    sacs = load_sacs("sac.archived=0")
    if u["is_captain"] and not u["is_admin"]:
        sacs = [e for e in sacs if captain_team_in_sac(u.get("team"), e)]

    # Cascade filters: Gender -> Age -> Sport Category -> Sport. Combos drive the
    # dropdown options (built from the role-scoped set, before filtering).
    combos = [{"gender": e.get("gender") or "", "age": e.get("age_category") or "",
               "cat": e.get("category_id") or "", "sport": e.get("sport_id") or ""}
              for e in sacs]
    f_gender = request.args.get("gender") or ""
    f_age = request.args.get("age") or ""
    f_cat = request.args.get("category") or ""
    f_sport = request.args.get("sport") or ""
    sacs = [e for e in sacs
            if (not f_gender or (e.get("gender") or "") == f_gender)
            and (not f_age or (e.get("age_category") or "") == f_age)
            and (not f_cat or (e.get("category_id") or "") == f_cat)
            and (not f_sport or (e.get("sport_id") or "") == f_sport)]

    show_all = request.args.get("all")
    if not show_all:
        sacs = [e for e in sacs if (e.get("date") or "") <= today]
    sacs.sort(key=lambda e: (e.get("date") or "~", e.get("slot") or "~", e.get("sport_name", "")))
    buckets = {"todo": [], "pending": [], "done": []}
    for e in sacs:
        st = e.get("approval_status")
        if st == "approved":
            buckets["done"].append(e)
        elif st in ("pending", "disputed"):
            buckets["pending"].append(e)
        else:
            buckets["todo"].append(e)
    hidden = db.count("sports_sport_age_categories", "date>?", (today,)) if not show_all else 0
    return render_template("score_index.html", buckets=buckets, show_all=show_all, hidden=hidden,
                           sac_label=sac_label, combos=combos,
                           sport_categories=sport_categories(), sports=all_sports(),
                           categories=config()["categories"], divisions=domain.DIVISIONS,
                           filters={"gender": f_gender, "age": f_age, "category": f_cat,
                                    "sport": f_sport})


@app.route("/score/<sid>", methods=["GET", "POST"])
@roles_required("captain", "admin")
def score_event(sid):
    u = current_user()
    sac = get_sac(sid)
    if not sac:
        abort(404)
    if not can_score(u, sac):
        abort(403)
    is_team = domain.is_team_format(sac.get("event_format"))
    # Results may only be entered once the event has happened (date today or earlier).
    # Applies to captains AND admins; undated/future events can't be scored yet.
    can_enter = bool(sac.get("date")) and sac["date"] <= date.today().isoformat()
    rows = db.query("SELECT * FROM sports_results WHERE sac_id=?", (sid,))
    res_by_key = {r["participant"]: decode_result(r) for r in rows}
    if is_team:
        elig_teams = eligible_teams(sac)
        if request.method == "POST":
            # Line-ups can be organised any time — even before the event date.
            if request.form.get("action") == "lineup":
                err = _save_team_lineups(sac, elig_teams, u)
                flash(err or "Line-ups saved.", "danger" if err else "success")
                return redirect(url_for("score_event", sid=sid))
            if not can_enter:
                flash("You can enter results once the event date has arrived.", "danger")
                return redirect(url_for("score_event", sid=sid))
            if sac.get("finalised") and not u["is_admin"]:
                abort(403)
            _save_team_scores(sac, elig_teams, u)
            flash("Scores saved.", "success")
            return redirect(url_for("score_event", sid=sid))
        return render_template("score_event.html", e=sac, is_team=True, can_enter=can_enter,
                               elig_teams=elig_teams, results=res_by_key,
                               can_edit_lineup=lambda t: _can_edit_lineup(u, t),
                               teams=teams(), team_name=lambda t: domain.team_name(t, teams()),
                               sac_label=sac_label,
                               sport_cat_name=lambda c: domain.sport_category_name(c, sport_categories()))

    enrolled = eligible_participants(sac)
    if request.method == "POST":
        if not can_enter:
            flash("You can enter results once the event date has arrived.", "danger")
            return redirect(url_for("score_event", sid=sid))
        if sac.get("finalised") and not u["is_admin"]:
            abort(403)
        _save_scores(sac, enrolled, u)
        flash("Scores saved.", "success")
        return redirect(url_for("score_event", sid=sid))
    return render_template("score_event.html", e=sac, is_team=False, can_enter=can_enter,
                           participants=enrolled, results=res_by_key,
                           teams=teams(), team_name=lambda t: domain.team_name(t, teams()),
                           sac_label=sac_label,
                           sport_cat_name=lambda c: domain.sport_category_name(c, sport_categories()))


def _can_edit_lineup(u, team_id):
    """Only a team's own captain (or any admin) may set that team's line-up."""
    return u["is_admin"] or (u["is_captain"] and u.get("team") == team_id)


def _save_team_lineups(sac, elig_teams, u):
    """Persist the captain-chosen competing line-up per team. Doubles must field
    exactly two (or none). Returns an error string on a bad selection, else None."""
    is_doubles = sac.get("event_format") == "doubles"
    pending = []
    for row in elig_teams:
        tid = row["team"]["id"]
        if not _can_edit_lineup(u, tid):
            continue
        valid = {p["id"] for p in row["players"]}
        sel = [pid for pid in request.form.getlist("lineup_{}".format(tid)) if pid in valid]
        if is_doubles and len(sel) not in (0, 2):
            return "Doubles line-ups need exactly two players per team (or none)."
        pending.append((tid, sel))
    for tid, sel in pending:
        db.execute("INSERT INTO sports_event_lineups(sac_id, team, members) VALUES(?,?,?) "
                   "ON CONFLICT(sac_id, team) DO UPDATE SET members=excluded.members",
                   (sac["id"], tid, db.dumps(sel)))
    log_activity("{} set line-ups for {}".format(_who(u), sac_label(sac)))
    return None


def _save_team_scores(sac, elig_teams, u):
    """Team/doubles: one result row per team (results.participant holds the team id)."""
    sid = sac["id"]
    for row in elig_teams:
        tid = row["team"]["id"]
        place = request.form.get("t_{}_place".format(tid))
        place_v = int(place) if place not in (None, "", "0") else None
        r = db.query_one("SELECT * FROM sports_results WHERE sac_id=? AND participant=?", (sid, tid))
        if r:
            db.execute("UPDATE sports_results SET place=?, participated=1 WHERE id=?", (place_v, r["id"]))
        else:
            db.execute("INSERT INTO sports_results(sac_id, participant, place, participated, history) "
                       "VALUES(?,?,?,1,?)", (sid, tid, place_v, db.dumps([])))
    domain.recompute_sac_places(sac)
    log_activity("{} recorded team results for {}".format(_who(u), sac_label(sac)))


def _save_scores(sac, enrolled, u):
    mode = sac.get("scoring_mode", "placement")
    sid = sac["id"]
    for p in enrolled:
        pid = p["id"]
        r = db.query_one("SELECT * FROM sports_results WHERE sac_id=? AND participant=?", (sid, pid))
        if mode == "measured":
            rounds = []
            for i in range(1, int(sac.get("rounds", 3)) + 1):
                val = request.form.get("p_{}_r{}".format(pid, i))
                rounds.append(float(val) if val not in (None, "") else None)
            if r:
                db.execute("UPDATE sports_results SET rounds=? WHERE id=?", (db.dumps(rounds), r["id"]))
            else:
                db.execute("INSERT INTO sports_results(sac_id, participant, rounds, history) "
                           "VALUES(?,?,?,?)", (sid, pid, db.dumps(rounds), db.dumps([])))
        else:
            place = request.form.get("p_{}_place".format(pid))
            place_v = int(place) if place not in (None, "", "0") else None
            part = 1 if request.form.get("p_{}_part".format(pid)) else 0
            if r:
                db.execute("UPDATE sports_results SET place=?, participated=? WHERE id=?",
                           (place_v, part, r["id"]))
            else:
                db.execute("INSERT INTO sports_results(sac_id, participant, place, participated, history) "
                           "VALUES(?,?,?,?,?)", (sid, pid, place_v, part, db.dumps([])))
    domain.recompute_sac_places(sac)
    log_activity("{} recorded results for {}".format(_who(u), sac_label(sac)))


# --------------------------------------------------------------------------
# Score approval workflow (keyed to the SAC)
# --------------------------------------------------------------------------
def involved_captains(sid):
    """Captain user-ids whose team is involved in this SAC. Works for individual
    events (results.participant is a participant id whose team we look up) and for
    team/doubles events (results.participant is the team id itself)."""
    rows = db.query(
        "SELECT DISTINCT u.id FROM sports_users u "
        "WHERE u.roles LIKE '%captain%' AND u.disabled=0 AND u.team IS NOT NULL AND ("
        "  u.team IN (SELECT p.team FROM sports_participants p JOIN sports_results r ON r.participant=p.id "
        "             WHERE r.sac_id=?)"
        "  OR u.team IN (SELECT r.participant FROM sports_results r WHERE r.sac_id=?))", (sid, sid))
    return [r["id"] for r in rows]


def _notify_admins(ntype, msg, link=""):
    ids = [r["id"] for r in db.query("SELECT id FROM sports_users WHERE roles LIKE '%admin%' AND disabled=0")]
    notify(ids, ntype, msg, link)


def _approve_event(sac, actor):
    db.execute("UPDATE sports_sport_age_categories SET approval_status='approved', finalised=1, "
               "status='completed' WHERE id=?", (sac["id"],))
    targets = set(involved_captains(sac["id"]))
    notify(list(targets), "approval", "Results for '{}' are published.".format(sac_label(sac)),
           url_for("result_detail", sid=sac["id"]))
    # Tell the players who competed (individual events) their results are out.
    puids = [r["user"] for r in db.query(
        'SELECT DISTINCT p."user" FROM sports_participants p JOIN sports_results r ON r.participant=p.id '
        'WHERE r.sac_id=? AND p."user" IS NOT NULL', (sac["id"],))]
    notify(puids, "approval", "Results for '{}' are published.".format(sac_label(sac)),
           url_for("result_detail", sid=sac["id"]))
    log_activity("{} — '{}' results published".format(_who(actor), sac_label(sac)))


def submit_for_approval(sac, actor):
    """A captain enters scores and auto-agrees. Other involved captains must agree
    (all agree -> published; a disagreement -> admin). If NO other captain is
    involved, the admin decides (it is not auto-published). Returns 'captains' or
    'admin' to say who it now sits with."""
    sid = sac["id"]
    caps = involved_captains(sid)
    others = [c for c in caps if c != actor["id"]]
    db.execute("DELETE FROM sports_score_votes WHERE sac_id=?", (sid,))
    ts = db.now_ts()
    # Record the enterer's own agreement either way.
    db.execute("INSERT INTO sports_score_votes(sac_id, captain_id, decision, ts) VALUES(?,?,?,?)",
               (sid, actor["id"], "agree", ts))
    db.execute("UPDATE sports_sport_age_categories SET approval_status='pending', finalised=0 WHERE id=?", (sid,))
    if not others:
        _notify_admins("admin", "Results for '{}' need your approval (no other captain "
                       "involved).".format(sac_label(sac)), url_for("approvals"))
        log_activity("{} entered '{}' results — awaiting admin (no other captain)".format(
            _who(actor), sac_label(sac)))
        return "admin"
    for cid in others:
        db.execute("INSERT INTO sports_score_votes(sac_id, captain_id, decision, ts) VALUES(?,?,?,?)",
                   (sid, cid, "pending", ts))
    notify(others, "approval", "Results for '{}' need your agreement.".format(sac_label(sac)),
           url_for("approvals"))
    log_activity("{} entered '{}' results — awaiting the other captain(s)".format(_who(actor), sac_label(sac)))
    return "captains"


@app.route("/score/<sid>/submit", methods=["POST"])
@roles_required("captain", "admin")
def score_submit(sid):
    u = current_user()
    sac = get_sac(sid)
    if not sac:
        abort(404)
    if not can_score(u, sac):
        abort(403)
    if u["is_admin"]:
        _approve_event(sac, u)  # admin has the final say — publish directly
        flash("Results published.", "success")
    else:
        dest = submit_for_approval(sac, u)
        flash("Results sent to the other captain(s) to agree." if dest == "captains"
              else "No other captain is involved — sent to the admin to decide.", "success")
    return redirect(url_for("score_event", sid=sid))


@app.route("/approvals")
@roles_required("captain", "admin")
def approvals():
    u = current_user()
    my_votes = []
    if u["is_captain"]:
        my_votes = db.query(
            "SELECT v.*, s.name AS event_name, sac.age_category, sac.gender, sac.date "
            "FROM sports_score_votes v JOIN sports_sport_age_categories sac ON sac.id=v.sac_id "
            "JOIN sports_sports s ON s.id=sac.sport_id "
            "WHERE v.captain_id=? AND v.decision='pending' ORDER BY sac.date DESC", (u["id"],))
    pending, disputed, roster = [], [], []
    if u["is_admin"]:
        for e in load_sacs("sac.approval_status IN ('pending','disputed')"):
            e["votes"] = db.query(
                "SELECT v.decision, us.name AS cap_name, us.team FROM sports_score_votes v "
                "LEFT JOIN sports_users us ON us.id=v.captain_id WHERE v.sac_id=?", (e["id"],))
            (disputed if e["approval_status"] == "disputed" else pending).append(e)
        # Roster requests (captains adding players) live here now too.
        for p in db.query("SELECT * FROM sports_participants WHERE pending_team IS NOT NULL "
                          "AND archived=0 ORDER BY id"):
            p["pending_team_name"] = domain.team_name(p["pending_team"], teams())
            p["team_name"] = domain.team_name(p["team"], teams()) if p.get("team") else ""
            p["age"] = domain.age_from_birth_year(p.get("birth_year"))
            roster.append(p)
    return render_template("approvals.html", my_votes=my_votes, pending=pending,
                           disputed=disputed, roster=roster, sac_label=sac_label)


@app.route("/approvals/<sid>/vote", methods=["POST"])
@roles_required("captain", "admin")
def approval_vote(sid):
    u = current_user()
    sac = get_sac(sid)
    if not sac:
        abort(404)
    decision = request.form.get("decision")
    vote = db.query_one("SELECT * FROM sports_score_votes WHERE sac_id=? AND captain_id=?", (sid, u["id"]))
    if not vote or decision not in ("agree", "disagree"):
        abort(403)
    db.execute("UPDATE sports_score_votes SET decision=?, ts=? WHERE sac_id=? AND captain_id=?",
               (decision, db.now_ts(), sid, u["id"]))
    if decision == "disagree":
        db.execute("UPDATE sports_sport_age_categories SET approval_status='disputed' WHERE id=?", (sid,))
        _notify_admins("admin", "Captain {} disputed the results for '{}'.".format(u["name"], sac_label(sac)),
                       url_for("approvals"))
        log_activity("Captain {} disputed '{}' results".format(u["name"], sac_label(sac)))
        flash("You disagreed — the admin has been notified to take action.", "info")
    else:
        votes = db.query("SELECT decision FROM sports_score_votes WHERE sac_id=?", (sid,))
        if votes and all(v["decision"] == "agree" for v in votes):
            _approve_event(get_sac(sid), u)
            flash("All captains agreed — results published.", "success")
        else:
            flash("Your approval is recorded. Waiting on the other captain(s).", "success")
    return redirect(url_for("approvals"))


@app.route("/approvals/<sid>/admin", methods=["POST"])
@roles_required("admin")
def approval_admin(sid):
    u = current_user()
    sac = get_sac(sid)
    if not sac:
        abort(404)
    action = request.form.get("action")
    if action == "approve":
        _approve_event(sac, u)
        flash("Approved by admin (override).", "success")
    elif action == "withhold":
        db.execute("UPDATE sports_sport_age_categories SET approval_status='draft', finalised=0, "
                   "status='in_progress' WHERE id=?", (sid,))
        db.execute("DELETE FROM sports_score_votes WHERE sac_id=?", (sid,))
        notify(involved_captains(sid), "approval",
               "Admin withheld the results for '{}' for re-checking.".format(sac_label(sac)),
               url_for("approvals"))
        log_activity("Admin withheld '{}' results".format(sac_label(sac)))
        flash("Withheld and reopened for re-scoring.", "info")
    return redirect(url_for("approvals"))


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------
def _participant_results(pid):
    """A participant's results across all completed events + total points."""
    rows = db.query(
        "SELECT r.*, s.name AS sport_name, sac.age_category, sac.gender, sac.id AS sac_id "
        "FROM sports_results r JOIN sports_sport_age_categories sac ON sac.id=r.sac_id "
        "JOIN sports_sports s ON s.id=sac.sport_id "
        "WHERE r.participant=? AND (sac.finalised=1 OR sac.status='completed') ORDER BY s.name",
        (pid,))
    return rows, sum(int(x["points"] or 0) for x in rows)


@app.route("/results")
@login_required
def results_list():
    u = current_user()
    acting = u["acting"]
    counts = {}
    for r in db.query("SELECT sac_id, COUNT(*) AS n FROM sports_results GROUP BY sac_id"):
        counts[r["sac_id"]] = r["n"]
    # Player's own linked participant (for the "My results" tab).
    mine = linked_participant(u) if u["is_player"] else None
    # The team behind the "Team results" tab: a player's own team, or the team a
    # captain manages.
    my_team = (mine.get("team") if mine else None) or \
              (u.get("team") if u["is_captain"] else None)
    # Captains see Standings + Overall in one scrollable view (no Mine/Team tabs).
    captain_view = (acting == "captain")
    if captain_view:
        has_mine = False
        has_team = False
    else:
        has_mine = bool(mine)
        has_team = bool(my_team)
    standings = team_standings()

    # Default: captain → standings (page shows both); player → mine; staff → overall.
    view = request.args.get("view") or ("mine" if mine and not captain_view else
                                        ("standings" if captain_view else "overall"))
    # Players don't get the staff Overall cascade — only their own, their team's,
    # and the standings. Coerce any other request to a sensible player view.
    if acting == "player" and view not in ("mine", "team", "standings"):
        view = "mine" if has_mine else "standings"

    # Team results: this team's published results (teammates' individual results +
    # any team/doubles event results keyed to the team id).
    team_results = None
    if my_team:
        trows = db.query(
            "SELECT r.*, s.name AS sport_name, sac.age_category, sac.gender, sac.id AS sac_id, "
            "sac.event_format, p.name AS pname FROM sports_results r "
            "JOIN sports_sport_age_categories sac ON sac.id=r.sac_id "
            "JOIN sports_sports s ON s.id=sac.sport_id "
            "LEFT JOIN sports_participants p ON p.id=r.participant "
            "WHERE (sac.finalised=1 OR sac.status='completed') AND (p.team=? OR r.participant=?) "
            "ORDER BY s.name, p.name", (my_team, my_team))
        team_results = {"rows": trows, "score": sum(int(x["points"] or 0) for x in trows),
                        "team_name": domain.team_name(my_team, teams()), "team_id": my_team}

    f_age = request.args.get("age") or ""
    f_gender = request.args.get("gender") or ""
    f_cat = request.args.get("category") or ""     # sport category
    f_sport = request.args.get("sport") or ""      # sport id
    f_team = request.args.get("team") or ""        # team id
    if not f_team and u["is_captain"] and not u["is_admin"]:
        f_team = u.get("team") or ""
    f_name = request.args.get("name") or ""        # participant id
    f_from = request.args.get("from") or ""        # event date range
    f_to = request.args.get("to") or ""

    # Decide whose individual results to show (if any).
    target = None
    if view == "mine" and mine:
        target = mine
    elif f_name:
        target = db.query_one("SELECT * FROM sports_participants WHERE id=?", (f_name,))

    player_results = None
    if target:
        rows, score = _participant_results(target["id"])
        player_results = {"p": target, "rows": rows, "score": score,
                          "team_name": domain.team_name(target.get("team"), teams())}

    # Overall completed events — the full set drives the cascade dropdowns.
    completed = [e for e in load_sacs("sac.archived=0")
                 if counts.get(e["id"]) and (e.get("finalised") or e.get("status") == "completed")]
    combos = [{"gender": e.get("gender") or "", "age": e.get("age_category") or "",
               "cat": e.get("category_id") or "", "sport": e.get("sport_id") or ""}
              for e in completed]
    # Events involving a given team (individual: a participant's team; team event:
    # the team id is the result's participant).
    team_sacs = None
    if f_team:
        team_sacs = set(r["sac_id"] for r in db.query(
            "SELECT DISTINCT r.sac_id FROM sports_results r LEFT JOIN sports_participants p ON p.id=r.participant "
            "WHERE p.team=? OR r.participant=?", (f_team, f_team)))
    sacs = [e for e in completed
            if (not f_gender or (e.get("gender") or "") == f_gender)
            and (not f_age or (e.get("age_category") or "") == f_age)
            and (not f_cat or (e.get("category_id") or "") == f_cat)
            and (not f_sport or (e.get("sport_id") or "") == f_sport)
            and (not f_from or (e.get("date") or "") >= f_from)
            and (not f_to or (e.get("date") or "") <= f_to)
            and (team_sacs is None or e["id"] in team_sacs)]
    sacs.sort(key=lambda e: (e.get("sport_name") or "", e.get("age_category") or ""))

    # Participants who have results — for the cascading Name dropdown.
    named = db.query(
        "SELECT DISTINCT p.id, p.name, p.division, p.category FROM sports_participants p "
        "JOIN sports_results r ON r.participant=p.id JOIN sports_sport_age_categories sac ON sac.id=r.sac_id "
        "WHERE (sac.finalised=1 OR sac.status='completed') ORDER BY p.name")

    return render_template("results.html", events=sacs, counts=counts, sac_label=sac_label,
                           view=view, has_mine=has_mine, has_team=has_team, acting=acting,
                           captain_view=captain_view,
                           player_results=player_results, team_results=team_results,
                           standings=standings,
                           named=named, categories=config()["categories"],
                           divisions=domain.DIVISIONS, sport_categories=sport_categories(),
                           sports=all_sports(), combos=combos, teams=teams(), my_team=my_team,
                           filters={"age": f_age, "gender": f_gender, "category": f_cat,
                                    "sport": f_sport, "team": f_team, "name": f_name,
                                    "date_from": f_from, "date_to": f_to},
                           team_name=lambda t: domain.team_name(t, teams()))


@app.route("/results/<sid>")
@login_required
def result_detail(sid):
    sac = get_sac(sid)
    if not sac:
        abort(404)
    cats = config()["categories"]
    parts = {p["id"]: p for p in db.query("SELECT * FROM sports_participants")}
    results = [decode_result(r) for r in db.query("SELECT * FROM sports_results WHERE sac_id=?", (sid,))]
    results.sort(key=lambda r: (r.get("place") or 99))
    is_team = domain.is_team_format(sac.get("event_format"))
    if is_team:
        # results.participant holds the team id; attach team + its chosen line-up.
        tmap = {t["id"]: t for t in teams()}
        members_by_team = {row["team"]["id"]: row["members"] for row in eligible_teams(sac)}
        for r in results:
            r["team_obj"] = tmap.get(r["participant"])
            r["players"] = members_by_team.get(r["participant"], [])
    u = current_user()
    groups = {(sac.get("age_category"), sac.get("gender")): results} if results else {}
    return render_template("result_detail.html", e=sac, groups=groups, parts=parts,
                           is_team=is_team, categories=cats, teams=teams(), sac_label=sac_label,
                           is_staff=domain.is_staff(u),
                           cat_name=lambda c: domain.category_name(c, cats),
                           team_name=lambda t: domain.team_name(t, teams()),
                           sport_cat_name=lambda c: domain.sport_category_name(c, sport_categories()))


# --------------------------------------------------------------------------
# Maintenance hub
# --------------------------------------------------------------------------
@app.route("/admin")
@roles_required("admin")
def admin_home():
    counts = {
        "sports_teams": db.count("sports_teams"), "sports_sport_categories": db.count("sports_sport_categories"),
        "sports_sports": db.count("sports_sports"), "sac": db.count("sports_sport_age_categories"),
        "sports_participants": db.count("sports_participants"),
        "sports_users": db.count("sports_users"), "sports_announcements": db.count("sports_announcements"),
        "sample": db.count("sports_participants", "sample=1") + db.count("sports_sports", "sample=1")
                  + db.count("sports_sport_age_categories", "sample=1"),
    }
    return render_template("admin_home.html", counts=counts)


@app.route("/admin/selftest", methods=["GET", "POST"])
@roles_required("admin")
def admin_selftest():
    """Run the committed end-to-end test suite on demand and show pass/fail.

    The suite (tests/test_app.py) spins up its OWN server on a throwaway database,
    so running it here never touches the live data. Keep tests/test_app.py updated
    as features change — this panel always reflects the current suite."""
    import subprocess
    import sys
    results, summary, raw, ran = [], "", "", False
    if request.method == "POST":
        ran = True
        root = os.path.dirname(os.path.abspath(__file__))
        try:
            proc = subprocess.run([sys.executable, os.path.join(root, "tests", "test_app.py")],
                                  capture_output=True, text=True, timeout=180, cwd=root)
            raw = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        except Exception as exc:  # noqa
            raw = "Could not run the test suite: {}".format(exc)
        for ln in raw.splitlines():
            if ln.startswith("PASS "):
                results.append({"ok": True, "text": ln[5:]})
            elif ln.startswith("FAIL "):
                results.append({"ok": False, "text": ln[5:]})
            elif "passed" in ln and "total" in ln:
                summary = ln.strip()
    return render_template("selftest.html", results=results, summary=summary, raw=raw, ran=ran,
                           passed=sum(1 for r in results if r["ok"]),
                           failed=sum(1 for r in results if not r["ok"]))


# ---- Team selection (draft players onto teams from offline data) ----------
@app.route("/admin/team-selection", methods=["GET", "POST"])
@roles_required("admin")
def team_selection():
    if request.method == "POST":
        changed = 0
        for key, val in request.form.items():
            if key.startswith("team_"):
                pid = key[5:]
                new_team = val or None
                cur = db.query_one('SELECT team, name, "user" FROM sports_participants WHERE id=?', (pid,))
                if cur and (cur["team"] or None) != new_team:
                    db.execute("UPDATE sports_participants SET team=? WHERE id=?", (new_team, pid))
                    changed += 1
                    if cur["user"]:
                        tname = domain.team_name(new_team, teams()) if new_team else "Unassigned"
                        notify([cur["user"]], "assignment",
                               "You have been placed in {}.".format(tname) if new_team
                               else "You have been moved to Unassigned.", url_for("dashboard"))
        log_activity("Admin updated team selections ({} change(s))".format(changed))
        flash("Saved — {} player(s) updated.".format(changed), "success")
        return redirect(url_for("team_selection", **{k: v for k, v in request.args.items()}))

    cats = config()["categories"]
    q = (request.args.get("q") or "").strip().lower()
    f_div = request.args.get("division") or ""
    f_cat = request.args.get("category") or ""
    f_team = request.args.get("team") or ""
    rows = db.query("SELECT * FROM sports_participants WHERE archived=0 ORDER BY name")
    for p in rows:
        p["age"] = domain.age_from_birth_year(p.get("birth_year"))

    def matches(p):
        if q and q not in p["name"].lower() and q not in p["id"].lower():
            return False
        if f_div and p.get("division") != f_div:
            return False
        if f_cat and p.get("category") != f_cat:
            return False
        if f_team == "__none__" and p.get("team"):
            return False
        if f_team and f_team != "__none__" and (p.get("team") or "") != f_team:
            return False
        return True

    people = [p for p in rows if matches(p)]
    tlist = teams()
    counts = {t["id"]: db.count("sports_participants", "team=? AND archived=0", (t["id"],)) for t in tlist}
    counts["__none__"] = db.count("sports_participants", "team IS NULL AND archived=0")
    return render_template("team_selection.html", people=people, teams=tlist, counts=counts,
                           categories=cats, divisions=domain.DIVISIONS,
                           filters={"q": q, "division": f_div, "category": f_cat, "team": f_team})


# ---- Teams maintenance ----------------------------------------------------
@app.route("/admin/teams", methods=["GET", "POST"])
@roles_required("admin")
def admin_teams():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            name = (request.form.get("name") or "").strip()
            if not name:
                flash("Team name is required.", "danger")
            elif db.query_one("SELECT 1 FROM sports_teams WHERE name=?", (name,)):
                flash("A team named '{}' already exists. Team names must be unique.".format(name), "danger")
            else:
                tid = _slugify(name)
                if not tid or db.query_one("SELECT 1 FROM sports_teams WHERE id=?", (tid,)):
                    tid = db.next_id("sports_teams", "team")
                _pid = (current_program() or {}).get("id")
                db.execute("INSERT INTO sports_teams(id, name, colour, program_id, sample) VALUES(?,?,?,?,0)",
                           (tid, name, request.form.get("colour") or "#888888", _pid))
                audit_stamp("sports_teams", tid, created=True)
                log_activity("Admin added team {}".format(name))
                flash("Team added.", "success")
        elif action == "save_all":
            # Validate uniqueness across the submitted names first.
            names, dupe = {}, None
            for t in db.query("SELECT id FROM sports_teams"):
                nm = (request.form.get("name_" + t["id"]) or "").strip()
                if not nm:
                    continue
                if nm in names:
                    dupe = nm
                    break
                names[nm] = t["id"]
            if dupe:
                flash("Duplicate team name '{}'. Team names must be unique.".format(dupe), "danger")
            else:
                for t in db.query("SELECT id, name, colour FROM sports_teams"):
                    nm = (request.form.get("name_" + t["id"]) or t["name"]).strip()
                    colour = request.form.get("colour_" + t["id"]) or "#888888"
                    if nm == t["name"] and colour == (t.get("colour") or "#888888"):
                        continue
                    db.execute("UPDATE sports_teams SET name=?, colour=? WHERE id=?", (nm, colour, t["id"]))
                    audit_stamp("sports_teams", t["id"])
                log_activity("Admin saved team changes")
                flash("Teams saved.", "success")
        elif action == "delete":
            tid = request.form.get("id")
            db.execute("UPDATE sports_participants SET team=NULL WHERE team=?", (tid,))
            db.execute("DELETE FROM sports_teams WHERE id=?", (tid,))
            flash("Team deleted; its players are now unassigned.", "info")
        return redirect(url_for("admin_teams"))
    pid = (current_program() or {}).get("id")
    rows = db.query("SELECT t.*, (SELECT COUNT(*) FROM sports_participants p WHERE p.team=t.id) AS n "
                    "FROM sports_teams t WHERE t.program_id=? ORDER BY t.name", (pid,))
    for r in rows:
        r["in_use"] = r["n"] > 0
        r["usage"] = {"Players": r["n"]}
    return render_template("teams.html", teams=rows)


# ---- Sport categories maintenance ----------------------------------------
@app.route("/admin/sport-categories", methods=["GET", "POST"])
@roles_required("admin")
def admin_sport_categories():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            name = (request.form.get("name") or "").strip()
            if not name:
                flash("Category name is required.", "danger")
            elif db.query_one("SELECT 1 FROM sports_sport_categories WHERE name=?", (name,)):
                flash("A category named '{}' already exists. Names must be unique.".format(name), "danger")
            else:
                sid = _slugify(name)
                if not sid or db.query_one("SELECT 1 FROM sports_sport_categories WHERE id=?", (sid,)):
                    sid = db.next_id("sports_sport_categories", "sc")
                nxt = (db.query_one("SELECT MAX(sort) AS m FROM sports_sport_categories")["m"] or 0) + 1
                _pid = (current_program() or {}).get("id")
                db.execute("INSERT INTO sports_sport_categories(id, name, sort, program_id, sample) VALUES(?,?,?,?,0)",
                           (sid, name, nxt, _pid))
                audit_stamp("sports_sport_categories", sid, created=True)
                flash("Category added.", "success")
        elif action == "save_all":
            names, dupe = {}, None
            for c in db.query("SELECT id FROM sports_sport_categories"):
                nm = (request.form.get("name_" + c["id"]) or "").strip()
                if not nm:
                    continue
                if nm in names:
                    dupe = nm
                    break
                names[nm] = c["id"]
            if dupe:
                flash("Duplicate category name '{}'. Names must be unique.".format(dupe), "danger")
            else:
                for c in db.query("SELECT id, name FROM sports_sport_categories"):
                    nm = (request.form.get("name_" + c["id"]) or c["name"]).strip()
                    if nm == c["name"]:
                        continue
                    db.execute("UPDATE sports_sport_categories SET name=? WHERE id=?", (nm, c["id"]))
                    audit_stamp("sports_sport_categories", c["id"])
                log_activity("Admin saved sport category changes")
                flash("Categories saved.", "success")
        elif action == "delete":
            sid = request.form.get("id")
            # If the category still has sports, send the admin to the manage page
            # to archive / move / delete those children first.
            if db.count("sports_sports", "category_id=?", (sid,)) > 0:
                return redirect(url_for("category_manage", cid=sid))
            db.execute("DELETE FROM sports_sport_categories WHERE id=?", (sid,))
            flash("Category deleted.", "info")
        return redirect(url_for("admin_sport_categories"))
    _pid = (current_program() or {}).get("id")
    rows = db.query("SELECT c.*, (SELECT COUNT(*) FROM sports_sports s WHERE s.category_id=c.id) AS n "
                    "FROM sports_sport_categories c WHERE c.program_id=? ORDER BY c.sort, c.name", (_pid,))
    for r in rows:
        r["in_use"] = r["n"] > 0
        r["usage"] = {"Sports": r["n"]}
    return render_template("sport_categories.html", categories=rows)


@app.route("/admin/sport-categories/<cid>/manage", methods=["GET", "POST"])
@roles_required("admin")
def category_manage(cid):
    u = current_user()
    cat = db.query_one("SELECT * FROM sports_sport_categories WHERE id=?", (cid,))
    if not cat:
        abort(404)
    if request.method == "POST":
        op = request.form.get("op")
        ids = request.form.getlist("child")  # selected sport ids
        if op in ("move", "archive", "delete") and not ids:
            flash("Select at least one sport first.", "warning")
            return redirect(url_for("category_manage", cid=cid))
        if op == "move":
            target = request.form.get("target_cat") or None
            for s in ids:
                db.execute("UPDATE sports_sports SET category_id=? WHERE id=? AND category_id=?",
                           (target, s, cid))
                audit_stamp("sports_sports", s)
            log_activity("{} moved {} sport(s) out of '{}'".format(_who(u), len(ids), cat["name"]))
            flash("Moved {} sport(s).".format(len(ids)), "success")
        elif op == "archive":
            for s in ids:
                db.execute("UPDATE sports_sports SET archived=1 WHERE id=? AND category_id=?", (s, cid))
                audit_stamp("sports_sports", s)
            flash("Archived {} sport(s).".format(len(ids)), "success")
        elif op == "delete":
            hard = soft = 0
            for s in ids:
                if sport_in_use(s):
                    db.execute("UPDATE sports_sports SET archived=1 WHERE id=? AND category_id=?", (s, cid))
                    soft += 1
                else:
                    db.execute("DELETE FROM sports_sports WHERE id=? AND category_id=?", (s, cid))
                    db.execute("DELETE FROM sports_signups WHERE sport_id=?", (s,))
                    hard += 1
            log_activity("{} deleted {} sport(s) from '{}'".format(_who(u), hard, cat["name"]))
            msg = "Deleted {} sport(s).".format(hard)
            if soft:
                msg += " {} were in use (have events) and were archived instead.".format(soft)
            flash(msg, "info")
        elif op == "delete_category":
            if db.count("sports_sports", "category_id=?", (cid,)) > 0:
                flash("Resolve the remaining sports before deleting the category.", "danger")
                return redirect(url_for("category_manage", cid=cid))
            db.execute("DELETE FROM sports_sport_categories WHERE id=?", (cid,))
            log_activity("{} deleted sport category '{}'".format(_who(u), cat["name"]))
            flash("Category '{}' deleted.".format(cat["name"]), "info")
            return redirect(url_for("admin_sport_categories"))
        return redirect(url_for("category_manage", cid=cid))
    sports = db.query(
        "SELECT s.*, (SELECT COUNT(*) FROM sports_sport_age_categories sac WHERE sac.sport_id=s.id) "
        "AS n_sac FROM sports_sports s WHERE s.category_id=? ORDER BY s.archived, s.name", (cid,))
    other_cats = db.query("SELECT * FROM sports_sport_categories WHERE id<>? ORDER BY sort, name", (cid,))
    return render_template("category_manage.html", cat=cat, sports=sports, other_cats=other_cats)


# ---- Age categories maintenance ------------------------------------------
@app.route("/admin/age-categories", methods=["GET", "POST"])
@roles_required("admin")
def admin_age_categories():
    cats = config()["categories"]
    if request.method == "POST":
        # Single save: apply edits + removals (del_<id>) + an optional new band,
        # all at once, so a removal doesn't discard other on-screen edits.
        old_by_id = {c["id"]: c for c in cats}
        cu = current_user()
        actor = cu["id"] if cu else None
        ts = db.now_ts()
        new = []
        for c in cats:
            cid = c["id"]
            if request.form.get("del_" + cid):
                continue
            old = old_by_id.get(cid, {})
            band = {
                "id": cid,
                "name": (request.form.get("name_" + cid) or c["name"]).strip(),
                "min_age": _to_int(request.form.get("min_" + cid), c["min_age"]),
                "max_age": _to_int(request.form.get("max_" + cid), c["max_age"]),
                "created_by": old.get("created_by"),
                "created_at": old.get("created_at"),
            }
            changed = (band["name"] != old.get("name")
                       or band["min_age"] != old.get("min_age")
                       or band["max_age"] != old.get("max_age"))
            band["modified_by"] = actor if changed else old.get("modified_by")
            band["modified_at"] = ts if changed else old.get("modified_at")
            new.append(band)
        nname = (request.form.get("new_name") or "").strip()
        if nname:
            new.append({
                "id": _unique_age_id(nname, [c["id"] for c in new]),
                "name": nname,
                "min_age": _to_int(request.form.get("new_min"), 0),
                "max_age": _to_int(request.form.get("new_max"), 200),
                "created_by": actor, "created_at": ts,
            })

        errors = []
        seen = set()
        for c in new:
            if c["min_age"] > c["max_age"]:
                errors.append("'{}' — min age can't be greater than max age.".format(c["name"]))
            if c["name"] in seen:
                errors.append("Duplicate age group name '{}'.".format(c["name"]))
            seen.add(c["name"])
        ov = _age_overlap(new)
        if ov:
            a, b = ov
            errors.append("Age ranges overlap: '{}' ({}–{}) and '{}' ({}–{}). Ranges must not overlap."
                          .format(a["name"], a["min_age"], a["max_age"],
                                  b["name"], b["min_age"], b["max_age"]))
        if errors:
            for e in errors:
                flash(e, "danger")
            # Re-render with the submitted values so edits aren't lost.
            return render_template("age_categories.html", categories=new,
                                   new_band={"name": nname,
                                             "min": request.form.get("new_min") or "",
                                             "max": request.form.get("new_max") or ""})
        db.set_setting("categories", new)
        _recompute_all_categories(new)
        log_activity("Admin updated age groups")
        flash("Age groups saved.", "success")
        return redirect(url_for("admin_age_categories"))
    p_counts = {r["category"]: r["n"] for r in db.query(
        "SELECT category, COUNT(*) AS n FROM sports_participants WHERE category IS NOT NULL GROUP BY category")}
    sac_counts = {r["age_category"]: r["n"] for r in db.query(
        "SELECT age_category, COUNT(*) AS n FROM sports_sport_age_categories WHERE age_category IS NOT NULL GROUP BY age_category")}
    for c in cats:
        n_p = p_counts.get(c["id"], 0)
        n_sac = sac_counts.get(c["id"], 0)
        c["in_use"] = (n_p + n_sac) > 0
        c["usage"] = {}
        if n_p:
            c["usage"]["Participants"] = n_p
        if n_sac:
            c["usage"]["Sport Events"] = n_sac
    return render_template("age_categories.html", categories=cats, new_band={})


def _to_int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _age_overlap(cats):
    """Return the first overlapping pair of age bands, or None."""
    for i in range(len(cats)):
        for j in range(i + 1, len(cats)):
            a, b = cats[i], cats[j]
            if a["min_age"] <= b["max_age"] and b["min_age"] <= a["max_age"]:
                return (a, b)
    return None


def _unique_age_id(name, existing):
    base = _slugify(name) or "age"
    sid, n = base, 2
    while sid in existing:
        sid, n = "{}_{}".format(base, n), n + 1
    return sid


def _recompute_all_categories(cats):
    for p in db.query("SELECT id, birth_year FROM sports_participants"):
        cat = domain.category_for_birth_year(p["birth_year"], cats)
        db.execute("UPDATE sports_participants SET category=? WHERE id=?", (cat, p["id"]))


# ---- Users & roles maintenance -------------------------------------------
@app.route("/admin/users")
@roles_required("admin")
def users_list():
    f_name = (request.args.get("name") or "").strip().lower()
    f_role = request.args.get("role") or ""
    f_team = request.args.get("team") or ""
    f_status = request.args.get("status") or ""
    f_age = request.args.get("age") or ""

    users = [decode_user(u) for u in db.query("SELECT * FROM sports_users ORDER BY username")]

    # Age category isn't exposed by the sports_users VIEW (admin accounts have none) -
    # look it up from sports_participants directly, keyed by the login/user id.
    cat_by_uid = {r["user"]: r["category"] for r in
                  db.query('SELECT "user", category FROM sports_participants WHERE "user" IS NOT NULL')}
    for u in users:
        u["category"] = cat_by_uid.get(u["id"])

    if f_name:
        users = [u for u in users if f_name in (u.get("name") or "").lower()
                 or f_name in (u.get("username") or "").lower()]
    if f_role:
        users = [u for u in users if f_role in u["roles"]]
    if f_team:
        users = [u for u in users if u.get("team") == f_team]
    if f_status:
        want_disabled = f_status == "disabled"
        users = [u for u in users if bool(u.get("disabled")) == want_disabled]
    if f_age:
        users = [u for u in users if u.get("category") == f_age]

    return render_template("users.html", users=users, teams=teams(),
                           categories=config()["categories"],
                           filters={"name": f_name, "role": f_role, "team": f_team,
                                    "status": f_status, "age": f_age},
                           team_name=lambda t: domain.team_name(t, teams()))


def _user_form_ctx(u, new):
    return dict(u=u, teams=teams(), new=new, houses=domain.HOUSES,
                admin_house=domain.ADMIN_HOUSE, security_questions=domain.SECURITY_QUESTIONS,
                divisions=domain.DIVISIONS, default_password=domain.DEFAULT_PASSWORD)


@app.route("/admin/users/new", methods=["GET", "POST"])
@roles_required("admin")
def user_new():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        name = (request.form.get("name") or "").strip()
        house = request.form.get("house") or ""
        is_admin_acct = house == domain.ADMIN_HOUSE
        roles = ["admin"] if is_admin_acct else (request.form.getlist("roles") or ["player"])
        team = request.form.get("team") or None
        number = (request.form.get("number") or "").strip()
        birth_year = request.form.get("birth_year")
        division = request.form.get("division") or None
        email = (request.form.get("email") or "").strip()
        sec_q = (request.form.get("security_question") or "").strip()
        sec_a = request.form.get("security_answer") or ""
        form = {"username": username, "name": name, "roles": roles, "team": team,
                "email": email, "house": house, "number": number, "birth_year": birth_year,
                "division": division, "security_question": sec_q}
        errors = []
        if not username or not name:
            errors.append("Username and name are required.")
        if not is_admin_acct and "captain" in roles and not team:
            errors.append("Captains must be assigned a team.")
        if db.query_one("SELECT 1 FROM sports_users WHERE lower(username)=?", (username.lower(),)):
            errors.append("Username already taken.")
        if not sec_q or not _norm_answer(sec_a):
            errors.append("Set a security question and answer (for password recovery).")
        by = None
        if not is_admin_acct:
            if house not in domain.HOUSES:
                errors.append("Pick a house (or Admin).")
            if not (number.isdigit() and len(number) == 3):
                errors.append("Enter a 3-digit house number.")
            elif house in domain.HOUSES and db.query_one(
                    "SELECT 1 FROM sports_participants WHERE house=? AND number=?", (house, number)):
                errors.append("That number is already taken in house {}.".format(house))
            if birth_year:
                try:
                    by = int(birth_year)
                except ValueError:
                    errors.append("Birth year must be a number.")
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("user_form.html", **_user_form_ctx(form, True))
        uid = db.next_id("sports_users", "U")
        pw_hash = security.hash_password(domain.DEFAULT_PASSWORD)
        if is_admin_acct:
            db.execute("INSERT INTO sports_admins(id, username, name, roles, email, password, "
                       "security_question, security_answer, must_change_pw, disabled, last_login, "
                       "created_at, notify_prefs, sample) VALUES(?,?,?,?,?,?,?,?,1,0,NULL,?,'{}',0)",
                       (uid, username, name, db.dumps(["admin"]), email, pw_hash,
                        sec_q, hash_answer(sec_a), db.now_ts()))
            audit_stamp("sports_admins", uid, created=True)
        else:
            cats = config()["categories"]
            is_player = "player" in roles
            pid = db.next_id("sports_participants", "R")
            db.execute(
                'INSERT INTO sports_participants(id, name, team, division, birth_year, category, "user", '
                "username, password, roles, email, security_question, security_answer, must_change_pw, "
                "house, number, roster, volunteer, archived, sample) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,0,0,0)",
                (pid, name, team if "captain" in roles else None, division, by,
                 domain.category_for_birth_year(by, cats) if by else None,
                 uid, username, pw_hash, db.dumps(roles), email, sec_q, hash_answer(sec_a),
                 house, number, 1 if is_player else 0))
            audit_stamp("sports_participants", pid, created=True)
        log_activity("Admin created account '{}' ({})".format(username, ", ".join(roles)))
        flash("Account created. Temporary password is '{}' — the user must change it at first "
              "login.".format(domain.DEFAULT_PASSWORD), "success")
        return redirect(url_for("users_list"))
    return render_template("user_form.html", **_user_form_ctx({"roles": ["player"]}, True))


@app.route("/admin/users/<uid>/edit", methods=["GET", "POST"])
@roles_required("admin")
def user_edit(uid):
    u = decode_user(db.query_one("SELECT * FROM sports_users WHERE id=?", (uid,)))
    if not u:
        abort(404)
    table, _key = _account_where(uid)
    if request.method == "POST":
        name = (request.form.get("name") or u["name"]).strip()
        email = (request.form.get("email") or "").strip()
        if table == "sports_admins":
            # Admin accounts keep the admin role; only name/email are editable here.
            update_account(uid, name=name, email=email)
        else:
            roles = request.form.getlist("roles") or ["player"]
            team = request.form.get("team") or None
            update_account(uid, name=name, roles=db.dumps(roles),
                           team=team if "captain" in roles else None, email=email)
            db.execute("UPDATE sports_participants SET roster=? WHERE \"user\"=?",
                       (1 if "player" in roles else 0, uid))
            log_activity("Admin set roles for '{}' to {}".format(u["username"], ", ".join(roles)))
        flash("User updated.", "success")
        return redirect(url_for("users_list"))
    return render_template("user_form.html", **_user_form_ctx(u, False))


@app.route("/admin/users/<uid>/toggle", methods=["POST"])
@roles_required("admin")
def user_toggle(uid):
    u = decode_user(db.query_one("SELECT * FROM sports_users WHERE id=?", (uid,)))
    if not u:
        abort(404)
    if u["is_admin"] and not u["disabled"]:
        if db.count("sports_admins", "id<>? AND disabled=0", (uid,)) == 0:
            flash("Cannot disable the last active admin.", "danger")
            return redirect(url_for("users_list"))
    newval = 0 if u["disabled"] else 1
    update_account(uid, disabled=newval)
    flash("Account {}.".format("disabled" if newval else "enabled"), "info")
    return redirect(url_for("users_list"))


@app.route("/admin/users/<uid>/reset", methods=["POST"])
@roles_required("admin")
def user_reset(uid):
    password = request.form.get("password") or ""
    errs = security.password_errors(password)
    if errs:
        for e in errs:
            flash("Password: " + e, "danger")
        return redirect(url_for("users_list"))
    u = db.query_one("SELECT * FROM sports_users WHERE id=?", (uid,))
    if not u:
        abort(404)
    set_account_password(uid, password, must_change=1)
    log_activity("Admin reset password for '{}'".format(u["username"]))
    flash("Password reset for {} — they'll be asked to change it at next login.".format(u["username"]), "success")
    return redirect(url_for("users_list"))


@app.route("/admin/users/<uid>/reset-default", methods=["POST"])
@roles_required("admin")
def user_reset_default(uid):
    """One-click: reset to DEFAULT_PASSWORD and force change on next login."""
    u = db.query_one("SELECT * FROM sports_users WHERE id=?", (uid,))
    if not u:
        abort(404)
    set_account_password(uid, domain.DEFAULT_PASSWORD, must_change=1)
    log_activity("Admin reset password to default for '{}'".format(u["username"]))
    next_url = request.form.get("next") or url_for("users_list")
    flash("Password for {} reset to '{}' — they must change it at next login.".format(
        u["username"], domain.DEFAULT_PASSWORD), "success")
    return redirect(next_url)


# ---- Announcements maintenance -------------------------------------------
@app.route("/admin/announcements", methods=["GET", "POST"])
@roles_required("admin")
def admin_announcements():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            title = (request.form.get("title") or "").strip()
            body = (request.form.get("body") or "").strip()
            visible = 1 if request.form.get("visible") else 0
            if title:
                _pid = (current_program() or {}).get("id")
                aid = db.execute("INSERT INTO sports_announcements(ts, title, body, visible, program_id, sample) "
                                 "VALUES(?,?,?,?,?,0)", (db.now_ts(), title, body, visible, _pid))
                audit_stamp("sports_announcements", aid, created=True)
                if visible:
                    notify_roles(domain.ALL_ROLES, "announcement",
                                 "New announcement: {}".format(title), url_for("dashboard"))
                log_activity("Admin posted announcement '{}'".format(title))
                flash("Announcement posted.", "success")
        elif action == "edit":
            aid = request.form.get("id")
            db.execute("UPDATE sports_announcements SET title=?, body=?, visible=? WHERE id=?",
                       ((request.form.get("title") or "").strip(),
                        (request.form.get("body") or "").strip(),
                        1 if request.form.get("visible") else 0, aid))
            audit_stamp("sports_announcements", aid)
            flash("Announcement updated.", "success")
        elif action == "delete":
            db.execute("DELETE FROM sports_announcements WHERE id=?", (request.form.get("id"),))
            flash("Announcement deleted.", "info")
        return redirect(url_for("admin_announcements"))
    _pid = (current_program() or {}).get("id")
    items = db.query("SELECT * FROM sports_announcements WHERE program_id=? ORDER BY id DESC", (_pid,))
    return render_template("announcements.html", items=items)


# ---- General settings -----------------------------------------------------
@app.route("/admin/settings", methods=["GET", "POST"])
@roles_required("admin")
def admin_settings():
    cfg = config()
    if request.method == "POST":
        db.set_setting("event_name", (request.form.get("event_name") or cfg["event_name"]).strip())
        pts, _ = domain.parse_points_string(request.form.get("default_points") or "")
        db.set_setting("points", pts)
        db.set_setting("count_in_progress", bool(request.form.get("count_in_progress")))
        db.set_setting("sender_email", (request.form.get("sender_email") or "").strip())
        for role in ("captain",):
            val = request.form.get("role_pw_" + role) or ""
            if val:
                errs = security.password_errors(val)
                if errs:
                    flash("Common {} password: {}".format(role, ", ".join(errs)), "danger")
                else:
                    db.set_setting("role_pw_" + role, security.hash_password(val))
        log_activity("Admin updated general settings")
        flash("Settings saved.", "success")
        return redirect(url_for("admin_settings"))
    default_points = ",".join(str(cfg["points"].get(str(i)))
                              for i in range(1, len(cfg["points"]) + 1))
    return render_template("settings.html", cfg=cfg, default_points=default_points)


# ---- Programs ---------------------------------------------------------------

@app.route("/select-program", methods=["GET", "POST"])
@login_required
def select_program():
    u = current_user()
    if request.method == "POST":
        pid = request.form.get("program_id")
        p = db.query_one("SELECT * FROM sports_programs WHERE id=?", (pid,))
        if p and (u["is_admin"] or p["status"] in domain.PROGRAM_VISIBLE_STATUSES):
            session["active_program"] = pid
            flash("Switched to {}.".format(p["name"]), "success")
            return redirect(url_for("dashboard"))
        flash("Invalid program selection.", "danger")
    if u["is_admin"]:
        programs = db.query(
            "SELECT p.*, "
            "(SELECT COUNT(*) FROM sports_sports WHERE program_id=p.id) AS n_sports, "
            "(SELECT COUNT(*) FROM sports_teams WHERE program_id=p.id) AS n_teams "
            "FROM sports_programs p ORDER BY p.name")
    else:
        programs = db.query(
            "SELECT p.*, "
            "(SELECT COUNT(*) FROM sports_sports WHERE program_id=p.id) AS n_sports, "
            "(SELECT COUNT(*) FROM sports_teams WHERE program_id=p.id) AS n_teams "
            "FROM sports_programs p WHERE p.status IN ('active','completed') ORDER BY p.name")
    current_pid = session.get("active_program")
    return render_template("select_program.html", programs=programs, current_pid=current_pid)


@app.route("/admin/programs")
@roles_required("admin")
def admin_programs():
    order_expr = "CASE p.status " + " ".join(
        "WHEN '{}' THEN {}".format(s, i)
        for i, s in enumerate(domain.PROGRAM_STATUS_ORDER)) + " ELSE 99 END"
    rows = db.query(
        "SELECT p.*, "
        "(SELECT COUNT(*) FROM sports_sports WHERE program_id=p.id) AS n_sports, "
        "(SELECT COUNT(*) FROM sports_teams WHERE program_id=p.id) AS n_teams "
        "FROM sports_programs p ORDER BY {}, p.name".format(order_expr))
    for r in rows:
        r["in_use"] = r["n_sports"] > 0 or r["n_teams"] > 0
        r["usage"] = {}
        if r["n_sports"]:
            r["usage"]["Sports"] = r["n_sports"]
        if r["n_teams"]:
            r["usage"]["Teams"] = r["n_teams"]
    current_pid = session.get("active_program")
    return render_template("programs.html", programs=rows, current_pid=current_pid,
                           status_order=domain.PROGRAM_STATUS_ORDER,
                           status_badge=domain.PROGRAM_STATUS_BADGE)


@app.route("/admin/programs/new", methods=["GET", "POST"])
@roles_required("admin")
def admin_program_new():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Program name is required.", "danger")
            return render_template("program_form.html", new=True, p={}, form=request.form)
        pid = _slugify(name) or db.next_id("sports_programs", "prog")
        if db.query_one("SELECT 1 FROM sports_programs WHERE id=?", (pid,)):
            pid = db.next_id("sports_programs", "prog")
        has_teams = 1 if request.form.get("has_teams") else 0
        db.execute(
            "INSERT INTO sports_programs(id, name, description, has_teams, status, start_date, end_date, sample) "
            "VALUES(?,?,?,?,?,?,?,0)",
            (pid, name, (request.form.get("description") or "").strip(),
             has_teams, request.form.get("status") or "planned",
             request.form.get("start_date") or None,
             request.form.get("end_date") or None))
        audit_stamp("sports_programs", pid, created=True)
        log_activity("Admin created program '{}'".format(name))
        flash("Program '{}' created.".format(name), "success")
        return redirect(url_for("admin_programs"))
    return render_template("program_form.html", new=True, p={}, form={},
                           statuses=domain.PROGRAM_STATUSES)


@app.route("/admin/programs/<pid>/edit", methods=["GET", "POST"])
@roles_required("admin")
def admin_program_edit(pid):
    p = db.query_one("SELECT * FROM sports_programs WHERE id=?", (pid,))
    if not p:
        abort(404)
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Program name is required.", "danger")
            return render_template("program_form.html", new=False, p=p, form=request.form,
                                   statuses=domain.PROGRAM_STATUSES)
        has_teams = 1 if request.form.get("has_teams") else 0
        db.execute(
            "UPDATE sports_programs SET name=?, description=?, has_teams=?, status=?, "
            "start_date=?, end_date=? WHERE id=?",
            (name, (request.form.get("description") or "").strip(),
             has_teams, request.form.get("status") or "active",
             request.form.get("start_date") or None,
             request.form.get("end_date") or None, pid))
        audit_stamp("sports_programs", pid)
        log_activity("Admin updated program '{}'".format(name))
        flash("Program updated.", "success")
        return redirect(url_for("admin_programs"))
    return render_template("program_form.html", new=False, p=p, form=p,
                           statuses=domain.PROGRAM_STATUSES)


@app.route("/admin/programs/<pid>/switch", methods=["POST"])
@roles_required("admin")
def admin_program_switch(pid):
    p = db.query_one("SELECT * FROM sports_programs WHERE id=?", (pid,))
    if not p:
        abort(404)
    session["active_program"] = pid
    flash("Switched to {}.".format(p["name"]), "success")
    return redirect(url_for("admin_programs"))


@app.route("/admin/programs/<pid>/archive", methods=["POST"])
@roles_required("admin")
def admin_program_archive(pid):
    # Legacy toggle — kept for backwards compat; redirects to set-status instead.
    p = db.query_one("SELECT * FROM sports_programs WHERE id=?", (pid,))
    if not p:
        abort(404)
    order = domain.PROGRAM_STATUS_ORDER
    cur = p["status"] if p["status"] in order else "active"
    idx = order.index(cur)
    new_status = order[idx + 1] if idx < len(order) - 1 else order[idx]
    db.execute("UPDATE sports_programs SET status=? WHERE id=?", (new_status, pid))
    if new_status not in domain.PROGRAM_VISIBLE_STATUSES and session.get("active_program") == pid:
        session.pop("active_program", None)
        flash("Program moved to '{}'. Please select another program.".format(new_status), "warning")
        return redirect(url_for("select_program"))
    flash("Program status: {}.".format(new_status), "success")
    return redirect(url_for("admin_programs"))


@app.route("/admin/sports_programs/<pid>/set-status", methods=["POST"])
@roles_required("admin")
def admin_program_set_status(pid):
    p = db.query_one("SELECT * FROM sports_programs WHERE id=?", (pid,))
    if not p:
        abort(404)
    new_status = request.form.get("status")
    order = domain.PROGRAM_STATUS_ORDER
    if new_status not in order:
        flash("Invalid status.", "danger")
        return redirect(url_for("admin_programs"))
    # Going Active requires start_date <= today (if start_date is set).
    if new_status == "active" and p.get("start_date"):
        try:
            sd = date.fromisoformat(p["start_date"])
            if sd > date.today():
                flash("Cannot activate '{}' before its start date ({}).".format(
                    p["name"], p["start_date"]), "danger")
                return redirect(url_for("admin_programs"))
        except (ValueError, TypeError):
            pass
    db.execute("UPDATE sports_programs SET status=? WHERE id=?", (new_status, pid))
    audit_stamp("sports_programs", pid)
    log_activity("Admin set program '{}' status to '{}'".format(p["name"], new_status))
    if new_status not in domain.PROGRAM_VISIBLE_STATUSES and session.get("active_program") == pid:
        session.pop("active_program", None)
        flash("Program moved to '{}' — no longer visible to players. Please select another.".format(
            new_status), "warning")
        return redirect(url_for("select_program"))
    flash("Program '{}' status set to '{}'.".format(p["name"], new_status), "success")
    return redirect(url_for("admin_programs"))


# ---- Import / export / backup --------------------------------------------
@app.route("/admin/data")
@roles_required("admin")
def admin_data():
    return render_template("data.html")


@app.route("/admin/export/<collection>")
@roles_required("admin")
def export_collection(collection):
    if collection not in ("sports_participants", "sports_sports", "sports_sport_age_categories", "sports_results"):
        abort(404)
    import json
    data = db.query("SELECT * FROM {}".format(collection))
    return Response(json.dumps(data, indent=2, ensure_ascii=False),
                    mimetype="application/json",
                    headers={"Content-Disposition": "attachment; filename={}.json".format(collection)})


@app.route("/admin/backup")
@roles_required("admin")
def backup():
    if not os.path.exists(db.DB_PATH):
        abort(404)
    with open(db.DB_PATH, "rb") as fh:
        blob = fh.read()
    log_activity("Admin downloaded a database backup")
    return Response(blob, mimetype="application/octet-stream",
                    headers={"Content-Disposition": "attachment; filename=sportsmeet.db"})


@app.route("/admin/import", methods=["POST"])
@roles_required("admin")
def import_participants():
    import csv
    import io
    import json as _json
    f = request.files.get("file")
    if not f:
        flash("No file uploaded.", "danger")
        return redirect(url_for("admin_data"))
    raw = f.read().decode("utf-8", errors="replace")
    cats = config()["categories"]
    try:
        rows = _json.loads(raw) if f.filename.lower().endswith(".json") \
            else list(csv.DictReader(io.StringIO(raw)))
    except Exception as exc:  # noqa
        flash("Could not parse file: {}".format(exc), "danger")
        return redirect(url_for("admin_data"))
    added = 0
    for row in rows:
        name = (row.get("name") or row.get("Full name") or "").strip()
        if not name:
            continue
        by = row.get("birth_year") or row.get("Birth year") or row.get("year")
        pid = db.next_id("sports_participants", "R")
        byi = int(by) if by else None
        db.execute("INSERT INTO sports_participants(id, name, team, division, birth_year, category, "
                   "volunteer, archived, sample) VALUES(?,?,?,?,?,?,0,0,0)",
                   (pid, name, (row.get("team") or "").strip() or None,
                    (row.get("division") or row.get("Gender") or "Boys").strip(),
                    byi, domain.category_for_birth_year(byi, cats)))
        audit_stamp("sports_participants", pid, created=True)
        added += 1
    log_activity("Admin imported {} participants".format(added))
    flash("Imported {} participants.".format(added), "success")
    return redirect(url_for("participants_list"))


# ---- Reset / sample-data maintenance -------------------------------------
@app.route("/admin/reset", methods=["GET", "POST"])
@roles_required("admin")
def admin_reset():
    u = current_user()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "sample":
            _delete_sample(u)
            log_activity("Admin deleted sample data")
            flash("Sample data removed. Your own data is untouched.", "success")
        elif action == "wipe":
            _wipe_all(u)
            log_activity("Admin wiped ALL data (admin logins kept)")
            flash("Everything wiped — participants, teams, sports, results, announcements and "
                  "age categories (reset to defaults). Only admin logins were kept.", "warning")
        elif action == "reseed":
            import seed
            seed.ensure_seed()
            flash("Sample data restored where tables were empty.", "info")
        return redirect(url_for("admin_reset"))
    counts = {
        "sample_participants": db.count("sports_participants", "sample=1"),
        "sample_events": db.count("sports_sports", "sample=1"),
        "sample_users": db.count("sports_users", "sample=1 AND roles NOT LIKE '%admin%'"),
        "sample_announcements": db.count("sports_announcements", "sample=1"),
        "total_participants": db.count("sports_participants"),
        "total_events": db.count("sports_sports"),
    }
    return render_template("reset.html", counts=counts)


def _delete_sample(current):
    # Remove sample content but keep teams, categories, settings and admins.
    db.execute("DELETE FROM sports_signups WHERE participant_id IN (SELECT id FROM sports_participants WHERE sample=1)")
    db.execute("DELETE FROM sports_results WHERE participant IN (SELECT id FROM sports_participants WHERE sample=1)")
    db.execute("DELETE FROM sports_signups WHERE sport_id IN (SELECT id FROM sports_sports WHERE sample=1)")
    db.execute("DELETE FROM sports_score_votes WHERE sac_id IN "
               "(SELECT id FROM sports_sport_age_categories WHERE sample=1)")
    db.execute("DELETE FROM sports_results WHERE sac_id IN "
               "(SELECT id FROM sports_sport_age_categories WHERE sample=1)")
    db.execute("DELETE FROM sports_sport_age_categories WHERE sample=1")
    db.execute("DELETE FROM sports_sports WHERE sample=1")
    # Sample player/captain accounts ARE sample participants — removed here too.
    db.execute("DELETE FROM sports_participants WHERE sample=1")
    db.execute("DELETE FROM sports_announcements WHERE sample=1")
    db.execute("DELETE FROM sports_admins WHERE sample=1 AND id<>?", (current["id"],))
    db.execute("DELETE FROM sports_notifications WHERE user_id NOT IN (SELECT id FROM sports_users)")
    db.execute("DELETE FROM sports_audit")


def _wipe_all(current):
    """Full factory reset: remove EVERY participant/event/team/category/result —
    keep only admin logins. Age categories reset to the default set."""
    for t in ("sports_signups", "sports_results", "sports_score_votes", "sports_participants", "sports_sport_age_categories",
              "sports_sports", "sports_announcements", "sports_notifications", "sports_audit", "sports_event_lineups",
              "sports_teams", "sports_sport_categories"):
        db.execute("DELETE FROM {}".format(t))
    db.set_setting("categories", domain.DEFAULT_CATEGORIES)


def _slugify(text):
    return "".join(ch if ch.isalnum() else "_" for ch in (text or "").lower()).strip("_")


# --------------------------------------------------------------------------
# What's New (X16) + About (X17)
# --------------------------------------------------------------------------
WHATS_NEW = [
    {"date": "2026-06-26", "title": "Mobile nav strip", "body":
     "Bottom tabs replaced with a full horizontal scrollable nav strip below the topbar on phones."},
    {"date": "2026-06-26", "title": "Captain dashboard hub", "body":
     "Captains now see a team summary card on the dashboard: standings, points, and recent results. "
     "The week schedule is also filtered to the team's own events."},
    {"date": "2026-06-26", "title": "Player profile & withdraw", "body":
     "Players can now edit their birth year and gender from the dashboard. "
     "A Withdraw button appears on each registered event (locked if results exist)."},
    {"date": "2026-06-26", "title": "Admin password reset", "body":
     "Admins can one-click reset any user's password to the default and force a change on next login."},
    {"date": "2026-06-25", "title": "Captain team-scoped views + Program lifecycle", "body":
     "Sports Calendar and Results pages default to the captain's team. "
     "Programs now have a 6-stage lifecycle (Planned → New → WIP → Draft → Active → Completed)."},
    {"date": "2026-06-25", "title": "Mobile-first redesign", "body":
     "Tables convert to stacked cards on phones. 44 px tap targets on all buttons and inputs."},
    {"date": "2026-06-24", "title": "Multi-program support", "body":
     "Multiple programs (e.g. yearly editions) can now coexist. Sport categories, sports, teams, "
     "and announcements are scoped per program."},
]


@app.route("/whats-new")
@login_required
def whats_new():
    return render_template("whats_new.html", items=WHATS_NEW)


@app.route("/about")
def about():
    cfg = config()
    return render_template("about.html", cfg=cfg,
                           schema_version=db.SCHEMA_VERSION)


@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403,
                           message="You don't have permission to view this page."), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="Page not found."), 404


if __name__ == "__main__":
    import seed
    seed.ensure_seed()
    port = int(os.environ.get("SPORTS_PORT", "3003"))
    # Debug on for local dev; set SPORTS_DEBUG=0 in production (also enables secure
    # cookies + secret-key enforcement above). This dev server is NOT for production —
    # use gunicorn (see DEPLOY.md).
    if _DEBUG and app.secret_key == "dev-change-me-in-production":
        print("WARNING: using the default SPORTS_SECRET_KEY — set a strong value in production.")
    app.run(host="127.0.0.1", port=port, debug=_DEBUG)
