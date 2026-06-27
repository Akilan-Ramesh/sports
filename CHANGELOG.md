# Changelog

A running log of changes to the Sports Meet app. Format: what changed, which session/date.

---

## 2026-06-25

### UI / Admin
- **Teams page** — redesigned "Existing teams" table: new columns Created by (login username) and Last edited (username · date); removed buried audit paragraph from Name cell; renamed "Remove" → "Actions" column header
- **Age Categories page** — renamed column header "Remove" → "Actions"
- **Sports page** — simplified Actions column: shows Edit + In-use badge + Archive when sport has child events; shows Edit + Delete + Archive when sport has no children; removed `<details>` popup and "Delete…" manage-page link

### Security / Hardening (Now items N05–N08)
- **N05** — `login next=` redirect now validated to same-site only; external/protocol-relative URLs rejected
- **N06** — `|first`-on-empty guarded in `participant_form.html` (line 19) and `participants.html` (line 84); was crashing if team/category not found
- **N07** — removed dead `sac_schedule` route, `sac_schedule.html` template, `referee TEXT` column from schema, and `is_referee = False` from `current_user()`
- **N08** — pinned exact dependency versions in `requirements.txt` (Flask 3.1.3, Werkzeug 3.1.8, Jinja2 3.1.6, etc.)

---

## 2026-06-23 → 2026-06-25 (Account creation + auth overhaul)

- **Houses** — participants now have a house (VA/VB/VC/A/B/C/D) + unique 3-digit number per house; Admin house type has no number
- **Two-table auth** — split old `users` table into `admins` + `participants`; unified via read-only `users` VIEW; FK column named `user`; non-destructive migration
- **Forced password change** — admin-created accounts default to `password` and must change on first login
- **Security question/answer** — added to account creation and login; used for forgot-password recovery
- **Forgot-password flow** — two-step: look up username → answer security question → set new password
- **Participant Actions column** — Sign-up / Edit / Release / Archive / Unarchive / Delete / In-use badge; words not emojis; archived rows dimmed
- **Sports archive cascade** — archiving a sport now cascades to all child SAC rows; Archive button always enabled with confirm dialog
- **Wipe-all overhaul** — now clears age categories + all event data; keeps only admin logins
- **Participants above Team Selection** — nav tab order updated in Maintenance section
- **Archive/delete self-tests** — 9 new tests (sport cascade, SAC archive toggle, participant archive/delete); suite at 96/96

---

## 2026-06-20 → 2026-06-23 (Set 12 changes)

- **Team Events calendar filters** — age / gender / scheduling filter toolbar on player sports status view
- **Dashboard score box** — all-team standings table in "Your score" card; own team row highlighted
- **Player results redesign** — My / Team / Standings tabs; approval noise hidden from players
- **Multiple formats per sport+age+gender** — dupe check now includes `event_format`; same sport can run as Individual, Doubles, and Team simultaneously
- **Date/status guard** — New/Open status blocked on past-dated events
- **Captain lineup selection** — for Doubles/Team events, captains choose which signed-up players represent the team; stored in `event_lineups` table
