# Sports Meet — Project Backlog

A living snapshot of what's built and what's next. Items are grouped by priority
(**Now / Next / Later**) and tagged by type and rough size.

**Tags:** `[Feat]` feature · `[Bug]` defect · `[Debt]` tech debt/refactor ·
`[Sec/Ops]` security/deployment · `[UX]` polish
**Size:** S (hours) · M (a day or two) · L (multi-day)

---

## ✅ Shipped so far

**S01** **Roles & access** — player / captain / admin; multi-role users with an active-role
  switcher; additive permissions; behaviour follows the *active* role (e.g. a captain+player
  signs up only themselves when acting as player).

**S02** **Data model** — Sport Category → Sport → Sport-Age-Category (SAC = "Sports Event");
  signups (participant↔sport); results; `score_votes`.

**S03** **Scoring** — referee role removed; captains enter scores; only involved captains agree;
  no other captains → auto-publish; any disagree → admin decides/override.

**S04** **Roster** — captain add/claim player → pending admin approval → approve/reject; admin
  assigns directly.

**S05** **Parent-child integrity** — deleting a Sport Category (→ sports) or Sport (→ events)
  routes to a manage page to move / archive / delete children first.

**S06** **Player Sports Event** — My events (Signed up | Can sign up) first & default + All-events
  calendar with age/gender filters, an Unscheduled row, and yellow/green eligibility key.

**S07** **Player Results** — My-results-first tabs; cascade filters Gender→Age→SportCat→Sport→Name.

**S08** **Admin Sports Event** — Sport added as a filter; Schedule folded into the Edit form.

**S09** **Audit trail** — created/modified by+when on all admin-managed entities, shown on edit
  pages and bulk tables.

**S10** **Platform** — in-app notifications, announcements, teams, volunteers, CSV/JSON participant
  import, JSON export, DB backup download, sample/all reset, PBKDF2 hashing, login throttle,
  session timeout, light/dark theme, mobile nav, Passenger WSGI entry.

**S11** **Team events** — events have a format (Individual / Doubles / Team); team & doubles are
  scored once per team (the team earns 10/5), results show the team with click-to-expand
  players (doubles list both names). Measured field events score 3/2/1.

**S12** **Security/ops** — CSRF tokens on all POST; secure session cookies + secret-key enforcement +
  debug-off when `SPORTS_DEBUG=0`; gunicorn + `wsgi.py` + `DEPLOY.md`.

**S13** **Quality** — committed end-to-end suite (`tests/test_app.py`, self-bootstrapping on a temp
  DB) plus an admin dashboard **Self-tests** panel that runs it on demand.

**S14** **Sample logins** — a demo player account per age category × gender (`u18_male` … `a70_female`,
  password `Demo@123`).

**S15** **House + number** — participants have a house (VA/VB/VC/A/B/C/D) and a 3-digit number;
  Admin house type has no number; unique per-house number enforced.

**S16** **Two-table auth** — `admins` + `participants` tables unified by a read-only `users` VIEW;
  FK column named `user`; non-destructive migration from old single-table model.

**S17** **Forced password change** — admin-created accounts default to password `password` and
  must change it on first login; security question/answer for forgot-password recovery.

**S18** **Team standings on dashboard** — all-team score table in the "Your score" card with
  own team row highlighted.

**S19** **Player results redesign** — My / Team / Standings tabs; approval noise hidden from players.

**S20** **Multiple formats per sport+age+gender** — dupe check includes `event_format`; same
  sport can run as Individual, Doubles, and Team simultaneously.

**S21** **Date/status guard** — New/Open status blocked on past-dated events.

**S22** **Captain lineup selection** — for Doubles/Team events captains choose which signed-up
  players represent the team; stored in `event_lineups`.

**S23** **Team Events calendar filters** — age / gender / scheduling filter toolbar on the player
  sports status view.

**S24** **Archive cascade on Sports** — archiving a sport cascades to all its child SAC rows;
  button always enabled with confirm dialog when children exist.

**S25** **Participant Actions column** — Sign-up / Edit / Release / Archive / Unarchive / Delete /
  In-use badge; archived rows dimmed; words not emojis.

**S26** **Wipe-all overhaul** — clears age categories + all event data; keeps only admin logins.

**S27** **Participants above Team Selection** — nav tab order updated in maintenance section.

**S28** **Archive/delete self-tests** — 9 new tests covering sport cascade, SAC archive, participant
  archive/delete; suite now 96/96 passing.

**S29** **Multi-program architecture** — `programs` table; per-program scoping for sport categories,
  sports, teams, announcements; `/select-program` + admin CRUD; program chip in topbar; idempotent
  backfill migration; SCHEMA_VERSION 8; `require_program` before-request hook.

**S30** **Mobile-first redesign (L10)** — bottom tab nav (5 tabs, fixed, replaces hamburger on
  ≤640px); tables → stacked label/value cards via CSS `data-label` + `::before`; 44px tap targets
  on all buttons/inputs/icon buttons; topbar compacted; full-width buttons; stacked toolbars and
  page headers; iOS safe-area padding. Zero backend changes; 96/96 tests still passing.

**S31** **Captain team-scoped views (X12) + Program lifecycle (X11)** — X12: Sports Calendar
  "All events" view gains a Team filter; captains land pre-filtered to their own team by default;
  Results "Overall" tab also defaults to captain's team. X11: Program status expanded from
  2 stages (active/archived) to a 6-stage lifecycle (Planned → New → WIP → Draft → Active →
  Completed); admin Programs page shows lifecycle progression buttons (← Prev / Next →) with
  colour-coded badges; visibility rules enforced — only Active + Completed programs visible to
  non-admins; start-date guard prevents activating before the program's start date.

**S32** **v1 feature bundle (X04/X05/X10/X15/X16/X17/X18/X19)** — Player self-service profile
  edit + withdraw (X04); Captain dashboard hub with team standings, points, and filtered week
  schedule (X05); Mobile horizontal scrollable pill-strip nav below topbar, replacing bottom
  tabs (X10); Dual-mode DB layer — `SPORTS_DB_ENGINE=mysql` activates PyMySQL with placeholder
  translation (`?`→`%s`), MySQL-compatible DDL generation, `CREATE OR REPLACE VIEW`, and guards
  on SQLite-only migration paths; `pymysql` noted in requirements (X15); `/whats-new` page with
  timestamped release log (X16); `/about` page accessible pre-login, shows config and schema
  version (X17); Change Password link added to sidebar + mobile strip for all roles (X18);
  Admin one-click "Reset PW" on Users list and participant edit page — sets password to default
  and forces change on next login (X19). 96/96 tests passing throughout.

---

## 🔴 Now — do before / at deployment

**N01** ✅ ~~CSRF protection on all POST forms~~ — done (per-session token, auto-injected).
**N02** ✅ ~~Fail-fast secret key + debug OFF by default~~ — done (enforced when `SPORTS_DEBUG=0`).
**N03** ✅ ~~gunicorn + production docs~~ — done (`wsgi.py`, `DEPLOY.md`, gunicorn in requirements).
**N04** ✅ ~~Commit the test suite as `tests/`~~ — done (`tests/test_app.py`, 96 checks, + dashboard panel).
**N05** ✅ ~~Validate the login `next=` redirect to same-site only~~ — done (rejects external/protocol-relative URLs).
**N06** ✅ ~~Harden `|first`-on-empty in templates~~ — done (`participant_form.html:19`, `participants.html:84` guarded).
**N07** ✅ ~~Remove dead code~~ — done (`sac_schedule` route + template deleted; `referee` column removed from schema; `is_referee` removed from `current_user()`).
**N08** ✅ ~~Pin exact dependency versions~~ — done (Flask/Werkzeug/Jinja2/etc. pinned in `requirements.txt`).

---

## 🟡 v1 — current sprint

**X04** ✅ ~~**Player self-service**~~ — done (`/profile/edit` for birth year/gender, per-event Withdraw button on dashboard, locked events (have results) cannot be withdrawn).
**X05** ✅ ~~**Captain tools**~~ — done (dashboard captain hub: team standings, team points, recent team results, week view filtered to team's events only).
**X10** ✅ ~~**Horizontal scrollable nav strip on mobile**~~ — done (full-width pill strip sticky below topbar at ≤640px; all role-scoped nav items scroll horizontally; sidebar and hamburger hidden on mobile; bottom-nav replaced).
**X19** ✅ ~~**Admin password reset for any user**~~ — done (one-click "Reset PW" on Users list + participant edit page; sets to default password + forces change on next login).

**X18** ✅ ~~**User-initiated password change**~~ — done (Change Password link in sidebar + mobile strip for all roles).

**A01** ✅ ~~**PWA shell**~~ — done (`manifest.json`, `sw.js` served at `/sw.js` with `Service-Worker-Allowed: /`; cache-first for `/static/*`, network-first for HTML; offline fallback to `/about`; iOS meta tags + `apple-touch-icon`; SVG icons at 192×512 — replace with PNG for full iOS home-screen quality).
**X12** ✅ ~~**Captain team-scoped views**~~ — done (S31, see below).
**X13** ✅ ~~**GitHub source control**~~ — done (git init, `.gitignore`, initial commit of 65 files pushed to https://github.com/Akilan-Ramesh/sports).

**X14** ✅ ~~**GoDaddy shared-hosting deployment**~~ — done. App is fully live at
  `akilanramesh.com/sports` — MySQL DB fully initialised (all `sports_*` tables/views created in
  the shared `akilandb` database), login works, HTTPS trusted, root domain redirects in. See
  X14b/X14c for how the two blockers along the way were resolved.

  *Deploy notes (2026-07-05):* Fixed several real MySQL-compatibility bugs found during deploy:
  SQL-comment semicolons breaking DDL parsing, composite `TEXT` primary keys needing explicit key
  lengths, reserved words `read`/`key` used as unquoted column names, literal `%` in `LIKE`
  queries colliding with PyMySQL's parameter substitution, and stale pooled MySQL connections not
  reconnecting after going idle (`conn.ping(reconnect=True)`).

**X14b** ✅ ~~**Trusted HTTPS certificate**~~ — done (2026-07-07). GoDaddy's cPanel AutoSSL turned
  out to be unavailable on this hosting plan (`uapi SSL start_autossl_check` → *"You do not have
  the feature 'autossl'"*). Worked around it by installing `certbot` via pip (user-space, no root)
  and issuing a real Let's Encrypt certificate via HTTP-01 webroot validation against
  `~/public_html`, then installing it into cPanel via `uapi SSL install_ssl`. Site now serves a
  properly trusted cert (expires 2026-10-05). Also fixed the bare domain root
  (`akilanramesh.com/`) showing GoDaddy's default "Coming Soon" placeholder — added a scoped
  `.htaccess` rule (`^$` → `/sports/`) that only redirects the exact root path, leaving the
  account's other project subdirectories (`medtrack`, `skilltrack`, `cdta`, `cdta-workver`,
  `dbdemo`, `abhishekjobs`) untouched.

  *Follow-up — X14d (below, v2 backlog):* this cert was issued manually, not via AutoSSL, so it
  won't auto-renew before expiry — needs either a renewal cron job or GoDaddy actually enabling
  the account's `autossl` feature.

**X14c** ✅ ~~**Sync live hotfixes back into git**~~ — done (2026-07-07). Confirmed all server-side
  hotfixes (MySQL reserved-word quoting, `%`-escaping, `click` version pin, demo-credential hint
  removal) were already captured in commits `a0c9f8e`/`6244af2`; fast-forwarded the server
  (`git reset --hard origin/main`) to reconcile cleanly — clean `git pull` deploys work again.
  `DEPLOY.md`'s stale `admin`/`password` credential doc fix folded into X14d.

**X15** ✅ ~~**MySQL dual-mode backend**~~ — done (`SPORTS_DB_ENGINE=mysql` activates PyMySQL; `?`→`%s` translation; MySQL DDL generator; `CREATE OR REPLACE VIEW`; SQLite migration paths guarded).

**X16** ✅ ~~**What's New page**~~ — done (`/whats-new`, login-required, timestamped release log in-code).

**X17** ✅ ~~**About page**~~ — done (`/about`, public, shows app name/features/contact/schema version).

**X25** `[Bug/Feat]` **Age calculation — configurable reference date + test coverage** — S/M

  *Problem:* `domain.age_from_birth_year()` computes age as `date.today().year - birth_year` —
  purely calendar-year-based, with no configurable reference date. Age category (and eligibility)
  silently shifts on Jan 1 every year regardless of when the actual program/meet runs, and there's
  no way to set a fixed "age as of" cutoff date the way most youth sports federations require
  (e.g. age as of the program's start date, not whenever someone happens to check). No test
  coverage exists for this logic at all (`age_from_birth_year`/`category_for_birth_year`/
  `derive_category` in `domain.py` — zero references in `tests/test_app.py`).

  *Solution:* Add an "Age calculation start date" field to the Settings page (`admin_settings()` /
  `templates/settings.html`) stored via `db.set_setting()`, used as the reference date in place of
  `date.today()` when computing age/category (`age_from_birth_year`, `category_for_birth_year`).
  Add self-tests covering: birthday-adjacent edge cases (day before/after the cutoff date crossing
  a category boundary), leap-year birth years, missing/invalid birth year input, and category
  boundary values (exact `min_age`/`max_age` matches).

---

## 🔵 v2 — after v1

**X11** `[Feat]` **Program lifecycle — status + date enforcement** — M

  *Problem:* Programs today have a basic status field and start/end dates but no enforced
  lifecycle. An admin has no way to signal "this program is being set up" vs "this is open to
  players" vs "this is over." Players can see programs that aren't ready; completed programs
  behave the same as active ones.

  *Solution:* Replace the current status with a six-stage lifecycle, each with distinct
  visibility and edit rules:

  | Status | Who set it | What it means | Who can see | Who can edit |
  |---|---|---|---|---|
  | **Planned** | Admin | Future program, not yet being configured | Admin only | Admin |
  | **New** | Admin | Program decided; setup not started | Admin only | Admin |
  | **WIP** | Admin | Admin actively building (adding sports, teams, events) | Admin only | Admin |
  | **Draft** | Admin | Setup complete; open for preview by selected members before public launch | Admin + invited preview members | Admin |
  | **Active** | Admin | Live — visible and open to all players/participants | Everyone | Admin + captains (own data) |
  | **Completed** | Admin | Event over; full results visible to everyone, no new entries | Everyone (read-only) | Admin only |

  Start/end dates are already stored; add validation so a program can only go **Active** on or
  after its start date, and auto-suggest **Completed** when the end date passes. Add status
  transition buttons on the program edit page (no free-text status field).

**X14d** ✅ ~~**HTTPS cert auto-renewal**~~ — done (2026-07-09). Added `~/letsencrypt/reinstall-cert.sh`
  on the server (re-runs `uapi SSL install_ssl` with the renewed cert files) and a daily cron job
  (`0 3 * * * certbot renew ... --deploy-hook reinstall-cert.sh`) — a no-op most days, only
  actually renews (and triggers the reinstall) once the cert is within 30 days of its 2026-10-05
  expiry. Verified end-to-end with both `certbot renew --dry-run` and a direct run of the reinstall
  script (confirmed idempotent — cPanel reported *"already installed... no changes"*). No manual
  action needed going forward.

  *Remaining — `DEPLOY.md` cleanup:* Stage 6/8 still document the stale `admin`/`password` login
  (actual seeded credential is `admin`/`nicknick`) — small doc fix, not yet done.

**X02** `[Feat]` **Audit / history viewer UI** — surface the created/modified data + the `audit`
  activity log (no browsing UI today). — M
**X07** `[Sec/Ops]` **Pagination + indexes** on large lists (participants/results/users) — loads
  all rows today. — M
**X08** `[Sec/Ops]` **Persistent login throttle** (DB-backed; survives restart) + optional lockout.
  Currently in-memory. — M
**X09** `[Debt]` **Structured logging** to file + raise/rotate audit retention (capped at 200). — S/M
**X20** `[Debt]` **Self-tests panel timeout on shared hosting** — the admin Self-tests panel spins
  up its own throwaway test server as a subprocess (`tests/test_app.py`), which times out with
  "server did not start in time" on the GoDaddy shared/CloudLinux host (likely slower
  process-spawning under the account's resource governor). Diagnostic-only, not a production bug —
  possibly fixable with a longer startup timeout in `start_server()`. — S
**L02** `[Sec/Ops]` Real **migration tool** (Alembic) for non-additive schema changes. — M/L
**L03** `[Sec/Ops]` Automated **backups + retention**; restrict DB file perms / encrypt at rest. — M
**X21** ✅ ~~**Users & Roles page — filters + Age Category column**~~ — done (2026-07-09). Toolbar
  with Name (free-text search, not a dropdown), Age Category, Role, Team, and Status filters;
  Age Category column added right after Name (looked up from `sports_participants` per-user, since
  the `sports_users` VIEW doesn't expose it — no schema change needed).
**X22** `[UX]` **Remove the "Reset PW" button** from the Users &amp; Roles page — the one-click
  destructive reset-to-default action (next to "Set PW", which lets an admin set a specific
  password directly) is redundant/risky; keep "Set PW" only. — S

**X23** ✅ ~~**"Internal Server Error" / CSRF errors shown after every create/update**~~ — root
  cause found and fixed (2026-07-13).

  *Investigated (2026-07-09):* confirmed via the production DB that `register()`/`user_new()`
  complete 100% successfully server-side — the account row, audit stamp, and activity-log entry
  are all written correctly. Ruled out Python exceptions, slow PBKDF2 hashing (~60ms/hash measured
  directly on the server), and `notify_roles()`'s overhead. Applied a real but ultimately unrelated
  efficiency fix (`notify_roles()` was doing a full table scan **per role** — now one scan total).

  *Root cause found (2026-07-13):* the error was showing on **every** create/update, not
  intermittently — reproduced a clean, minimal repro (fresh `GET /login` → immediately `POST
  /login` with that exact CSRF token) failing with "CSRF token missing or invalid" using `curl`
  directly, isolating it to session/CSRF signature verification itself, not infrastructure timing.
  Compared SHA-256 hashes of `SPORTS_SECRET_KEY` (values never exposed) between the live running
  Passenger worker's actual process environment (`/proc/<pid>/environ`) and the current
  `cloudlinux-selector` config — **they didn't match**, even immediately after a forced restart
  with a freshly-spawned worker process. The running app had been signing/verifying sessions with
  a stale secret key that no longer matched deploys, causing CSRF/session verification to fail
  whenever a request's session was established under one key and checked against another.
  *Fixed:* regenerated `SPORTS_SECRET_KEY` and re-applied it via cPanel's Setup Python App UI
  (user-driven, so the new key never had to pass through the assistant), then restarted. This
  invalidated all existing sessions (expected, one-time) but resolved the underlying drift.

**X24** ✅ ~~**Sports Events leaked across programs**~~ — done (2026-07-13). Creating a new
  program (e.g. "tennis") still showed the original Sports Meet's Sports Events (100m Freestyle,
  100m Sprint, etc.) — `teams()`, `sport_categories()`, and `all_sports()` were already correctly
  scoped by `program_id`, but the shared `_SAC_SELECT` query (backing `load_sacs()`/`get_sac()`,
  used by 15+ call sites: dashboard, sign-up, results, approvals, callsheet, etc.) joined
  `sports_sport_age_categories` to `sports_sports` without ever filtering by the sport's
  `program_id`. Fixed by baking `WHERE s.program_id=?` into the shared query itself, scoping every
  caller automatically rather than patching each site individually. Verified locally: a fresh
  program now shows zero Sports Events, teams, sport categories, or sports until you add your own.

**X26** ✅ ~~**Age Categories leaked across programs**~~ — done (2026-07-15). Same class of bug as
  X24, different subsystem: age categories (`config()["categories"]`) were stored under a single
  global settings key (`"categories"`), shared by every program, unlike teams/sports/sport
  categories which already had `program_id` columns. Fixed by making the settings key per-program
  (`"categories_" + program_id`, via `domain.get_config(program_id)`) — every existing reader
  already went through `config()["categories"]`, so this scoped all of them at once. Also fixed two
  related bugs surfaced by the same code path: `_recompute_all_categories()` was recomputing every
  participant in the database (any program) using whichever program's age bands were just edited —
  risked corrupting other programs' participant categories; now scoped via the participant's team's
  `program_id`. The Age Categories page's "in use" counts were similarly unscoped; now joined
  through `sports_teams`/`sports_sports` to the current program. `_wipe_all()` and `seed.py`'s
  initial seed updated to match the new per-program key convention.

---

## 🟢 Later — longer-term / nice-to-have

**X01** `[Feat]` **CSV / PDF exports** — results by event/team/participant, rosters, schedules
  (only JSON export + browser-print call sheet exist). — M
**X03** `[Feat]` **Email notifications** — actually send (SMTP); `sender_email` setting + user
  emails exist but nothing is sent. — M/L
**L01** `[Debt]` Split the **`app.py` monolith** (~2.8k lines) into blueprints. — L
**L04** `[Feat]` **Roster size limits** (configurable per-team caps). — S
**L05** `[Feat]` Surface the existing **`specifics`/notes** field per event; capture a **dispute
  reason** when a captain disagrees. — S/M
**L06** `[Feat]` **Email verification** on self-register; notification archive + digest. — M
**L07** `[UX]` Inline field validation, unsaved-changes warning, loading states. — M
**L08** `[UX]` **Accessibility** pass: skip links, focus management, colour-contrast audit. — M
**L09** `[Feat]` **i18n / localisation** scaffolding (all copy is English today). — L
**L10** ✅ ~~**Mobile-first redesign**~~ — done (S30).

---

**A02** `[Feat]` **Native app** — React Native or Flutter wrapper around the existing API, or a
  full rewrite using the web app as the reference UX. Scope TBD once web v2 ships. — L

---

---

## 🗄️ DB Changes Log

Tracks schema-level changes and their migration status across environments.
**Rule:** local SQLite and production MySQL must stay in sync. Always apply locally first, then
script the MySQL equivalent before deploying.

| # | Change | SCHEMA_VERSION | Local (SQLite) | Prod (MySQL) |
|---|--------|---------------|----------------|--------------|
| DB-01 | Initial schema (programs, teams, admins, participants, sports, sport\_categories, sport\_age\_categories, signups, results, score\_votes, event\_lineups, announcements, notifications, audit, settings) | v1 | ✅ Done | ⏳ Pending (pre-MySQL migration) |
| DB-02 | `users` VIEW created from `admins` UNION `participants` | v7 | ✅ Done | ⏳ Pending |
| DB-03 | `programs` table; per-program scoping columns added | v8 | ✅ Done | ⏳ Pending |
| DB-04 | All tables, views, and named indexes prefixed with `sports_` (e.g. `participants` → `sports_participants`, `ix_results_sac` → `sports_ix_results_sac`); `users` VIEW → `sports_users`; old unprefixed tables/indexes dropped from local DB | v9 | ✅ Done | ✅ Ready — `init_db()` auto-creates all tables with prefix when `SPORTS_DB_ENGINE=mysql` |

*Last updated: 2026-07-13. Re-tier items freely as priorities change.*
