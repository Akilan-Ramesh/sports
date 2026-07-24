# Sports Meet Management — Rebuild Specification

**Purpose of this document:** a complete, stack-agnostic functional specification for rebuilding
this application from scratch, on any technology stack, without referring to or copying the
existing source code. It describes *what the system does* — data model, business rules, workflows,
screens, and operational requirements — not *how the current implementation does it*. Where the
current implementation has a known bug, gap, or design mistake, this spec calls it out explicitly
and specifies the **corrected** behavior to build instead.

A short "Known gaps to fix" list and a "Past mistakes to avoid" checklist are included near the end
— read those before starting, since they save re-discovering the same problems.

---

## Table of Contents

1. [Product Overview](#1-product-overview)
2. [Roles & Permissions](#2-roles--permissions)
3. [Data Model](#3-data-model)
4. [Core Domain Rules](#4-core-domain-rules)
5. [Authentication & Accounts](#5-authentication--accounts)
6. [Programs (Multi-Tenancy)](#6-programs-multi-tenancy)
7. [Participants & Rosters](#7-participants--rosters)
8. [Sports Catalogue & Events](#8-sports-catalogue--events)
9. [Sign-ups & Eligibility](#9-sign-ups--eligibility)
10. [Scoring & Results Workflow](#10-scoring--results-workflow)
11. [Team Standings](#11-team-standings)
12. [Results Viewing & Call Sheets](#12-results-viewing--call-sheets)
13. [Notifications & Announcements](#13-notifications--announcements)
14. [Admin Tooling](#14-admin-tooling)
15. [The "In Use" / Archive-vs-Delete Pattern](#15-the-in-use--archive-vs-delete-pattern)
16. [UI & Navigation Structure](#16-ui--navigation-structure)
17. [Non-Functional Requirements](#17-non-functional-requirements)
18. [Known Gaps in the Current App to Fix in the Rebuild](#18-known-gaps-in-the-current-app-to-fix-in-the-rebuild)
19. [Past Mistakes to Avoid](#19-past-mistakes-to-avoid)
20. [Deployment & Operations](#20-deployment--operations)

---

## 1. Product Overview

A web application for running a multi-team sports meet/competition: managing participants, teams,
a catalogue of sports and scheduled events, player sign-ups, score entry with a captain-approval
workflow, live standings, and admin tooling — usable on mobile devices in the field, installable as
a PWA. Supports running **multiple independent meets/editions** ("Programs") from one deployment,
each with its own sports, teams, categories, and announcements.

Three user roles — **player**, **captain**, **admin** — with additive, multi-role accounts (one
person can hold more than one role) and a session-level "acting role" that determines which
navigation and default views they see at any moment, independent of which roles they actually hold.

---

## 2. Roles & Permissions

- **player** — signs up for sports, views their own results/team standings, manages their own
  profile.
- **captain** — everything a player can do, plus: manages their own team's roster (claim/release
  players, subject to admin approval for claims), enters scores for events their team is in, votes
  to approve/dispute other captains' score submissions, views their team's volunteers.
- **admin** — full access to everything: all participant/team/program/sports-catalogue management,
  score override/publish authority, settings, data import/export/reset, user account management.

**Rules:**
- Roles are additive and stored as a list per account — a person can be player+captain, or
  captain+admin, etc.
- `admin` can **never** be self-granted at registration — only an existing admin can grant it.
- A multi-role account has one **active/acting role** at a time (session-scoped, switchable without
  logging out via a role switcher in the UI). Nearly all page content and default views branch on
  the *acting* role. **Permission checks, however, are based on the full set of roles the account
  holds, not just the acting role** — e.g. a captain+admin account can still reach admin-only pages
  while acting as captain; only the navigation/default-view presentation changes with the acting
  role.
- Default acting role priority when one must be picked automatically: **admin > captain > player**
  (highest-priority held role wins).
- A captain must have a team assigned to exercise captain-specific actions (score entry, roster
  claims) — a captain account with no team is effectively inert for those actions.

---

## 3. Data Model

*(Entity names below are conceptual — a rebuild is free to name/prefix underlying tables however
fits its stack. Composite/conditional uniqueness rules and the exact list of fields are the actual
requirement.)*

### 3.1 Program
The top-level tenancy container — one row per "meet" (e.g. a yearly edition). Every sport-catalogue
entity belongs to exactly one Program.

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| id | id | No (PK) | |
| name | text | No | Display name |
| description | text | Yes | |
| has_teams | boolean | No (default true) | Whether this program uses competing teams at all |
| status | enum | No (default "active") | See §4.5 lifecycle |
| start_date | date | Yes | |
| end_date | date | Yes | |
| audit fields | — | — | created_by/at, modified_by/at |
| sample | boolean | No (default false) | Marks demo/seed rows for easy bulk-identification/removal |

### 3.2 Team
A competing organizational team within one Program.

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| id | id | No (PK) | |
| name | text | No | |
| colour | text | Yes | For UI badges |
| program_id | id (FK → Program) | No | |
| audit + sample fields | — | — | |

### 3.3 Account (login identity)

**Design decision for the rebuild:** the original implementation split this into two physically
different row-shapes — a pure-staff "Admin" table and a "Participant" table that doubles as both a
roster entry and (optionally) a login — unified only by a read-only view for generic lookups. This
was a source of real friction (see §19). **The rebuild should use a single Account entity** with
role-driven optional fields, rather than reproducing the two-table split. The fields below describe
what an account needs to carry; whether a player-only field is nullable-on-the-same-row or lives on
a related "player profile" sub-record is an implementation choice — the constraint that matters is
"one account, one set of login credentials, that may additionally carry player-specific attributes
when the account holds the player role."

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| id | id | No (PK) | Stable login identity, referenced by notifications, audit, score votes |
| username | text | No, **unique** (case-insensitive) | |
| email | text | Yes | Alternate login identifier; contact address |
| name | text | No | Display name |
| password_hash | text | No | See §17.2 |
| roles | list of enum | No | One or more of player/captain/admin |
| security_question | text | Yes | Chosen preset (§4.6) |
| security_answer_hash | text | Yes | Normalized (trim+lowercase) before hashing |
| must_change_password | boolean | No (default false) | Forces a change on next login |
| disabled | boolean | No (default false) | Soft-disable |
| last_login_at | timestamp | Yes | |
| notify_preferences | JSON | No (default `{}`) | Mute-all flag + per-type mute list, §13.1 |
| team_id | id (FK → Team) | Yes | Only meaningful for captain-role accounts |
| division | enum (Male/Female) | Yes | Only meaningful for player-role accounts |
| birth_year | integer | Yes | Only meaningful for player-role accounts |
| category_id | id | Yes | Cached/derived age category (player-role); recomputed whenever age-category bands or birth_year change |
| house | enum | Yes | Only meaningful for player-role accounts, see §4.4 |
| house_number | text (3 digits) | Yes | Only meaningful for player-role accounts |
| is_roster_entry | boolean | No (default true for players) | True = a real competitor who appears on rosters/eligible to sign up; false = a login-only account (e.g. captain with no competing entry) |
| is_volunteer | boolean | No (default false) | |
| archived | boolean | No (default false) | Soft-delete |
| pending_team_id | id (FK → Team) | Yes | An outstanding, not-yet-approved team-transfer request |
| **program_id** | id (FK → Program) | **No, once assigned** | **New/corrected field — see §18.1: every account with the player or captain role must have a direct, mandatory program association, set at creation time, not derived indirectly through team membership.** |
| audit + sample fields | — | — | |

**Uniqueness constraints:**
- `username` unique (case-insensitive) across all accounts.
- `(house, house_number)` unique together, but **only enforced when both are non-null** (a
  conditional/partial constraint) — accounts with no house/number (e.g. admin-type accounts) must
  not collide with each other under a naive always-on constraint.

### 3.4 Sport Category
A grouping/classification for sports (e.g. "Track & Field", "Water Sports"), scoped per Program.

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| id | id | No (PK) | |
| name | text | No, unique **within its program** | |
| sort_order | integer | No (default 0) | Display ordering |
| program_id | id (FK → Program) | No | |
| audit + sample fields | — | — | |

### 3.5 Sport
The master catalogue entry for a sport being organized, scoped to a Program. Age/gender-specific
scheduling and scoring live one level down (§3.6).

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| id | id | No (PK) | |
| category_id | id (FK → Sport Category) | Yes | |
| name | text | No | |
| archived | boolean | No (default false) | |
| program_id | id (FK → Program) | No | |
| audit + sample fields | — | — | |

**Uniqueness:** `(name, program_id)` composite — the same sport name may exist in different
programs, but not twice within one program.

### 3.6 Sport Event ("Sport Age Category" / SAC)
The actual scheduled/scoreable unit: one Sport, restricted to one age category + gender, with its
own scoring configuration, schedule slot, and lifecycle status.

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| id | id | No (PK) | |
| sport_id | id (FK → Sport) | No | |
| age_category_id | id | Yes | Null = "all ages" wildcard |
| gender | enum | Yes | Null = "mixed"/no restriction |
| scoring_mode | enum | No (default "placement") | placement / measured / participation, §4.2 |
| event_format | enum | No (default "individual") | individual / doubles / team, §4.3 |
| rounds | integer | No (default 3) | Measured-mode attempt count |
| points_map | JSON (place→points) | Yes | Overrides the program/site default when set |
| places_paid | integer | No (default 3) | How many finishing places actually earn points |
| date | date | Yes | |
| time_slot | text | Yes | |
| location | text | Yes | |
| notes | text | Yes | Free-text event-specific notes |
| finalised | boolean | No (default false) | Results locked/published |
| approval_status | enum | No (default "draft") | draft / pending / disputed / approved, §10.5 |
| status | enum | No (default "new") | Scheduling-lifecycle status, §4.7 |
| archived | boolean | No (default false) | |
| audit + sample fields | — | — | |

*Should be indexed for: lookup by sport_id, sort/filter by date, filter by status.*

### 3.7 Score Vote
One captain's approval/dispute decision on one Sport Event's results.

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| sac_id | id (FK → Sport Event) | No | Part of composite PK |
| captain_account_id | id (FK → Account) | No | Part of composite PK |
| decision | enum | No (default "pending") | pending / agree / disagree |
| decided_at | timestamp | Yes | |

*Primary key: (sac_id, captain_account_id). Index for lookup by sac_id.*

### 3.8 Sign-up
A participant's registration for a Sport (catalogue-level).

| Field | Type | Nullable |
|---|---|---|
| account_id | id (FK → Account) | No — part of composite PK |
| sport_id | id (FK → Sport) | No — part of composite PK |

*Primary key: (account_id, sport_id). Index for lookup by sport_id.*

### 3.9 Event Lineup
For doubles/team-format events: the captain-curated competing line-up for one Team in one Sport
Event.

| Field | Type | Nullable |
|---|---|---|
| sac_id | id (FK → Sport Event) | No — part of composite PK |
| team_id | id (FK → Team) | No — part of composite PK |
| member_account_ids | list of ids | No (default empty list) | Must all belong to that team |

*Primary key: (sac_id, team_id). **Business rule: absence of a row means "default to every
eligible signed-up player of that team"** — an unset lineup is not the same as an empty one.*

### 3.10 Result
The score/placement record — one row per participant (or per team, for team-format events) per
Sport Event.

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| id | id | No (PK) | |
| sac_id | id (FK → Sport Event) | No | |
| participant_ref | id | No | An Account id, or (team-format events) a Team id |
| place | integer | Yes | Null until scored/computed |
| points | integer | No (default 0) | Derived from place + the event's points map |
| best | decimal | Yes | Best measurement across rounds (measured mode only) |
| rounds | list of decimals | Yes | Individual round measurements (measured mode only) |
| participated | boolean | No (default false) | Present/participated flag (participation mode) |
| history | JSON | Yes | Change history of edits to this result |

*Should be indexed for: lookup by sac_id (leaderboards/recompute), lookup by participant_ref (a
competitor's full history).*

### 3.11 Announcement

| Field | Type | Nullable |
|---|---|---|
| id | id | No (PK) |
| published_at | timestamp | Yes |
| title | text | Yes |
| body | text | Yes |
| visible | boolean | No (default true) |
| program_id | id (FK → Program) | No |
| audit + sample fields | — | — |

### 3.12 Notification

| Field | Type | Nullable |
|---|---|---|
| id | id | No (PK) |
| account_id | id (FK → Account) | No — recipient |
| created_at | timestamp | Yes |
| type | enum | Yes — see §13.1 |
| message | text | Yes |
| link | text | Yes |
| read | boolean | No (default false) |

*Index for lookup by account_id.*

### 3.13 Audit Log
Append-only system audit trail: id, timestamp, message (free text).

### 3.14 Setting
Generic site-wide key/value store (JSON-encoded values). Known keys: event display name, default
points map, "count in-progress events" toggle, per-program age-category band definitions, sender
email address, age-calculation reference date, schema/migration version bookkeeping.

---

## 4. Core Domain Rules

### 4.1 Age Category Derivation
- Age categories are **not** built into the system — each Program defines its own bands from a
  blank slate (id, name, inclusive min_age–max_age range).
- **Algorithm:**
  1. Determine the reference date: the program's configured "age as of" cutoff date if set and
     valid, else today's date.
  2. reference_year = reference date's year.
  3. age = reference_year − birth_year.
  4. Match age against each configured band's [min_age, max_age] inclusive range, in definition
     order; assign the participant to the **first** matching band. No match → category undetermined
     (null).
- Gender/division is a separate, independent axis — Sport Events filter on age_category AND gender
  together, not as a combined single value.
- Saving a change to a program's age-category bands must **immediately recompute** every
  participant in that program's derived category (a synchronous cascade, not a background job).

### 4.2 Scoring Modes

| Mode | Description | Default points/places |
|---|---|---|
| **placement** | A result-enterer records each competitor's finishing position directly; points awarded strictly by that recorded place. | 5/3/1, 3 places |
| **measured** | Each competitor submits a raw measurement (distance/height/time) across a configured number of rounds; the system auto-ranks everyone by their single best value (**highest** value wins — a rebuild targeting time-based events where lower is better must account for this, e.g. store negated times, or add an explicit "lower is better" flag per event) and derives placement from that rank. | 3/2/1, 3 places |
| **participation** | Everyone taking part is simply marked present; top finishers can optionally still be assigned places & points on top of that. | 5/3/1, 3 places |

**Default points logic** (used only when creating a new event, before any manual override): if the
event format is team-based (team or doubles), default is **10/5, 2 places**, regardless of scoring
mode. Otherwise: measured mode defaults to 3/2/1 (3 places); everything else defaults to 5/3/1 (3
places).

Each Sport Event may override its own points map and "places paid" count; falls back to the
program/site-wide default points map (itself defaulting to 5/3/1) when not overridden.

**Recomputation rule (runs every time results are saved or an event's points/places config
changes):**
- *Measured mode:* for each result, `best` = the maximum of all non-empty recorded round values.
  Results with no valid value get no place. All results with a value are ranked descending by
  `best` into places 1, 2, 3…
- *Placement/participation mode:* place is whatever was directly entered.
- *All modes, points:* if a result has a place and that place is ≤ the event's "places paid"
  count, points = the event's points map value for that place (falling back to the program
  default map); otherwise points = 0 — **even if a place number beyond the cutoff was recorded**
  (e.g. 4th place in a top-3-only event explicitly scores 0, not a lesser nonzero amount).

### 4.3 Event Formats
- **individual** — each competitor scored independently.
- **doubles** (pairs) / **team** — collectively "team format": scored **once per competing team**,
  not per individual member. The Result row's participant reference is the Team's id, not an
  individual's. The actual competing line-up for each team in a given event is the Event Lineup
  entity (§3.9); absent an explicit lineup, default to every eligible signed-up player of that
  team. **Doubles lineups must contain exactly 0 or exactly 2 players — any other count must be
  rejected at save time.** Team-format lineups have no such fixed-size constraint.

### 4.4 House / Number System
Distinct from competing Team. A fixed enumerated set of "houses": `VA, VB, VC, A, B, C, D`, plus a
special `Admin` pseudo-house value exposed only on the admin-facing account-creation form (selecting
it creates an admin-type account with no participant number). Within a real house, each participant
gets a 3-digit number; `(house, number)` must be unique together, but only among accounts that
actually have both fields populated.

### 4.5 Program Status Lifecycle
Six ordered stages, each with a visibility rule:

| Order | Status | Visible to non-admins? |
|---|---|---|
| 1 | Planned | No |
| 2 | New | No |
| 3 | WIP | No |
| 4 | Draft | No |
| 5 | **Active** | **Yes** |
| 6 | **Completed** | **Yes** |

- Only Active and Completed programs are selectable/visible to non-admin users; the first four
  stages are admin-only staging states for setting up a new meet.
- A program **cannot move to Active** if it has a `start_date` set in the future (blocks going live
  before the meet's own start date).
- The rebuild should also add: a program cannot go Active if its end_date is in the past (the
  original app doesn't enforce this — see §18).
- Status transitions follow this fixed linear order (advance/step-back controls, not a free-form
  graph).

### 4.6 Security Question Presets
A fixed preset list offered for self-service account recovery (chosen by the account holder,
paired with a free-text answer):
1. "What is your parent's name?"
2. "What is the name of your first school?"
3. "What is your favourite sport?"
4. "What city were you born in?"

### 4.7 Sport Event (SAC) Status Lifecycle
Independent of `approval_status` (§10.5): **new → registration_open → registration_closed →
scheduled → in_progress → completed**, plus two side-branches **postponed** and **cancelled**
reachable from other states. Each status has a UI badge style (presentational only).

A site-wide setting controls whether `in_progress` (not yet `completed`) events should count
toward displayed standings/leaderboards — see the caveat in §11 about this setting's intended vs.
actual behavior in the current app, which the rebuild should resolve deliberately one way or the
other.

### 4.8 Default Password Behavior
Admin-created accounts are seeded with a fixed, disclosed default password and flagged to force a
password change on next login.

---

## 5. Authentication & Accounts

### 5.1 Self-Registration (public)
**Fields:** username, display name, role choice, team (captain only), password, shared "role
password" (captain only — see below), email, birth year (player only), division/gender (player
only), house (player only), 3-digit house number (player only), security question + answer.

**Corrected rule for the rebuild (per explicit product decision this session):** self-registration
should **only ever create a player account** — do not offer a role choice, do not offer a captain
path with a shared "role password," and do not collect a team at registration. Captain is a
role **granted later by an admin**, editing the player's account (see §5.4). This removes a real
security/process gap in the original design: anyone who knew a shared "captain password" could
self-grant captain for any team, bypassing admin review entirely.

**Validation:**
- Username & display name required; username unique (case-insensitive).
- Password must meet the strength rule (§17.2).
- A security question AND non-blank answer are mandatory.
- Birth year: integer, 1900 ≤ year ≤ current year (computed via the configurable age-reference
  date, §4.1).
- Division must be one of the two allowed values.
- House must be one of the fixed house codes; house number exactly 3 digits, unique within that
  house (conditional constraint, §4.4).

**On success:** creates the account (role = player only), assigns it to the **currently active
program** (mandatory direct program link — see §18.1), derives its age category from birth year,
does *not* log the new account in, redirects to login with a success message. Notifies all
captains+admins that a new player registered and needs a team.

### 5.2 Login
- Public form: a single "username" field matched case-insensitively against either username or
  email.
- Disabled accounts are rejected with the same generic "invalid username or password" message used
  for any other failure (no information disclosure about account existence/state).
- **Login throttling:** track failed attempts per lowercased username/email; after 5 failures within
  a 15-minute rolling window, block further attempts with a generic "too many attempts" message
  regardless of whether the credentials would have succeeded. Clear on success. **Must be persisted
  (DB-backed), not in-process memory** — the original app's in-memory throttle resets on restart and
  doesn't work across multiple server processes; see §18.
- On success: fully reinitialize the session, mark permanent (subject to idle timeout below), stamp
  last-login time.
- **Forced password change:** if the account is flagged to require a password change, redirect
  straight to the change-password page and block navigation to any other page (except
  logout/change-password) until cleared.
- **"Remember me"** extends the session idle-timeout window (see §17.3), not the absolute session
  lifetime.
- Post-login redirect must validate any `next=` target is a same-site relative path (starts with
  `/`, not `//`) — an anti open-redirect check.

### 5.3 Forgot Password (security-question based; no email delivery required)
Two-step flow: (1) submit username → if the account has a security question configured, show it
(else a generic error that doesn't reveal whether the username exists); (2) submit the answer
(trimmed, lowercased before comparison) + new password + confirmation → on match and valid password
strength, reset the password (not forced to change again) and log the event without an attributable
actor (the user isn't authenticated at this point).

### 5.4 Password & Role Management
- **Self-service password change:** requires current password unless a forced-change is pending;
  new/confirm must match and pass strength rules; clears the forced-change flag.
- **Self-service security-question update:** a separate action requiring current password (unless
  forced-change pending); leaving the answer blank keeps the previous answer while updating only the
  question text; leaving the question blank is a no-op.
- **Admin sets a specific new password** for any account: always forces a change at next login.
- **Admin one-click reset to default password**: same forced-change behavior.
- **Admin-created accounts** (`user_new`): admin picks house/role(s) directly — selecting the
  special `Admin` house value creates an admin-role account (forced role = admin only); any other
  house creates a player/captain-capable account with whatever roles the admin checks. If `captain`
  is among the chosen roles, a team is mandatory. No password is collected here — every
  admin-created account gets the disclosed default password + forced change. **No shared "role
  password" gate applies to admin-created accounts** (only the corrected/removed captain
  self-registration path had that gate).
- **Granting captain to an existing player** (the only path to captain in the corrected design): an
  admin edits the account and adds the captain role + assigns a team.
- Changing an account's roles also flips whether it's a roster-visible competitor entry (true if
  the player role is present).
- Disabling an account must refuse to disable the last remaining active admin account (prevents
  total lockout).

---

## 6. Programs (Multi-Tenancy)

- Every logged-in user must have an **active program** selected in their session before doing
  almost anything (a global request-level guard). If none is selected and exactly one program is
  currently Active, auto-select it transparently; otherwise redirect to a program picker (admins
  see all programs regardless of status; everyone else only Active/Completed ones).
- A "program switcher" (visible to admin/staff, and to anyone with more than one eligible program)
  lets the current user change their active program at any time; if the currently-selected program
  becomes invisible to them (e.g. an admin demoted it below Active), detect this and force
  re-selection on the next request.
- Full CRUD for programs (admin only): name (required), description, has_teams flag, status,
  start/end dates.
- **Date enforcement:** when a program has both start_date and end_date set, every Sport Event's
  own scheduled date must fall within that inclusive range — reject an out-of-range save with a
  message naming the program's actual date range. Programs without both dates set are unconstrained
  (no forced range). *(This is a real, already-implemented rule worth keeping as specified — it's
  correct, not a gap.)*
- **What must be scoped per-program:** Teams, Sport Categories, Sports (master catalogue),
  Announcements, age-category band definitions, and — **critically, corrected from the original
  app** — every Account with the player/captain role (see §18.1). Sport Events inherit program
  scope indirectly through their parent Sport.

---

## 7. Participants & Rosters

*(In the corrected data model, "participant" = any Account holding the player role; this section
describes the roster/team-management workflows around such accounts.)*

### 7.1 CRUD
- **Create** (admin or captain): name, division, birth year required; team is admin-only at
  creation (a captain's newly-added player starts teamless, see §7.2). Age category is always
  computed server-side from birth year — never trusted from client input.
- **Edit** (admin, or captain restricted to their own team's players): name/division/birth year
  editable by both; team reassignment via this form is **admin-only** — a captain cannot use the
  edit form to move a player to a different team (must go through the assign/roster workflow,
  §7.2).
- **Archive/Unarchive** (admin, or captain for own-team players): a reversible soft-delete toggle
  hiding a participant from active lists without losing history.
- **Delete** (admin, or captain for own-team players): **hard delete only if the participant has
  zero recorded results**; if any results exist, silently convert to an archive instead (with an
  explanatory message) to preserve score integrity. A hard delete cascades to remove sign-ups.
- Every create/update stamps who made the change and when.

### 7.2 Team Assignment & Roster Approval Workflow
A participant is in exactly one of three states: **assigned** (has a team), **unassigned & free**
(no team, no pending request), or **unassigned with a pending request** (a captain has asked to
claim them; awaiting admin decision).

- **Admin assignment is immediate and authoritative:** setting or clearing a participant's team
  always takes effect instantly and clears any outstanding pending request in the same action
  (an admin decision overrides any in-flight captain request). Notifies the affected player either
  way.
- **Captain actions, both scoped to their own team only:**
  - *Claim* an unassigned player: does not move them immediately — sets a pending-request flag and
    notifies admins for approval. Attempting to claim someone already on your own team is a no-op.
  - *Release* a player currently on your own team: takes effect immediately (no approval needed to
    remove someone from your own team); notifies the player.
- **Admin decision on a pending request:** *Approve* promotes the request into an actual assignment
  and clears the pending flag, notifying both the requesting team's captain(s) and the player.
  *Reject* just clears the pending flag (player stays unassigned), notifying the captain(s). Acting
  on a participant with no pending request is a no-op.
- The roster/participants view should surface: for a captain, their own pending requests plus the
  pool of fully-free (unassigned, unrequested) players they can claim; for an admin, a consolidated
  list of every outstanding request across all teams.

### 7.3 Volunteers
A boolean flag on a participant (not a separate account type) marking them as helping run the event
in addition to (or instead of) competing. Toggleable by admin (any participant) or captain (own
team only). A volunteers list view should be filterable by name, team, category, division, and
should default-scope a captain to their own team.

### 7.4 Bulk Import
Admin-only CSV/JSON upload accepting name, team, division, birth year (age category derived
automatically). Rows with no name are skipped without erroring the whole batch; a summary count is
reported. Imported rows have no login credentials by default (roster-only entries) — **note: the
rebuild should decide deliberately whether an unrecognized/missing division value defaults to a
real allowed value (Male/Female) rather than an arbitrary placeholder string, which was a latent
bug in the original app — see §18.**

### 7.5 Bulk Team Placement ("Team Selection")
Admin-only page: every non-archived participant (properly scoped to the active program — see §18),
filterable by search text/division/category/team (including an "unassigned only" filter), with an
inline editable team-assignment field per row and a single "save all changes" action that diffs
against current state and only writes actual changes, notifying each affected participant. This is
a direct override path (no approval step), distinct from the captain-claim workflow in §7.2. Should
show live per-team headcounts including an "unassigned" bucket.

---

## 8. Sports Catalogue & Events

### 8.1 Sport Categories (admin, per-program)
CRUD with: name required & unique within the program, display sort order. Deleting a category that
still has sports assigned must **not** silently fail or error — redirect to a dedicated management
view for bulk-resolving its children (move to another category / archive / delete, with automatic
archive-instead-of-delete for any sport that's "in use," see §15). Once a category has zero
children, deleting it becomes available.

### 8.2 Sports (admin, per-program)
CRUD with: name required, unique within the program (composite constraint — same name allowed
across different programs). Category assignment. A list view should show an "in use" indicator and
usage breakdown (child events, sign-ups, results) and only allow archiving when the sport has no
active (non-archived) child events, prompting resolution via a management view otherwise.

**Archive cascade:** archiving (or unarchiving) a sport must automatically archive (or unarchive)
every one of its child Sport Events in the same action, reporting how many were also toggled — an
admin should never have to archive each child event individually first.

**Delete:** refuse (redirect to the management view) if the sport has any child events at all,
regardless of archived state; otherwise hard-delete along with sign-ups.

### 8.3 Sport Events / SACs (admin, per-program)
Full CRUD as described in §3.6/§4.2/§4.3. Creation requires picking a sport, optional age
category/gender restriction, and event format; a duplicate check must prevent an identical
sport+age+gender+format combination from being created twice (a sport CAN have multiple SACs for
different age/gender/format combinations, including the same age+gender run as both individual and
doubles simultaneously — that's intentional, not a dupe). Scheduling (date/time/location) and
scoring configuration (points/places/rounds/scoring mode) are editable after creation, subject to
the program date-range constraint (§6) and a rule that an event whose scheduled date/time has
already passed cannot be (re-)set to "new" or "registration_open" status (that would contradict the
clock).

---

## 9. Sign-ups & Eligibility

- A participant is **eligible** for a Sport Event if: they are signed up for that event's parent
  Sport, AND (the event's age-category restriction is unset, or matches the participant's category),
  AND (the event's gender restriction is unset, or matches the participant's division). An event
  with no age/gender restriction acts as a wildcard matching every sign-up for that sport.
- Players self-manage their own sport sign-ups (a checklist of available sports, filtered to ones
  their age/gender is eligible for); admins/captains can manage sign-ups on behalf of participants
  (a captain limited to their own team's roster).
- Only currently "open to register" events should be offered to a self-managing player as *newly*
  selectable, but a player must never be silently un-registered from a sport they're already signed
  up for just because it later stopped being open — that's only reversible by the player themself.
- A sign-up that already has a **recorded result in a completed event cannot be withdrawn/removed**
  (a "locked" sign-up) — the UI should indicate this explicitly rather than allowing a silent no-op
  or error.
- For team/doubles-format events, "eligible teams" = teams with at least one eligible signed-up
  player; each carries its full eligible roster plus the captain-curated lineup (§3.9).

---

## 10. Scoring & Results Workflow

*(The most complex business logic in the system — implement this state machine exactly.)*

### 10.1 Access & Timing
- Only captains (own team's events, where their team has ≥1 eligible participant) and admins (any
  event) may enter scores.
- Score entry is blocked until the event's scheduled date has arrived (today ≥ event date); an
  event with no date set cannot have scores entered at all. This gate applies to admins too.
- Once an event is finalised (published), only an admin may continue editing scores — a captain is
  blocked until an admin explicitly reopens it (§10.5).

### 10.2 Score Entry Forms (four distinct shapes)
1. **Team/doubles format (any scoring mode):** one row per eligible team. A separate "line-ups"
   panel (its own save action, always editable regardless of the event date) lets each team's
   captain (or any admin) select which eligible signed-up players actually compete — **doubles
   lineups must be exactly 0 or 2 players, rejected otherwise; team-format lineups have no such
   constraint.** A separate "scores" panel takes one numeric place per team (the whole team gets
   that place/points).
2. **Individual + measured mode:** one row per eligible participant, one numeric input per
   configured round count (decimals allowed).
3. **Individual + participation mode:** one row per eligible participant with a present/participated
   checkbox plus an optional numeric place (so attendance can be recorded even without full
   ranking).
4. **Individual + placement mode (default):** one row per eligible participant with a single
   numeric place input.

Saving any of these must immediately, synchronously recompute placement/points for the whole event
(§4.2) — there is no separate manual "calculate" step.

### 10.3 Eligibility of Score Entry vs Publication
Recomputation on save happens **regardless of publication/approval state** — a freshly entered,
not-yet-approved score already affects live totals immediately (see the standings caveat in §11).
Only the finalised/published results views filter to approved/completed data.

### 10.4 Line-ups
Editable independently of the score-entry timing gate (a captain can set up their lineup before the
event date arrives). Absence of an explicit lineup row for a team defaults to every eligible
signed-up player of that team being considered part of the entry.

### 10.5 Captain-Vote Approval State Machine
Each Sport Event carries `approval_status` ∈ {draft, pending, disputed, approved} and a `finalised`
boolean (true only when approved).

**"Involved captains"** for an event = the set of active, non-disabled captain accounts whose team
appears in the event's results (either an individual team member has a result row, or — team/doubles
format — the team itself is a result's participant reference). A captain with no team, or who is
disabled, is never "involved."

**Admin submits/publishes (any state, any time):** unconditionally sets approved + finalised — no
vote required at all, bypasses any in-progress captain vote. Notifies all involved captains and
every player with a result in the event that results are published.

**A captain submits for approval:**
1. Any existing votes for the event are cleared and rebuilt.
2. The submitter's own vote is auto-recorded as "agree" — they never separately vote on their own
   submission.
3. Status → pending.
4. If there are **no other involved captains** (e.g. a single-team event): **do not auto-publish.**
   Notify all admins that this event needs their approval specifically because no other captain is
   involved — it stays `pending` until an admin acts. *(Get this exactly right — a single-captain
   event must never be treated as automatically approved just because there's no one left to
   disagree.)*
5. If there are other involved captains: create a pending vote for each and notify them that
   agreement is needed.

**A captain votes:**
- Only a captain with an existing (pending) vote row for that event may vote.
- **Disagree** → status flips to `disputed` immediately (doesn't wait for remaining votes); notifies
  all admins. Disputing does not itself reopen the event for editing — only an admin action does.
- **Agree** → records the vote, then checks: if **every** vote for the event (including the
  submitter's auto-agree) is now "agree," publish immediately (same effect as an admin publish —
  approved + finalised + notifications sent). If any vote is still pending, no publish yet — just
  confirm the vote was recorded. Publication happens in the same request as the final agreeing
  vote, with no separate confirmation step.

**Admin decision on a pending or disputed event:**
- **Approve (override):** publishes regardless of current vote tally.
- **Withhold & reopen:** resets to draft (finalised → false, all existing votes deleted, event
  status → in_progress); notifies all previously-involved captains that it's been reopened for
  re-checking. A captain (or admin) must then re-enter/adjust and resubmit from scratch, generating
  a fresh vote round.

**Full transition summary:**
`draft` → (captain submits) → `pending` → (all agree) → `approved`/`finalised`
`pending` → (any disagree) → `disputed`
`pending`/`disputed` → (admin approves) → `approved`/`finalised` (skips remaining votes)
`pending`/`disputed` → (admin withholds) → `draft` (votes cleared, reopened)
any state → (admin submits directly) → `approved`/`finalised` immediately

---

## 11. Team Standings

- Computed live (not cached) whenever needed.
- Per team: **individual points** = sum of points from every result belonging to a participant on
  that team, **plus team-keyed points** = sum of points from every result whose participant
  reference is that team's own id (team/doubles-format results). Total = both summed.
- **Design decision required for the rebuild:** the original app sums this across **every** result
  regardless of approval/finalised state — i.e. a freshly entered, not-yet-approved (even disputed)
  score already contributes to live standings and to a player's own dashboard point totals the
  moment it's saved, while the dedicated Results pages (an individual's history, `result_detail`,
  the "overall completed events" list) filter to finalised/completed only. There is a "count
  in-progress events" setting in the original app that describes gating standings on finalisation,
  **but it is not actually wired into the standings calculation** — it's stored but has no effect.
  **The rebuild must decide deliberately** whether standings should honor that toggle for real
  (only count finalised results when the toggle is off) or intentionally always show live/unapproved
  scoring — and implement whichever is chosen consistently, rather than shipping a setting that
  silently does nothing.
- Rank by total points descending, ties broken alphabetically by team name.

---

## 12. Results Viewing & Call Sheets

### 12.1 Results Pages
Content shown differs by acting role:
- **Player:** "My results" (own history, if linked to a roster entry), "Team results" (if on a
  team), "Standings." No unrestricted browsing of everyone's results.
- **Captain:** a combined view — Standings always shown, plus a full filterable "Overall results"
  list (no separate Mine/Team tabs); selecting a specific participant swaps in their individual
  result table.
- **Admin/other staff:** "Standings" plus "Overall results" (and Mine/Team if linked to a roster
  entry too).
- **Overall results filter:** a cascading filter (Gender → Age Category → Sport Category → Sport →
  Team → date range → participant name) where each dependent dropdown narrows to only combinations
  that actually exist among qualifying events, resetting a selection that becomes invalid as parents
  change. Only finalised/completed events with at least one recorded result are listed. Non-admin
  users must never see draft/in-progress scoring state through this view.

### 12.2 Per-Event Result Detail
Any logged-in user can view any event's result detail (staff see the raw approval-status badge,
non-staff see a simplified published/not-yet-published indicator). Sorted by place (unplaced last).
Individual format shows place/name/team/points (+ best value for measured mode). Team/doubles
format shows place/team/points plus the line-up — inline names for small line-ups (≈≤2), collapsing
into an expandable "N players" control for larger rosters. Must support print/browser-PDF.

### 12.3 Call Sheet
A print-friendly attendance/roster checklist for one event: event summary header (sport, category
badge, age/gender/date/time/location/scoring mode) plus a checkbox table of every eligible
participant (id, name, team, category, gender) and a total-expected count. For staff to print and
carry — does not itself record results.

---

## 13. Notifications & Announcements

### 13.1 In-App Notifications
- Delivery is per-account, gated by (a) the account's own mute preferences (mute-all, or mute
  specific types) and (b) whether the notification type is relevant to at least one role the
  account holds — a multi-role account receives a type if *any* of its roles finds it relevant.
- **Types and their relevant roles:**
  - `assignment` (a player's own team add/removal) → players.
  - `roster` (team-join requests and decisions) → captains, admins.
  - `new_player` (a new player needs a team) → captains, admins.
  - `approval` (score submissions/votes/disputes/publications) → players, captains, admins.
  - `admin` (admin/config-level notices, e.g. "no other captain involved") → admins only.
  - `announcement` (a new visible announcement) → all roles.
- Provide a helper for "notify everyone holding any of these roles," used throughout the app rather
  than resolving recipient lists ad-hoc at each call site.
- Notifications carry read/unread state; show an unread-count badge in the nav; a notifications page
  lists the most recent (cap at a reasonable count, e.g. 100), supports mark-one/mark-all-read, and
  lets the user edit mute preferences.

**Concrete triggers to implement** (recipient(s) ← event):
- Captains+admins ← new player self-registers.
- Admins ← captain requests to claim a player.
- Target team's captain(s) ← admin approves/rejects a roster request; the affected player ←
  approval only.
- Affected player ← admin (or bulk team-selection tool) directly changes their team.
- Other involved captains ← a captain submits scores for approval (or admins, if no other captain is
  involved).
- Admins ← a captain disputes results.
- Involved captains + every player with a result in that event ← results published (any path).
- Previously-involved captains ← an admin withholds/reopens a submission.
- Everyone (all roles) ← a new announcement is posted visible.

### 13.2 Announcements (admin, per-program)
CRUD: title (required) + body + visible toggle, scoped to the currently active program. Posting
with "visible" checked immediately notifies everyone. Editing does not re-notify (only initial
visible creation does). Toggling visibility later via edit does not retroactively notify. Delete is
a straightforward hard delete (no archive-protection needed here — announcements have no dependent
data). The dashboard feed should show the most recent visible announcements for the active program
only.

---

## 14. Admin Tooling

### 14.1 Overview/Maintenance Hub
A navigation hub with a live count per admin sub-area (teams, sport categories, sports, sport
events, participants, user accounts, announcements) plus a combined "sample data" count. Purely
navigational — no editing on this page itself. **All counts must be scoped to the currently active
program** — this is a known gap in the original app (see §18.2).

### 14.2 Self-Tests Panel
Admin-triggerable, on-demand run of the application's own automated end-to-end test suite against a
throwaway/isolated database (never the live one), with a reasonable timeout, surfacing pass/fail
counts and per-test detail plus raw output for debugging. An in-app smoke-test button so an admin
can sanity-check a live deployment without shell/CLI access.

### 14.3 "What's New" & "About" Pages
- **What's New** (any logged-in role): a maintained, reverse-chronological release-notes feed.
- **About** (public, no login required — along with login/register/forgot-password, one of the few
  pages reachable while logged out): platform description, configured event name, feature
  highlights, optional contact link, running schema/version info, and (if logged in) a link to
  What's New. Should double as the PWA's offline fallback page (§17.5).

### 14.4 Data Management
- **CSV/JSON participant import** — see §7.4.
- **JSON export** of individual data collections (participants, sports/events, results) as
  downloadable attachments, restricted to a defined whitelist of exportable collections.
- **Full database backup** — a one-click complete backup download, logged in the audit trail.
- Result sheets / call sheets / schedules should each offer a print/browser-PDF button rather than
  requiring server-side PDF generation — a deliberate simplicity constraint, not a missing feature.

### 14.5 Sample/All Data Reset
Three distinct destructive actions, each showing sample-vs-total counts first:
- **Remove sample data only** — deletes rows flagged as demo/seed data (and their dependents:
  sign-ups, results, votes) across sports/events/participants/announcements/sample admin accounts
  (except the acting admin), clears the audit log and orphaned notifications, explicitly preserves
  teams/categories/settings/all real data.
- **Wipe everything** — a full reset of the *current program's* data domain: sign-ups, results,
  votes, participants, events, sports, announcements, notifications, audit log, event lineups,
  teams, sport categories, and every program's stored age-category bands. Only admin accounts
  survive. **Programs themselves are not deleted by a wipe.** The confirmation/warning must spell
  out exactly what's being erased.
- **Reseed** — re-runs demo/seed generation, only filling in currently-empty tables (idempotent,
  won't duplicate existing data).

### 14.6 Settings
Admin-only form covering: event display name; default points map (parsed from a friendly
`5,3,1`-style string); the "count in-progress events" standings toggle (§11 — must actually be
wired up, or removed if not honored); shared role passwords **(remove the captain shared-password
concept per §5.1's corrected design — there is no longer a self-service role to gate)**; sender
email address (display/contact only, not SMTP config); the age-calculation reference date override
(§4.1).

### 14.7 Age Categories Admin
Per-program bulk editor: add new bands (name, min/max age) plus inline-editable existing bands, one
combined "save all" action applying every add/edit/remove together so partial edits aren't lost.
IDs auto-generated from name (with collision suffixing), never user-typed. Validation on save: min
≤ max per band, no duplicate names, no overlapping ranges (report the first offending pair by name
and range) — reject the whole save with everything the admin typed preserved on screen. A
successful save must synchronously recompute every participant's category in that program. A band
that's currently in use (referenced by any participant or event) must not even show a delete
control until the admin resolves those references elsewhere — a hard UI-level block, not an
attempted-then-caught error.

---

## 15. The "In Use" / Archive-vs-Delete Pattern

Applied uniformly across Sports, Sport Categories, Sport Events, Age Categories, and Participants —
**this is a core, consistent rule to implement once and apply everywhere, not per-feature:**

- **"In use"** means: has dependent/recorded data that would be orphaned or would silently erase
  history if hard-deleted (a sport with any event; a category with any sport; an event with any
  recorded result; a participant with any recorded result; an age band referenced by any
  participant or event).
- **Two acceptable enforcement styles**, chosen per context:
  1. **Silent auto-fallback:** attempt the delete as normal; if the target (or any item in a bulk
     selection) is in use, archive it instead and report back exactly how many were hard-deleted vs.
     archived. (Used for Sports, Events, Participants.)
  2. **UI hard-block:** simply don't render a delete control when in use; show an inline "in use"
     indicator with a usage breakdown instead, requiring the admin to resolve dependents first via a
     dedicated management view. (Used for Age Categories, and as the first line of defense for Sport
     Categories/Sports before falling back to a bulk-resolution view.)
- **Archiving must always be reversible** (a toggle, never one-way).
- **Never hard-delete anything with recorded results, sign-ups, or dependents without at least
  offering an automatic archive fallback and a clear explanation of why.**

---

## 16. UI & Navigation Structure

### 16.1 Overall Layout
- **Top bar** (logged in): menu toggle (mobile), app brand linking to the dashboard, an active-
  program indicator/switcher (admin/staff visible), a role switcher (only shown as a dropdown if
  the account holds more than one role — single-role accounts see a static badge), a user menu
  (profile, about, sign out), a light/dark theme toggle (persisted client-side).
- **Desktop:** a persistent left sidebar of role-filtered links:
  - Everyone: Dashboard, What's New, Sports Calendar, Results.
  - Player/Captain/Admin: + Sports Sign-up.
  - Captain: + Participants. (Admin also reaches Participants, but under its own Admin section.)
  - Captain/Admin: + Volunteers, Result Entry, Approvals, Account/Password.
  - Admin only: a distinct "Maintenance" section — Overview, Programs, Teams, Sport Categories, Age
    Categories, Sports, Sport Events, Participants, Team Selection, Players (accounts),
    Announcements, Settings, Import/Backup, Reset/Sample data.
  - Notifications (with unread badge), always last, for everyone.
- **Mobile (narrow viewport):** replace the sidebar entirely with a horizontal, wrapping pill-strip
  fixed below the top bar; role-heavy clusters ("Team tools," "Admin tools") collapse into
  tap-to-open dropdown groups rather than each being its own pill, to keep the row manageable on a
  phone screen.
- Flash/toast messages (success/error/info/warning categories) render at the top of the content
  area on every page.
- Logged-out pages (login, register, forgot-password, about) render without app navigation chrome —
  a minimal shell plus a theme toggle.
- **Permission checks are always based on the account's full role set, not the currently active
  role** — the acting-role switch changes presentation/defaults only, never what's actually
  reachable.

### 16.2 PWA Support
- Installable as a standalone app (manifest with name, start/scope, theme color, portrait
  orientation, maskable icons at common sizes) with iOS-specific meta tags.
- Service worker: pre-caches core static assets and the About page on install; **static assets** use
  a cache-first-with-background-refresh strategy; **HTML/page requests** use network-first, falling
  back to the cached About page only when fully offline (no full offline experience for dynamic
  pages like the dashboard or scoring — that's an acceptable, deliberate scope limit, not a bug).
  Never intercept non-GET requests — writes always go straight to network and simply fail normally
  offline (no offline write-queueing/sync). Bump the cache version on each deploy to force a clean
  refresh.

### 16.3 Mobile-Responsiveness (Hard Requirements)
- **Every data table must degrade to a stacked card layout** on narrow viewports (label/value pairs
  per row, first column as a prominent card title, actions anchored to the card's bottom/right).
- **Full-width, touch-sized primary buttons and inputs** on mobile; compact/icon-only buttons only
  acceptable inside dense per-row action groups.
- **Filter toolbars and page headers stack vertically** on narrow screens.
- **Status/highlight coloring** (e.g. "your team" row, medal-place rows) must survive the
  table-to-card transform, in both light and dark themes.
- **Print stylesheets** must hide all navigation chrome for call sheets, result sheets, and
  schedules — printable views are first-class, not an afterthought, given there's no server-side PDF
  generation.

---

## 17. Non-Functional Requirements

### 17.1 CSRF Protection
Every mutating request (POST/PUT/PATCH/DELETE) must carry a valid per-session anti-CSRF token
(auto-injected into forms client-side, or an equivalent header for programmatic requests), verified
with a constant-time comparison; mismatches must be rejected (HTTP 400) before the underlying
handler runs.

### 17.2 Password Handling
Passwords hashed with a modern, deliberately-slow algorithm (PBKDF2-HMAC-SHA256 with ≥200,000
iterations and a per-password random salt is the proven baseline here; a rebuild may prefer Argon2
or bcrypt instead — either is acceptable) stored in a self-describing format so the scheme can be
upgraded later without invalidating old hashes. Verify with constant-time comparison. Minimum
password length: 6 characters (the original app intentionally keeps this minimal — no forced
complexity rules); a rebuild may raise this but shouldn't add friction beyond what's actually needed
for a casual community-event tool.

### 17.3 Session Model
- Secure cookie flags: HttpOnly always; SameSite=Lax; Secure flag forced on whenever running in a
  production posture (i.e., HTTPS is a hard requirement for real deployments).
- Base session idle timeout: 30 minutes of inactivity; extended to 24 hours if "remember me" was
  checked at login. This should be a rolling/sliding idle timeout (each request refreshes the
  countdown), not a fixed absolute expiry.
- Refuse to start in a production posture with a default/placeholder secret key — force operators to
  configure a real random one.

### 17.4 Testing Approach
Maintain a committed, self-contained end-to-end test suite that spins up the app against a
throwaway/temporary database, seeds representative demo data, and exercises: auth/RBAC boundaries
(every role blocked/allowed correctly per route), the full sign-up → score entry → captain-vote →
publish workflow (including every branch: auto-agree, single-captain-no-vote-needed, dispute,
admin override, admin withhold), the archive-vs-delete "in use" pattern, and cross-program data
isolation. Make this runnable both from the command line and as an in-app admin "self-tests" button
against a live deployment (isolated database, never the real one).

### 17.5 Cross-Program Data Isolation (critical, testable requirement)
Every list, count, and aggregate that could span more than one program must be explicitly filtered
to the currently active program — this must be a first-class, tested requirement given how many
places the original app got this wrong (§18.2–§18.4). A good acceptance test: seed two programs with
parallel data (participants/teams/sports/events/results in each), and assert that no page/count/
list in Program A ever shows so much as one row's worth of Program B's data, and vice versa.

---

## 18. Known Gaps in the Current App to Fix in the Rebuild

These are concrete, confirmed defects/gaps in the existing implementation — do not reproduce them.

**18.1 — Participants have no direct program association.** The original app only links a
participant to a program *indirectly*, via whichever team they're on. An unassigned participant
(which includes every self-registered player before a captain claims them) belongs to no program at
all. Several key admin pages (the main participants list, the volunteers page, the bulk
team-selection page) query participants with **no program filter whatsoever**, so in a multi-program
deployment, participants — especially unassigned ones — from one program's edition are visible/
actionable from a different program's admin screens. **Fix:** give every participant a mandatory,
direct program association set at creation time (§3.3, §6).

**18.2 — Admin Overview stat tiles are unscoped.** The admin dashboard's per-area counts (teams,
sport categories, sports, events, participants, users, announcements, sample-data count) are
computed with no program filter at all — a brand-new, genuinely empty program's overview still shows
totals from every program. **Fix:** scope every count to the active program (§14.1).

**18.3 — Results/scores/standings are not scoped to the active program.** Results are only reachable
indirectly via their event → sport → program chain, and most result-aggregating queries (a player's
own score, team standings, the results list's several views, per-event result detail) filter only by
participant/team, never joining through to check the program. A participant/team's results from
every program they've ever been part of can get summed together on one program's dashboard/
standings. **Fix:** every result query must join through to the event and filter on the active
program (§10, §11, §12).

**18.4 — The "count in-progress events" standings setting is stored but not honored.** It's exposed
as a real toggle in Settings but has no actual effect on the standings calculation, which always
includes non-finalised results regardless of the toggle's value. **Fix:** wire it up for real, or
remove the setting — don't ship a control that silently does nothing (§11).

**18.5 — CSV import's division default doesn't match the app's own allowed values.** When a division/
gender column is missing from an imported row, the original app defaults to a placeholder string
that isn't one of the two actual allowed division values used everywhere else in the system. **Fix:**
default to a genuinely valid value, or require the column and reject rows missing it (§7.4).

**18.6 — Login throttling is in-memory, not persisted.** Resets on every restart and doesn't work
correctly across multiple server processes/workers. **Fix:** persist failure counts in the database
(§5.2, §17).

**18.7 — Program end-date isn't enforced when activating.** The original app blocks activating a
program before its start date but has no equivalent check preventing activation (or continued
active status) after its end date has passed. **Fix:** add that check (§4.5).

**18.8 — Self-registration allows a shared-password captain path.** Anyone who knows a shared
"common role password" can self-grant the captain role for any team at signup, bypassing admin
review. **Already corrected in this spec — see §5.1: registration is player-only; captain is
admin-granted.**

---

## 19. Past Mistakes to Avoid

Lessons from this application's own schema evolution — specify the corrected end-state directly
rather than repeating the multi-step journey that got there:

1. **Don't merge "sport catalogue entry" and "its per-age/gender scheduled instance" into one
   table.** These have different cardinality (one sport, many age/gender variants) and different
   lifecycles (catalogue entries change rarely; scheduled instances get edited constantly for
   scoring/status). Model them as two separate, related entities from day one.
2. **Don't merge "pure staff login" and "competitor roster entry with an optional login" into one
   overloaded table**, and don't split them into two physically separate tables unified only by a
   view either (that was this app's own fix, and it still causes friction — see §3.3's design
   decision). Prefer one Account entity with role-driven optional fields.
3. **Don't give catalogue-entry names (like a Sport's name) global uniqueness if there's any chance
   of multiple tenants/programs** — scope uniqueness to the correct container level (per-program)
   from the start, not retrofitted after a real collision is reported in production.
4. **Build the multi-tenant/multi-program container into the schema from day one** if there's any
   chance of running more than one "edition" per deployment. Retrofitting it means every
   previously-global entity needs a new scoping foreign key and a data backfill — and, per §18.1,
   it's easy to miss scoping some of them even when you do go back and add it.
5. **When adding composite uniqueness over columns that can legitimately be null for some rows**
   (e.g. house+number, which doesn't apply to admin-type accounts), make the "only enforce when all
   parts are actually set" condition explicit in the constraint — don't rely on a particular
   database engine's default NULL-handling behavior to get this right by accident.
6. **Don't carry two overlapping foreign-key-ish columns for the same relationship** (the original
   schema has both a legacy `user_id` and an actively-used `user` login-reference column on
   participants, left over from an old migration). Collapse to one clearly-named field.
7. **Don't ship a settings toggle that isn't actually wired into the behavior it claims to control**
   (§18.4) — either implement it for real or remove it; a toggle that silently does nothing is worse
   than no toggle.

---

## 20. Deployment & Operations

*(This section is intentionally split: stack-agnostic requirements first, then a labeled appendix of
gotchas specific to the original Python/Flask/shared-hosting stack — read that appendix only if
choosing a similar setup; otherwise the general requirements above it are what to carry forward.)*

### 20.1 Stack-Agnostic Deployment Requirements
- Must be deployable on **constrained shared/budget hosting** (no guaranteed root access, no
  guaranteed ability to run background daemons or long-lived processes beyond the web app itself,
  a process manager that may recycle worker processes and lazily reload code on file changes).
- Must support running against **whatever the host's available database engine is** — historically
  this meant supporting both a lightweight embedded database for local development (zero-setup) and
  a shared-hosting-provided relational database in production, via one codebase. Even if the new
  stack commits to a single database engine everywhere, keep local dev **and** production on the
  *same* engine to avoid an entire class of "works locally, breaks in production" bugs — this was a
  major, repeated source of pain in the original build (see 20.2).
  same one used in production (see 20.2 below).
- HTTPS is a hard requirement for any real deployment (forces secure cookies, see §17.3); certificate
  provisioning/renewal must be automatable without requiring paid/manual intervention on every
  renewal cycle.
- No server-side PDF generation dependency — printable views (call sheets, result sheets, schedules)
  rely on the browser's print-to-PDF, by design (§14.4) — keep this as a hosting-simplicity
  constraint unless the new environment genuinely has more headroom.
- Provide an admin-triggerable, on-demand smoke-test run against an isolated database, reachable
  from the deployed app itself (§17.4) — valuable specifically because shell/SSH access to
  constrained hosting may be limited or slow to use.
- Provide a one-click full database backup download and a granular per-collection data export, both
  admin-only, for off-site backup without needing infrastructure-level tooling.
- Session secret/config must fail fast (refuse to start) in a production posture if left at a
  placeholder default (§17.3).

### 20.2 Appendix — Gotchas Specific to the Original Stack (Flask + SQLite/MySQL + shared hosting via a Python WSGI process manager)

Only relevant if the rebuild uses a similar combination (Python/WSGI web framework + a
budget/shared-hosting provider + dual local-SQLite/production-MySQL support). Each of these cost
real debugging time the first time around:

- **A LIMIT clause inside an `IN (subquery)` may be silently unsupported by some MySQL-compatible
  engines (MariaDB rejected it outright with a clear error once discovered), while SQLite allows it
  freely.** If a query needs "top N of X, then filter something else by membership in that set,"
  wrap the inner query in its own derived subquery/table rather than nesting a LIMIT directly inside
  an IN clause — this exact pattern caused a genuine, hard-to-trace production-only 500 error on
  every write in the original app (the culprit was an audit-log-trimming query, not the visible
  feature at all) that never reproduced locally or in tests because local development used a
  different database engine than production.
- **A debug/development-mode flag set only inside a framework's own `if __name__ == "__main__"`
  block has no effect when a WSGI process manager imports the application module directly** — such a
  flag must be set at module scope (or via whatever the framework's equivalent "always apply this
  regardless of entry point" mechanism is), or it will silently never take effect in production while
  appearing to work in every local test.
- **A process manager that "lazily" reloads on a touched restart-trigger file may not always fully
  propagate updated environment variables to already-running worker processes** — a genuinely fresh
  worker restart (not just a reload signal) may be needed after changing environment configuration,
  and this is worth testing explicitly rather than assuming a config change took effect just because
  the app didn't error.
- **Composite text-based primary keys may need explicit key-length specifications** on some MySQL-
  compatible engines that don't need it under SQLite.
- **Reserved SQL keywords used as unquoted column names** (e.g. a column literally named `read` or
  `key`) can work fine under one engine and fail under another — quote all identifiers defensively,
  or avoid reserved words as column names entirely.
- **A literal `%` character inside a `LIKE` pattern can collide with a database driver's own
  parameter-substitution syntax** if the driver uses `%s`-style placeholders — escape or
  parameterize carefully.
- **Pooled database connections can go stale after periods of idleness on shared hosting** and need
  an explicit reconnect-if-needed check before use, rather than assuming a long-lived connection
  handle stays valid indefinitely.
- **A shared host's system-level scripting runtime may be an old version** with a smaller standard
  library API surface than what's used in local development — verify any admin-triggered
  server-side scripting (like the self-tests panel) actually works against the real production
  runtime version, not just the newer local dev version.
- **Free/managed HTTPS certificate auto-issuance may simply be unavailable on some shared-hosting
  tiers** despite being advertised generally for the platform — have a fallback plan (a
  webroot-validated certificate authority client run via user-space `pip`/equivalent, installed via
  whatever the host's control panel API supports) plus an automated renewal schedule (e.g. a cron
  job checking daily, which is a no-op most days and only acts near actual expiry).
- **A hosting platform's default "coming soon" placeholder page can shadow the app at the bare root
  domain** even after deploying — a narrowly-scoped redirect rule (matching only the exact root
  path) may be needed, taking care not to disturb any other applications/subdirectories sharing the
  same hosting account.
- **Rapid, unpaced local testing against a file-based embedded database can produce "database is
  locked" errors** from many concurrent short-lived connections piling up — pace test/script
  execution, and ensure connections are properly closed rather than leaked per-request.
