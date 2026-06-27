"""Domain logic: roles, age/sport categories, per-event points, scoring."""
from datetime import date

import db

# --- roles -----------------------------------------------------------------
ALL_ROLES = ["player", "captain", "admin"]
STAFF_ROLES = {"captain", "admin"}
# Roles a user can pick when self-registering (admin can never be self-granted).
SELF_REGISTER_ROLES = ["player", "captain"]
# Used to pick a sensible default "acting" role when a user has several.
ROLE_PRIORITY = ["admin", "captain", "player"]


def default_active_role(roles):
    for r in ROLE_PRIORITY:
        if r in roles:
            return r
    return roles[0] if roles else "player"


def roles_of(user):
    if not user:
        return []
    r = user.get("roles")
    if isinstance(r, list):
        return r
    return db.loads(r, []) or []


def has_role(user, *roles):
    return bool(set(roles_of(user)) & set(roles))


def is_staff(user):
    return bool(set(roles_of(user)) & STAFF_ROLES)


# --- age categories --------------------------------------------------------
DEFAULT_CATEGORIES = [
    {"id": "U9", "name": "Under 9", "min_age": 0, "max_age": 8},
    {"id": "U13", "name": "Under 13", "min_age": 9, "max_age": 12},
    {"id": "U18", "name": "Under 18", "min_age": 13, "max_age": 17},
    {"id": "U30", "name": "Under 30", "min_age": 18, "max_age": 29},
    {"id": "U50", "name": "Under 50", "min_age": 30, "max_age": 49},
    {"id": "U70", "name": "Under 70", "min_age": 50, "max_age": 69},
    {"id": "A70", "name": "Above 70", "min_age": 70, "max_age": 200},
]

DIVISIONS = ["Male", "Female"]   # shown in the UI as "Gender"

# Houses a participant belongs to (separate from competing teams). Players pick a
# house + a 3-digit number (unique within the house). "Admin" is an account-type
# option only on the admin-facing create form — it makes an admin login (no number).
HOUSES = ["VA", "VB", "VC", "A", "B", "C", "D"]
ADMIN_HOUSE = "Admin"

DEFAULT_PASSWORD = "password"   # auto-set for admin-created accounts (must change)

# Security question presets for password recovery.
SECURITY_QUESTIONS = [
    "What is your parent's name?",
    "What is the name of your first school?",
    "What is your favourite sport?",
    "What city were you born in?",
]
SCORING_MODES = ["placement", "measured", "participation"]

# How an event is contested: one person, a pair, or a whole team. Team/doubles
# events are scored once per team (the team gets the points).
EVENT_FORMATS = ["individual", "doubles", "team"]
EVENT_FORMAT_LABELS = {
    "individual": "Individual",
    "doubles": "Doubles (pairs)",
    "team": "Team",
}


def is_team_format(fmt):
    return fmt in ("team", "doubles")


def default_points_for(event_format, scoring_mode):
    """Sensible default {place: points} + place-count for a new event.

    Team & doubles: 10 / 5 (two places).   Measured field events: 3 / 2 / 1.
    Individual races / participation: 5 / 3 / 1."""
    if is_team_format(event_format):
        return {"1": 10, "2": 5}, 2
    if scoring_mode == "measured":
        return {"1": 3, "2": 2, "3": 1}, 3
    return {"1": 5, "2": 3, "3": 1}, 3

# Human explanations of each scoring mode (shown in the sport form).
SCORING_MODE_HELP = {
    "placement": "Referee records each competitor's finishing position (1st, 2nd, 3rd…). "
                 "Points are awarded by place. Best for races and head-to-head events.",
    "measured": "Each competitor gets a measurement (distance, height or time) across the "
                "set number of rounds. The system auto-ranks everyone by their best result, "
                "then awards points by rank. Best for throws, jumps and timed events.",
    "participation": "Everyone who takes part is marked as present; the top finishers can still "
                     "be given places & points. Best for fun games where taking part is what counts.",
}

# Sport lifecycle statuses: key -> (label, badge-class).
EVENT_STATUSES = [
    ("new", "New", "pill-draft"),
    ("registration_open", "Open to register", "pill-open"),
    ("registration_closed", "Registration closed", "pill-pending"),
    ("scheduled", "Scheduled", "pill-open"),
    ("in_progress", "In progress", "pill-pending"),
    ("completed", "Completed", "pill-approved"),
    ("postponed", "Postponed", "pill-pending"),
    ("cancelled", "Cancelled", "pill-disputed"),
]
STATUS_META = {k: (label, cls) for (k, label, cls) in EVENT_STATUSES}

PROGRAM_STATUSES = [
    ("planned",   "Planned"),
    ("new",       "New"),
    ("wip",       "WIP"),
    ("draft",     "Draft"),
    ("active",    "Active"),
    ("completed", "Completed"),
]

# Statuses non-admin users may see and access
PROGRAM_VISIBLE_STATUSES = ("active", "completed")

# Ordered lifecycle for Next / Prev transitions
PROGRAM_STATUS_ORDER = ["planned", "new", "wip", "draft", "active", "completed"]

PROGRAM_STATUS_BADGE = {
    "planned":   "pill-draft",
    "new":       "pill-draft",
    "wip":       "pill-pending",
    "draft":     "pill-pending",
    "active":    "pill-open",
    "completed": "pill-approved",
}

DEFAULT_SPORT_CATEGORIES = [
    {"id": "track_field", "name": "Track & Field", "sort": 1},
    {"id": "water_sports", "name": "Water Sports", "sort": 2},
    {"id": "ball_sports", "name": "Ball Sports", "sort": 3},
    {"id": "others", "name": "Others", "sort": 4},
]

# Default per-event points; each event may override (e.g. 10/5 for team games).
DEFAULT_POINTS = {"1": 5, "2": 3, "3": 1}


def get_config():
    return {
        "event_name": db.get_setting("event_name", "Community Sports Meet 2026"),
        "points": db.get_setting("points", DEFAULT_POINTS),
        "count_in_progress": db.get_setting("count_in_progress", False),
        "categories": db.get_setting("categories", DEFAULT_CATEGORIES),
        "sender_email": db.get_setting("sender_email", ""),
    }


def current_year():
    return date.today().year


def age_from_birth_year(birth_year):
    try:
        return current_year() - int(birth_year)
    except (TypeError, ValueError):
        return None


def derive_category(age, categories):
    try:
        age = int(age)
    except (TypeError, ValueError):
        return None
    for c in categories:
        if c["min_age"] <= age <= c["max_age"]:
            return c["id"]
    return None


def category_for_birth_year(birth_year, categories):
    return derive_category(age_from_birth_year(birth_year), categories)


def category_name(cat_id, categories):
    for c in categories:
        if c["id"] == cat_id:
            return c["name"]
    return cat_id or "-"


def team_name(team_id, teams):
    for t in teams:
        if t["id"] == team_id:
            return t["name"]
    return team_id or "Unassigned"


def sport_category_name(cat_id, sport_categories):
    for c in sport_categories:
        if c["id"] == cat_id:
            return c["name"]
    return cat_id or "Others"


# --- per-event points ------------------------------------------------------

def event_points_map(item, config=None):
    """Return the {place: points} map for a SAC/sport (its own, or the default)."""
    pts = db.loads(item.get("points"), None) if item.get("points") else None
    if not pts:
        pts = (config or get_config()).get("points", DEFAULT_POINTS)
    # normalise keys to str
    return {str(k): int(v) for k, v in pts.items()}


def points_for_place(place, item, config=None):
    if not place:
        return 0
    return event_points_map(item, config).get(str(place), 0)


def parse_points_string(s, default_places=3):
    """Parse '5,3,1' or '10/5' into ({'1':5,...}, places). Empty -> default."""
    if not s or not s.strip():
        pts = DEFAULT_POINTS
        return {k: int(v) for k, v in pts.items()}, default_places
    parts = [p for p in s.replace("/", ",").replace(" ", ",").split(",") if p != ""]
    pts = {}
    for i, val in enumerate(parts, start=1):
        try:
            pts[str(i)] = int(float(val))
        except ValueError:
            pts[str(i)] = 0
    return pts, len(pts)


def recompute_sac_places(sac, config=None):
    """Recompute placement & points for all results of one SAC, in the DB."""
    config = config or get_config()
    ev_results = db.query("SELECT * FROM sports_results WHERE sac_id=?", (sac["id"],))
    mode = sac.get("scoring_mode", "placement")

    if mode == "measured":
        for r in ev_results:
            rounds = db.loads(r.get("rounds"), []) or []
            vals = [v for v in rounds if v not in (None, "")]
            r["best"] = max((float(v) for v in vals), default=None)
        ranked = sorted([r for r in ev_results if r.get("best") is not None],
                        key=lambda r: r["best"], reverse=True)
        for i, r in enumerate(ranked, start=1):
            r["place"] = i
        for r in ev_results:
            if r.get("best") is None:
                r["place"] = None

    places = int(sac.get("places") or 3)
    for r in ev_results:
        place = r.get("place")
        # Only award points within the configured number of places.
        if place and place <= places:
            r["points"] = points_for_place(place, sac, config)
        else:
            r["points"] = 0
        db.execute("UPDATE sports_results SET place=?, points=?, best=? WHERE id=?",
                   (r.get("place"), r["points"], r.get("best"), r["id"]))
    return ev_results
