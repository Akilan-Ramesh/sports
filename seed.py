"""Create the schema and seed a large, realistic SAMPLE dataset.

Everything seeded here is flagged ``sample=1`` so the admin can wipe just the
sample data later (Maintenance -> Reset) without touching real data they add.
Idempotent: each section only runs when its table is empty.
"""
import random

import db
import domain
import security

random.seed(42)
YEAR = domain.current_year()

TEAMS = [
    {"id": "smashers", "name": "Smashers", "colour": "#e02424"},
    {"id": "hammers", "name": "Hammers", "colour": "#2563eb"},
    {"id": "warriors", "name": "Warriors", "colour": "#16a34a"},
    {"id": "titans", "name": "Titans", "colour": "#9333ea"},
]

ROLE_PASSWORDS = {"captain": "Captain@2026"}

# Temporary convenience: every seeded account uses this single login password.
DEMO_PASSWORD = "nicknick"

# (name, username, roles, team, password)
# Admins hold ONLY the admin role (no role switcher). Several accounts have
# multiple roles to demo the top-right role dropdown.
STAFF = [
    ("Event Owner", "admin", ["admin"], None, "Admin@123"),
    ("Smashers Captain", "captain_smashers", ["captain"], "smashers", "Smash@123"),
    ("Hammers Captain", "captain_hammers", ["captain"], "hammers", "Hammer@123"),
    ("Warriors Captain", "captain_warriors", ["captain"], "warriors", "Warrior@123"),
    ("Titans Captain", "captain_titans", ["captain"], "titans", "Titan@123"),
    # Multi-role demo accounts (these get the role-switch dropdown):
    ("Player & Captain", "play_cap", ["player", "captain"], "titans", "Playcap@123"),
]

# Team sports: fewer places, bigger points (10/5). Individual: 5/3/1.
TEAM_SPORTS = {"Football", "Volleyball", "Basketball", "Cricket", "Water Polo", "Tug of War"}
# Two-player events: scored per team but only two names, shown inline.
DOUBLES_SPORTS = {"Badminton Doubles", "Table Tennis Doubles"}


def sport_format(name):
    if name in TEAM_SPORTS:
        return "team"
    if name in DOUBLES_SPORTS:
        return "doubles"
    return "individual"


SPORTS = {
    "track_field": [
        ("100m Sprint", "placement", ["U13", "U18", "U30"], 1),
        ("200m Sprint", "placement", ["U18", "U30"], 1),
        ("400m Run", "placement", ["U18", "U30", "U50"], 1),
        ("800m Run", "placement", ["U18", "U30", "U50"], 1),
        ("Long Jump", "measured", ["U13", "U18", "U30"], 3),
        ("High Jump", "measured", ["U18", "U30"], 3),
        ("Shot Put", "measured", ["U30", "U50", "U70"], 3),
        ("Discus Throw", "measured", ["U30", "U50"], 3),
        ("Javelin Throw", "measured", ["U18", "U30", "U50"], 3),
    ],
    "water_sports": [
        ("50m Freestyle", "placement", ["U13", "U18", "U30"], 1),
        ("100m Freestyle", "placement", ["U18", "U30"], 1),
        ("50m Backstroke", "placement", ["U18", "U30"], 1),
        ("Water Polo", "placement", ["U18", "U30"], 1),
    ],
    "ball_sports": [
        ("Football", "placement", ["U18", "U30"], 1),
        ("Volleyball", "placement", ["U18", "U30", "U50"], 1),
        ("Basketball", "placement", ["U18", "U30"], 1),
        ("Cricket", "placement", ["U18", "U30"], 1),
        ("Table Tennis", "placement", ["U13", "U18", "U30", "U50"], 1),
        ("Badminton Doubles", "placement", ["U18", "U30"], 1),
        ("Table Tennis Doubles", "placement", ["U18", "U30", "U50"], 1),
    ],
    "others": [
        ("Tug of War", "placement", ["U30", "U50", "U70"], 1),
        ("Chess", "placement", ["U13", "U18", "U30", "U50"], 1),
        ("Carrom", "placement", ["U18", "U30", "U50"], 1),
        ("Balloon Volleyball", "participation", ["U50", "U70"], 1),
    ],
}

STATIONS = ["Track A", "Track B", "Field 1", "Field 2", "Pool", "Court 1",
            "Court 2", "Ground", "Hall A", "Hall B"]

BOY_NAMES = ["Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh",
             "Krishna", "Ishaan", "Rohan", "Karthik", "Vijay", "Suresh", "Ramesh",
             "Gopal", "Mahesh", "Naveen", "Praveen", "Dinesh", "Ganesh", "Hari",
             "Kiran", "Manoj", "Nikhil", "Rahul", "Sanjay", "Tarun", "Anand",
             "Bharath", "Chandran", "Deepak", "Eswar", "Gautam", "Harish"]
GIRL_NAMES = ["Aanya", "Diya", "Saanvi", "Aadhya", "Anika", "Pari", "Myra", "Sara",
              "Priya", "Anjali", "Meena", "Lakshmi", "Divya", "Kavya", "Nisha",
              "Pooja", "Radha", "Sneha", "Swathi", "Geetha", "Hema", "Indira",
              "Janani", "Kamala", "Latha", "Malar", "Nandhini", "Revathi", "Sushma",
              "Usha", "Bhavya", "Charu", "Ishita", "Jaya"]
SURNAMES = ["Kumar", "Raj", "Nair", "Iyer", "Menon", "Reddy", "Pillai", "Sharma",
            "Verma", "Rao", "Gupta", "Das", "Bose", "Shetty", "Krishnan",
            "Subramanian", "Venkat", "Mohan", "Prasad", "Naidu"]

AGE_BANDS = [(9, 12), (9, 12), (13, 17), (13, 17), (13, 17),
             (18, 29), (18, 29), (18, 29), (30, 49), (30, 49), (50, 69)]

PLAYERS_PER_TEAM = 20
FINALISED_COUNT = 12


def _by(age):
    return YEAR - age


def _seed_admin(uid, username, name):
    db.execute("INSERT INTO sports_admins(id, username, name, roles, email, password, "
               "must_change_pw, disabled, last_login, created_at, notify_prefs, sample) "
               "VALUES(?,?,?,?,?,?,0,0,NULL,?,'{}',1)",
               (uid, username, name, db.dumps(["admin"]), "",
                security.hash_password(DEMO_PASSWORD), db.now_ts()))


def _seed_participant_login(pid, uid, username, roles, **cols):
    """Insert a participant that also carries a login (player/captain account)."""
    base = dict(id=pid, name=cols.get("name", username), team=cols.get("team"),
                division=cols.get("division"), birth_year=cols.get("birth_year"),
                category=cols.get("category"))
    base["user"] = uid
    base["username"] = username
    base["password"] = security.hash_password(DEMO_PASSWORD)
    base["roles"] = db.dumps(roles)
    base["roster"] = 1 if "player" in roles else 0
    base["volunteer"] = 0
    base["archived"] = 0
    base["sample"] = 1
    cnames = list(base.keys())
    quoted = ", ".join('"{}"'.format(c) if c == "user" else c for c in cnames)
    ph = ", ".join("?" for _ in cnames)
    db.execute("INSERT INTO sports_participants({}) VALUES({})".format(quoted, ph),
               tuple(base[c] for c in cnames))


DEFAULT_PROGRAM_ID = "default"
DEFAULT_PROGRAM_NAME = "Community Sports Meet 2026"


def ensure_seed():
    db.init_db()

    if db.get_setting("points") is None:
        db.set_setting("event_name", DEFAULT_PROGRAM_NAME)
        db.set_setting("points", domain.DEFAULT_POINTS)
        db.set_setting("count_in_progress", False)
        db.set_setting("categories", domain.DEFAULT_CATEGORIES)
        db.set_setting("sender_email", "")
    for role, pw in ROLE_PASSWORDS.items():
        if db.get_setting("role_pw_" + role) is None:
            db.set_setting("role_pw_" + role, security.hash_password(DEMO_PASSWORD))

    # ---- Default program (must come before teams / sport_categories / sports) --
    if db.count("sports_programs") == 0:
        db.execute(
            "INSERT INTO sports_programs(id, name, status, has_teams, sample) VALUES(?,?,?,?,1)",
            (DEFAULT_PROGRAM_ID, DEFAULT_PROGRAM_NAME, "active", 1))

    if db.count("sports_teams") == 0:
        db.executemany(
            "INSERT INTO sports_teams(id, name, colour, program_id, sample) VALUES(?,?,?,?,1)",
            [(t["id"], t["name"], t["colour"], DEFAULT_PROGRAM_ID) for t in TEAMS])

    if db.count("sports_sport_categories") == 0:
        db.executemany(
            "INSERT INTO sports_sport_categories(id, name, sort, program_id, sample) VALUES(?,?,?,?,1)",
            [(c["id"], c["name"], c["sort"], DEFAULT_PROGRAM_ID) for c in domain.DEFAULT_SPORT_CATEGORIES])

    if db.count("sports_users") == 0:
        n = 0
        for name, username, roles, team, pw in STAFF:
            n += 1
            uid = "U{:03d}".format(n)
            if "admin" in roles:
                _seed_admin(uid, username, name)
            else:
                pid = db.next_id("sports_participants", "R")
                _seed_participant_login(pid, uid, username, roles, name=name, team=team)

    # ---- Sports master + sport-age-categories ----------------------------
    if db.count("sports_sports") == 0:
        from datetime import date, timedelta
        today = date.today()
        flat = []
        for cat_id, sports in SPORTS.items():
            for (name, mode, age_cats, rounds) in sports:
                flat.append((cat_id, name, mode, age_cats, rounds))
        random.shuffle(flat)
        n = len(flat)
        sid_n = sac_n = 0
        for i, (cat_id, name, mode, age_cats, rounds) in enumerate(flat):
            sid_n += 1
            sport_id = "S{:03d}".format(sid_n)
            # Demo: last sport archived; 2nd-last left with no SAC (so it's deletable).
            archived = 1 if i == n - 1 else 0
            db.execute("INSERT INTO sports_sports(id, category_id, name, program_id, archived, sample) "
                       "VALUES(?,?,?,?,?,1)", (sport_id, cat_id, name, DEFAULT_PROGRAM_ID, archived))
            if archived or i == n - 2:
                continue
            offset = -14 + round(i * 27 / max(1, n - 1))
            d = today + timedelta(days=offset)
            fmt = sport_format(name)
            points, places = domain.default_points_for(fmt, mode)
            status = "registration_open" if offset > 7 else "scheduled"
            for j, age in enumerate(age_cats):
                for g, gender in enumerate(domain.DIVISIONS):
                    sac_n += 1
                    sac_id = "SAC{:04d}".format(sac_n)
                    slot = "09:00" if (j + g) % 2 == 0 else "11:00"
                    db.execute(
                        "INSERT INTO sports_sport_age_categories(id, sport_id, age_category, gender, "
                        "scoring_mode, event_format, rounds, points, places, date, slot, location, "
                        "specifics, finalised, status, sample) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,1)",
                        (sac_id, sport_id, age, gender, mode, fmt, rounds, db.dumps(points), places,
                         d.isoformat(), slot, STATIONS[i % len(STATIONS)],
                         "{} · {} · {}".format(name, age, gender), status))

    # ---- Participants ----------------------------------------------------
    # (STAFF above may already have created captain participant-logins, so start
    # the roster ids after whatever exists and guard on the roster, not the count.)
    created_pids = []
    if not db.query_one('SELECT 1 FROM sports_participants WHERE sample=1 AND "user" IS NULL'):
        cats = domain.DEFAULT_CATEGORIES
        pid_n = int(db.next_id("sports_participants", "R")[1:])
        used = set()
        for t in TEAMS:
            for j in range(PLAYERS_PER_TEAM):
                division = "Male" if j % 2 == 0 else "Female"
                pool = BOY_NAMES if division == "Male" else GIRL_NAMES
                while True:
                    name = "{} {}".format(random.choice(pool), random.choice(SURNAMES))
                    if name not in used:
                        used.add(name)
                        break
                lo, hi = random.choice(AGE_BANDS)
                by = _by(random.randint(lo, hi))
                pid = "R{:03d}".format(pid_n)
                pid_n += 1
                created_pids.append((pid, t["id"], division))
                db.execute(
                    "INSERT INTO sports_participants(id, name, team, division, birth_year, "
                    "category, volunteer, archived, sample) VALUES(?,?,?,?,?,?,?,0,1)",
                    (pid, name, t["id"], division, by,
                     domain.category_for_birth_year(by, cats),
                     1 if random.random() < 0.15 else 0))

        # A few UNASSIGNED sample players to demo the captain-assignment flow.
        for k in range(4):
            division = "Male" if k % 2 == 0 else "Female"
            pool = BOY_NAMES if division == "Male" else GIRL_NAMES
            name = "{} {}".format(random.choice(pool), random.choice(SURNAMES))
            by = _by(random.randint(13, 29))
            pid = "R{:03d}".format(pid_n)
            pid_n += 1
            db.execute(
                "INSERT INTO sports_participants(id, name, team, division, birth_year, "
                "category, volunteer, archived, sample) VALUES(?,?,NULL,?,?,?,0,0,1)",
                (pid, name, division, by,
                 domain.category_for_birth_year(by, cats)))

    # ---- Demo player logins: turn existing roster entries into accounts -----
    # (player-role demo accounts; play_cap is created above in STAFF)
    if not db.query_one("SELECT 1 FROM sports_users WHERE username='player1'"):
        targets = db.query('SELECT id FROM sports_participants WHERE team IS NOT NULL '
                           'AND "user" IS NULL AND sample=1 ORDER BY id LIMIT 3')
        for i, username in enumerate(["player1", "player2", "player3"]):
            if i < len(targets):
                uid = db.next_id("sports_users", "U")
                db.execute('UPDATE sports_participants SET "user"=?, username=?, password=?, '
                           'roles=?, roster=1 WHERE id=?',
                           (uid, username, security.hash_password(DEMO_PASSWORD),
                            db.dumps(["player"]), targets[i]["id"]))
        # "mixed" is a multi-role demo account deliberately left WITHOUT a competing
        # team, so it isn't treated as a spurious co-captain in approval workflows.
        mt = db.query_one('SELECT id FROM sports_participants WHERE team IS NULL '
                          'AND "user" IS NULL AND sample=1 ORDER BY id LIMIT 1')
        if mt:
            uid = db.next_id("sports_users", "U")
            db.execute('UPDATE sports_participants SET "user"=?, username=?, password=?, '
                       'roles=?, roster=1 WHERE id=?',
                       (uid, "mixed", security.hash_password(DEMO_PASSWORD),
                        db.dumps(["player", "captain"]), mt["id"]))

    # ---- Demo logins for every age category x gender ---------------------
    # Meaningful usernames like "u18_male" / "a70_female" (password Demo@123),
    # each linked to a participant in that exact age + gender, on a real team.
    if not db.query_one("SELECT 1 FROM sports_users WHERE username='u18_male'"):
        cats = domain.DEFAULT_CATEGORIES
        team_ids = [t["id"] for t in TEAMS]
        ti = 0
        for c in cats:
            mid_age = (c["min_age"] + min(c["max_age"], c["min_age"] + 10)) // 2
            by = domain.current_year() - mid_age
            for gender in domain.DIVISIONS:
                uname = "{}_{}".format(c["id"].lower(), gender.lower())
                display = "{} {} Demo".format(c["name"], gender)
                team = team_ids[ti % len(team_ids)]
                ti += 1
                uid = db.next_id("sports_users", "U")
                pid = db.next_id("sports_participants", "R")
                _seed_participant_login(pid, uid, uname, ["player"], name=display, team=team,
                                        division=gender, birth_year=by, category=c["id"])

    # ---- Sign-ups (participant -> sport, by eligibility) -----------------
    if db.count("sports_signups") == 0:
        by_sport = {}
        for r in db.query("SELECT sport_id, age_category, gender FROM sports_sport_age_categories"):
            by_sport.setdefault(r["sport_id"], set()).add((r["age_category"], r["gender"]))
        for p in db.query("SELECT * FROM sports_participants WHERE team IS NOT NULL"):
            eligible = [sid for sid, combos in by_sport.items()
                        if (p["category"], p["division"]) in combos]
            if not eligible:
                continue
            k = random.randint(1, min(4, len(eligible)))
            for sid in random.sample(eligible, k):
                db.execute("INSERT OR IGNORE INTO sports_signups(participant_id, sport_id) "
                           "VALUES(?,?)", (p["id"], sid))

    # ---- Results for SACs already held (date <= today) -------------------
    if db.count("sports_results") == 0:
        from datetime import date
        today = date.today().isoformat()
        cfg = domain.get_config()
        sacs = db.query("SELECT * FROM sports_sport_age_categories WHERE date IS NOT NULL AND date<=? "
                        "ORDER BY date, slot", (today,))
        # Collect scoreable individual events; team/doubles auto-complete inline.
        indiv = []
        for sac in sacs:
            if domain.is_team_format(sac.get("event_format")):
                tids = [r["team"] for r in db.query(
                    "SELECT DISTINCT p.team FROM sports_participants p JOIN sports_signups su ON su.participant_id=p.id "
                    "WHERE su.sport_id=? AND p.category=? AND p.division=? AND p.archived=0 "
                    "AND p.team IS NOT NULL", (sac["sport_id"], sac["age_category"], sac["gender"]))]
                if len(tids) < 2:
                    continue
                random.shuffle(tids)
                for i, tid in enumerate(tids, 1):
                    db.execute("INSERT INTO sports_results(sac_id, participant, place, participated, history) "
                               "VALUES(?,?,?,1,?)", (sac["id"], tid, i, db.dumps([])))
                domain.recompute_sac_places(sac, cfg)
                db.execute("UPDATE sports_sport_age_categories SET finalised=1, approval_status='approved', "
                           "status='completed' WHERE id=?", (sac["id"],))
                continue
            pids = [r["id"] for r in db.query(
                "SELECT p.id FROM sports_participants p JOIN sports_signups su ON su.participant_id=p.id "
                "WHERE su.sport_id=? AND p.category=? AND p.division=? AND p.archived=0",
                (sac["sport_id"], sac["age_category"], sac["gender"]))]
            if len(pids) >= 2:
                indiv.append((sac, pids))

        # Score only the first ~third of them (-> pending / disputed / completed
        # demo). Leave the rest as already-held DRAFTS awaiting result entry.
        n_score = max(3, len(indiv) // 3)
        demo_done = 0  # 0 -> pending, 1 -> disputed, rest -> completed
        for sac, pids in indiv[:n_score]:
            random.shuffle(pids)
            sid, mode = sac["id"], sac["scoring_mode"]
            if mode == "measured":
                for pid in pids:
                    rounds = [round(random.uniform(3.0, 12.0), 2) for _ in range(sac["rounds"])]
                    db.execute("INSERT INTO sports_results(sac_id, participant, rounds, history) "
                               "VALUES(?,?,?,?)", (sid, pid, db.dumps(rounds), db.dumps([])))
            elif mode == "participation":
                for i, pid in enumerate(pids, 1):
                    place = i if i <= 3 else None
                    db.execute("INSERT INTO sports_results(sac_id, participant, place, participated, "
                               "history) VALUES(?,?,?,1,?)", (sid, pid, place, db.dumps([])))
            else:
                for i, pid in enumerate(pids, 1):
                    db.execute("INSERT INTO sports_results(sac_id, participant, place, history) "
                               "VALUES(?,?,?,?)", (sid, pid, i, db.dumps([])))
            domain.recompute_sac_places(sac, cfg)
            if demo_done == 0:
                _seed_approval(sid, "pending")
            elif demo_done == 1:
                _seed_approval(sid, "disputed")
            else:
                db.execute("UPDATE sports_sport_age_categories SET finalised=1, approval_status='approved', "
                           "status='completed' WHERE id=?", (sid,))
            demo_done += 1

    # ---- Announcements (player-facing) -----------------------------------
    if db.count("sports_announcements") == 0:
        anns = [
            ("Welcome to Sports Meet 2026!",
             "Registration is open. Sign up for your sports_sports from the Sports Sign-up page."),
            ("Opening ceremony — June 8th, 9:00 AM",
             "Assemble at the main ground. All sports_participants and volunteers please be on time."),
            ("Results going live",
             "Finished sports now show their results. Check the Sports page for medals."),
        ]
        ts = db.now_ts()
        for i, (title, body) in enumerate(anns):
            db.execute("INSERT INTO sports_announcements(ts, title, body, visible, program_id, sample) "
                       "VALUES(?,?,?,1,?,1)", (ts - (len(anns) - i) * 3600, title, body, DEFAULT_PROGRAM_ID))

    # ---- Notifications: tell captains about unassigned players -----------
    if db.count("sports_notifications") == 0:
        captains = db.query("SELECT id, team FROM sports_users WHERE roles LIKE '%captain%'")
        unassigned = db.query("SELECT id, name, division FROM sports_participants WHERE team IS NULL")
        ts = db.now_ts()
        for u in unassigned:
            for c in captains:
                db.execute(
                    'INSERT INTO sports_notifications(user_id, ts, type, message, link, "read") '
                    "VALUES(?,?,?,?,?,0)",
                    (c["id"], ts, "new_player",
                     "New player '{}' ({}) registered and needs a team.".format(
                         u["name"], u["division"]),
                     "/participants"))

    # ---- Activity log (staff only) ---------------------------------------
    if db.count("sports_audit") == 0:
        activity = [
            "System initialised with sample data",
            "Admin set up {} sports_sports and {} sport-age-categories".format(
                db.count("sports_sports"), db.count("sports_sport_age_categories")),
            "Captains added {} players to the rosters".format(
                db.count("sports_participants", "team IS NOT NULL")),
            "{} players registered and await team assignment".format(
                db.count("sports_participants", "team IS NULL")),
            "Captains completed {} sport-age-categories".format(
                db.count("sports_sport_age_categories", "finalised=1")),
        ]
        ts = db.now_ts()
        for i, msg in enumerate(activity):
            db.execute("INSERT INTO sports_audit(ts, message) VALUES(?,?)",
                       (ts - (len(activity) - i) * 3600, msg))

    # ---- Attribute SAMPLE rows to 'system' for the audit trail -----------
    # (Only sample data — never touch real records the user added; those simply
    #  display "system" via the NULL fallback until they're next edited.)
    now = db.now_ts()
    for t in db.AUDIT_TABLES:
        db.execute("UPDATE {} SET created_by='system', "
                   "created_at=COALESCE(created_at, ?) "
                   "WHERE created_by IS NULL AND sample=1".format(t),
                   (now,))


def _involved_captains(sac_id):
    rows = db.query(
        "SELECT DISTINCT u.id FROM sports_users u JOIN sports_participants p ON p.team=u.team "
        "JOIN sports_results r ON r.participant=p.id "
        "WHERE r.sac_id=? AND u.roles LIKE '%captain%'", (sac_id,))
    return [r["id"] for r in rows]


def _seed_approval(sac_id, mode):
    """Set up a live approval demo: 'pending' (awaiting captains) or 'disputed'."""
    row = db.query_one("SELECT s.name FROM sports_sport_age_categories sac "
                       "JOIN sports_sports s ON s.id=sac.sport_id WHERE sac.id=?", (sac_id,))
    label = row["name"] if row else sac_id
    caps = _involved_captains(sac_id)
    ts = db.now_ts()
    db.execute("DELETE FROM sports_score_votes WHERE sac_id=?", (sac_id,))
    for i, cid in enumerate(caps):
        decision = "pending"
        if mode == "disputed":
            decision = "disagree" if i == 0 else "agree"
        db.execute("INSERT INTO sports_score_votes(sac_id, captain_id, decision, ts) VALUES(?,?,?,?)",
                   (sac_id, cid, decision, ts))
        if mode == "pending":
            db.execute('INSERT INTO sports_notifications(user_id, ts, type, message, link, "read") '
                       "VALUES(?,?,?,?,?,0)",
                       (cid, ts, "approval",
                        "Results for '{}' need your approval.".format(label), "/approvals"))
    db.execute("UPDATE sports_sport_age_categories SET finalised=0, approval_status=?, "
               "status='in_progress' WHERE id=?", (mode, sac_id))
    if mode == "disputed":
        for a in db.query("SELECT id FROM sports_users WHERE roles LIKE '%admin%'"):
            db.execute('INSERT INTO sports_notifications(user_id, ts, type, message, link, "read") '
                       "VALUES(?,?,?,?,?,0)",
                       (a["id"], ts, "admin",
                        "A captain disputed the results for '{}'.".format(label), "/approvals"))


if __name__ == "__main__":
    ensure_seed()
    print("Seed complete. DB:", db.DB_PATH)
