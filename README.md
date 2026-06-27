# Sports Meet Management

A self-hosted web app to run a multi-event community sports meet end-to-end —
participants, teams, events, live scoring, and a live team leaderboard.

**Python + Flask**, a **SQLite database** (standard-library `sqlite3`, single
file, zero config), **no Node.js** — so it deploys to GoDaddy Starter shared
hosting (cPanel + Passenger) with nothing to provision.

## Run locally

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python app.py
```

Then open <http://127.0.0.1:5000>. Seed data is created automatically on first run.

Or just: `./run.sh`

## Accounts

Anyone except admin can **self-register** at `/register`. Players register
freely; captains and referees must also enter the shared **common role
password** (admin sets these in Configuration). Accounts are active immediately.

Seeded demo logins:

| Role | Username | Password |
|------|----------|----------|
| Admin | `admin` | `Admin@123` |
| Captain (Smashers) | `captain_smashers` | `Smash@123` |
| Captain (Hammers) | `captain_hammers` | `Hammer@123` |
| Captain (Warriors) | `captain_warriors` | `Warrior@123` |
| Referee | `referee1` | `Ref@1234` |

Common role passwords (for self-registration): captain `Captain@2026`,
referee `Referee@2026`.

**Change all of these before any real deployment** (Admin → Users / Configuration).

## What's implemented

- **4 roles** with strict server-side permission enforcement on every request
  (Player read-only · Captain manages own team · Referee scores · Admin everything).
- **Self-registration** for every role except admin; captains/referees gated by
  an admin-set common role password.
- **Auth**: PBKDF2-SHA256 password hashing, 8+/upper/lower/number/special rules
  with live validation, 30-min idle sessions, 24-hour "remember me",
  5-failures/15-min login rate limiting.
- **Teams** (3, editable name/colour), age categories × Boys/Girls
  (admin-editable bands), category auto-derived from age.
- **Sports in categories**: Track & Field, Water Sports, Ball Sports, Others —
  admin renames categories, adds new ones, and adds sports within each; each
  sport has a date, time, station, age categories and scoring mode.
- **Participants**: minimal CRUD (name, team, division, age → category),
  captain scoped to own team, CSV/JSON import, archive-on-delete when results exist.
- **Sports Sign-up**: a dedicated section where each participant's sports are
  chosen from a searchable, category-grouped picker.
- **Volunteers**: a separate section to add/remove volunteers from the roster.
- **Schedule**: dashboard shows the **upcoming week**; the full schedule lists
  the rest, grouped by date, with printable call sheets.
- **Scoring**: referee score entry, placement / measured (best-of-N) /
  participation modes, measured events auto-rank by best round, finalise/reopen.
- **Results**: live individual results and points (configurable point values).
  *(The leaderboard has been removed.)*
- **Admin**: dashboard stats + recent-activity log, user management (single admin
  guaranteed), configuration (points, teams, age & sport categories, role
  passwords), JSON export, full database backup (.db download).
- **SQLite**: a single `data/sportsmeet.db` file; schema and seed data are
  created automatically on first run.

## Deploy to GoDaddy (cPanel)

1. Upload the project (excluding `venv/`) to a folder above `public_html`, or
   set `SPORTS_DATA_DIR` to a path outside the web root.
2. cPanel → **Setup Python App** → Python 3.8+, set the application startup file
   to `passenger_wsgi.py`.
3. Install requirements (`pip install -r requirements.txt`) via the app's venv.
4. Set environment variable `SPORTS_SECRET_KEY` to a long random value.
5. Restart the app.

## Files

```
app.py            Flask app + routes (role enforcement, scoring, admin)
db.py             SQLite layer: connection, schema, settings/config helpers
domain.py         Age/sport categories, scoring, config
security.py       Password hashing + strength rules
seed.py           Schema creation + first-run seed data
passenger_wsgi.py cPanel/Passenger entry point
templates/        Jinja2 templates (mobile-first, print-friendly)
static/style.css  Responsive stylesheet
data/             sportsmeet.db (created at runtime; .htaccess-protected)
```
