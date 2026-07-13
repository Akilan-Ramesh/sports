"""End-to-end regression suite for the Sports Meet app.

Self-contained: spins up the Flask app on a throwaway SQLite DB (sample data
seeded), exercises auth / RBAC / workflows / edge cases over HTTP, then tears the
server down. Never touches the real database.

Run directly:      python tests/test_app.py
Run under pytest:  pytest tests/test_app.py

Covered scenarios include the result-entry agreement workflow and its negative
cases (other captain must approve; it does NOT auto-publish or skip to the admin
when another team's captain is involved; a disagreement escalates to the admin).
Keep extending this file as features are added.
"""
import http.cookiejar
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import sqlite3
import json as _j

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------
# Server bootstrap (temp DB, no reloader, debug flag keeps cookies non-secure)
# --------------------------------------------------------------------------
def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start_server():
    tmp = tempfile.mkdtemp(prefix="sportsmeet-test-")
    db_path = os.path.join(tmp, "test.db")
    port = _free_port()
    env = dict(os.environ)
    # Drop Werkzeug reloader vars that may be inherited when this suite is launched
    # from inside a running dev server (they'd make the child reuse the parent socket).
    for k in ("WERKZEUG_SERVER_FD", "WERKZEUG_RUN_MAIN"):
        env.pop(k, None)
    env.update(SPORTS_DB=db_path, SPORTS_DEBUG="1", SPORTS_SECRET_KEY="test-secret")
    code = ("import seed; seed.ensure_seed(); import app; "
            "app.app.run(host='127.0.0.1', port=%d, debug=False, use_reloader=False)" % port)
    proc = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d" % port
    for _ in range(60):
        if proc.poll() is not None:
            raise RuntimeError("server process exited during startup")
        try:
            urllib.request.urlopen(base + "/login", timeout=1)
            break
        except Exception:
            time.sleep(0.25)
    else:
        proc.terminate()
        raise RuntimeError("server did not start in time")
    return proc, base, db_path


# --------------------------------------------------------------------------
# HTTP helpers (CSRF token auto-injected into every POST)
# --------------------------------------------------------------------------
class Session:
    def __init__(self, base):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def csrf(self):
        for c in self.jar:
            if c.name == "csrf_token":
                return c.value
        return ""

    def req(self, path, data=None):
        # Ensure we hold a CSRF token before any mutating POST.
        if data is not None and not self.csrf():
            try:
                self.op.open(self.base + path, timeout=15)
            except urllib.error.HTTPError:
                pass
        body = None
        if data is not None:
            data = dict(data)
            data.setdefault("_csrf", self.csrf())
            body = urllib.parse.urlencode(data, doseq=True).encode()
        rq = urllib.request.Request(self.base + path, data=body)
        try:
            r = self.op.open(rq, timeout=15)
            return r.getcode(), r.geturl(), r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, self.base + path, e.read().decode("utf-8", "replace")


def run_checks(base, db_path):
    R = []

    def ck(name, ok, detail=""):
        R.append((bool(ok), name, detail))

    def session():
        return Session(base)

    def login(u, p):
        s = session()
        s.req("/login")  # obtain CSRF token first
        code, url, body = s.req("/login", {"username": u, "password": p})
        return s, (code, url, body)

    def db(q, a=()):
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        rows = [dict(x) for x in c.execute(q, a)]
        c.close()
        return rows

    def elig_for(s):
        return db("SELECT p.id, p.team FROM sports_participants p JOIN sports_signups su ON su.participant_id=p.id "
                  "WHERE su.sport_id=? AND p.archived=0 "
                  "AND (? IS NULL OR p.category=?) AND (? IS NULL OR p.division=?)",
                  (s["sport_id"], s["age_category"], s["age_category"], s["gender"], s["gender"]))

    def score_form(s, eids, base_place=1):
        form = {}
        if s["scoring_mode"] == "measured":
            for i, pid in enumerate(eids):
                form["p_%s_r1" % pid] = str(base_place + i)
        else:
            for i, pid in enumerate(eids, base_place):
                form["p_%s_place" % pid] = str(i)
        return form

    # ---- anon + auth ----
    anon = session()
    code, url, body = anon.req("/")
    ck("anon / -> login", url.endswith("/login") or "Log In" in body, url)
    code, url, body = anon.req("/admin")
    ck("anon /admin -> login", "/login" in url, url)

    admin, (c, u, b) = login("admin", "nicknick")
    ck("admin login ok", "Sign out" in b and not u.endswith("/login"))
    _, (c, u, b) = login("admin", "WRONGPW")
    ck("admin bad pw rejected", "Invalid" in b and u.endswith("/login"))

    cap, _ = login("captain_smashers", "nicknick")
    ply, _ = login("player1", "nicknick")

    # ---- CSRF ----
    bare = session()
    bare.req("/login")  # gets a session + token
    # POST without a token must be rejected.
    no_tok = urllib.request.Request(base + "/login",
                                    data=urllib.parse.urlencode({"username": "admin",
                                                                 "password": "nicknick"}).encode())
    # The 400 is now caught by a handler that redirects back with a flash
    # message instead of a dead-end error page - so a bare urllib request
    # (which follows redirects) lands on 200, not the raw 400 itself. The
    # security property that matters is unchanged: login was never processed.
    resp2 = bare.op.open(no_tok, timeout=15)
    c2, body2 = resp2.getcode(), resp2.read().decode()
    ck("CSRF: POST without token rejected + bounced back",
       c2 == 200 and "reload and try again" in body2 and "Sign out" not in body2, str(c2))

    # ---- no referee remnants ----
    ck("no referee role in any user",
       db("SELECT COUNT(*) n FROM sports_users WHERE roles LIKE '%referee%'")[0]["n"] == 0)

    # ---- admin sweep ----
    admin_paths = ["/", "/sports-status", "/results", "/signup", "/participants",
                   "/volunteers", "/score", "/approvals", "/admin", "/admin/sports",
                   "/admin/sports/new", "/sac", "/sac/new", "/admin/sport-categories",
                   "/admin/age-categories", "/admin/teams", "/admin/users",
                   "/admin/announcements", "/admin/settings", "/admin/data",
                   "/admin/reset", "/admin/team-selection", "/notifications"]
    for p in admin_paths:
        code, url, body = admin.req(p)
        ck("admin GET %s = 200" % p, code == 200, str(code))

    # ---- RBAC ----
    for p in ["/admin", "/admin/sports", "/sac", "/participants", "/score", "/admin/users"]:
        code, _, _ = ply.req(p)
        ck("player BLOCKED %s" % p, code == 403, str(code))
    for p in ["/admin", "/admin/sports", "/sac"]:
        code, _, _ = cap.req(p)
        ck("captain BLOCKED %s" % p, code == 403, str(code))
    for p in ["/score", "/participants", "/approvals"]:
        code, _, _ = cap.req(p)
        ck("captain CAN %s" % p, code == 200, str(code))

    other = db("SELECT id FROM sports_participants WHERE team IS NOT NULL AND team!='smashers' LIMIT 1")
    if other:
        code, _, _ = cap.req("/participants/%s/edit" % other[0]["id"])
        ck("captain BLOCKED other-team edit", code == 403, str(code))

    # ---- 404s ----
    for p in ["/results/NOPE", "/score/NOPE", "/sac/NOPE/edit", "/callsheet/NOPE"]:
        code, _, _ = admin.req(p)
        ck("404 %s" % p, code == 404, str(code))

    # ---- player signup gating ----
    prow = db('SELECT p.* FROM sports_participants p JOIN sports_users u ON u.id=p."user" WHERE u.username=\'player1\'')
    if prow:
        pid = prow[0]["id"]; pcat = prow[0]["category"]; pdiv = prow[0]["division"]
        open_ok = set(r["sport_id"] for r in db(
            "SELECT sport_id FROM sports_sport_age_categories WHERE archived=0 AND status='registration_open' "
            "AND (age_category IS NULL OR age_category=?) AND (gender IS NULL OR gender=?)", (pcat, pdiv)))
        all_sports = set(r["id"] for r in db("SELECT id FROM sports_sports WHERE archived=0"))
        # Only a sport the player is NOT already signed up to — an existing (seeded)
        # sign-up is legitimately kept by the route even once registration closes /
        # the result is locked, which is correct behaviour, not a rejection.
        current = set(r["sport_id"] for r in
                      db("SELECT sport_id FROM sports_signups WHERE participant_id=?", (pid,)))
        inelig = sorted(all_sports - open_ok - current)  # sorted = deterministic
        if inelig:
            ply.req("/signup", {"pid": pid, "events": list(open_ok) + [inelig[0]]})
            after = db("SELECT COUNT(*) n FROM sports_signups WHERE participant_id=? AND sport_id=?", (pid, inelig[0]))[0]["n"]
            ck("player signup rejects new ineligible sport", after == 0, "after=%s" % after)
        if other:
            code, _, _ = ply.req("/signup", {"pid": other[0]["id"], "events": []})
            ck("player BLOCKED signup of other", code == 403, str(code))

    # ---- player calendar ----
    code, _, body = ply.req("/sports-status")
    ck("player lands on My events", code == 200 and "Signed up" in body and "Can sign up" in body, str(code))
    code, _, body = ply.req("/sports-status?view=all")
    ck("player all-events shows key", code == 200 and "You can play" in body, str(code))
    code, _, body = ply.req("/sports-status?view=all&sched=unscheduled")
    ck("player not-scheduled filter 200", code == 200, str(code))

    # ---- admin direct publish ----
    # Individual, draft, and already-held (date today or earlier) so results can be entered.
    draft = db("SELECT sac.* FROM sports_sport_age_categories sac WHERE sac.archived=0 AND sac.finalised=0 "
               "AND sac.approval_status='draft' AND sac.event_format='individual' "
               "AND sac.date IS NOT NULL AND sac.date<=date('now') ORDER BY sac.id")
    pub = None
    for s in draft:
        elig = elig_for(s)
        if len(elig) >= 2:
            pub = (s, [e["id"] for e in elig]); break
    if pub:
        s, eids = pub
        admin.req("/score/%s" % s["id"], score_form(s, eids))
        admin.req("/score/%s/submit" % s["id"], {})
        row = db("SELECT * FROM sports_sport_age_categories WHERE id=?", (s["id"],))[0]
        ck("admin publish -> approved+finalised+completed",
           row["approval_status"] == "approved" and row["finalised"] == 1 and row["status"] == "completed",
           str((row["approval_status"], row["finalised"], row["status"])))
        pts = db("SELECT points FROM sports_results WHERE sac_id=?", (s["id"],))
        ck("admin scoring computed points", any(r["points"] for r in pts), str(pts[:3]))
    else:
        ck("found draft SAC w/ eligible to publish", False, "none found")

    # ---- captain enters score; cross-captain agreement (+ negative cases) ----
    used = {pub[0]["id"]} if pub else set()
    multi = None
    for s in draft:
        if s["id"] in used:
            continue
        elig = elig_for(s)
        teams = set(e["team"] for e in elig if e["team"])
        if "smashers" in teams and len(teams) >= 2:
            multi = (s, [e["id"] for e in elig], teams); break
    if multi:
        s, eids, teams = multi
        used.add(s["id"])
        cap.req("/score/%s" % s["id"], score_form(s, eids))
        cap.req("/score/%s/submit" % s["id"], {})
        row = db("SELECT * FROM sports_sport_age_categories WHERE id=?", (s["id"],))[0]
        votes = {v["captain_id"]: v["decision"] for v in db("SELECT * FROM sports_score_votes WHERE sac_id=?", (s["id"],))}
        smash_cap = db("SELECT id FROM sports_users WHERE username='captain_smashers'")[0]["id"]
        # NEGATIVE CASE 1: must NOT auto-publish — it goes to pending, not approved.
        ck("captain submit -> pending (not auto-approved)", row["approval_status"] == "pending", row["approval_status"])
        ck("not finalised while pending", row["finalised"] == 0, str(row["finalised"]))
        ck("enterer (smashers) auto-agreed", votes.get(smash_cap) == "agree", str(votes))
        # NEGATIVE CASE 2: the OTHER captain (not the admin) is the one asked.
        ck("other captain has a pending vote",
           any(d == "pending" for c2, d in votes.items() if c2 != smash_cap), str(votes))
        adm_id = db("SELECT id FROM sports_users WHERE username='admin'")[0]["id"]
        ck("admin is NOT a voter", adm_id not in votes, str(list(votes)))
        other_team = [t for t in teams if t != "smashers"][0]
        ocap = db("SELECT username FROM sports_users WHERE team=? AND roles LIKE '%captain%' AND disabled=0 LIMIT 1",
                  (other_team,))
        if ocap:
            pwmap = {"captain_hammers": "nicknick", "captain_warriors": "nicknick",
                     "captain_titans": "nicknick", "play_cap": "nicknick"}
            oop, _ = login(ocap[0]["username"], pwmap.get(ocap[0]["username"], "x"))
            # Positive: other captain agrees -> published, no admin needed.
            oop.req("/approvals/%s/vote" % s["id"], {"decision": "agree"})
            r2 = db("SELECT approval_status, finalised FROM sports_sport_age_categories WHERE id=?", (s["id"],))[0]
            ck("all captains agree -> published (no admin)",
               r2["approval_status"] == "approved" and r2["finalised"] == 1, str(dict(r2)))

    # NEGATIVE CASE 3: a disagreement escalates to the admin (disputed).
    multi2 = None
    for s in draft:
        if s["id"] in used:
            continue
        teams = set(e["team"] for e in elig_for(s) if e["team"])
        if "smashers" in teams and len(teams) >= 2:
            multi2 = (s, [e["id"] for e in elig_for(s)], teams); break
    if multi2:
        s, eids, teams = multi2
        used.add(s["id"])
        cap.req("/score/%s" % s["id"], score_form(s, eids))
        cap.req("/score/%s/submit" % s["id"], {})
        other_team = [t for t in teams if t != "smashers"][0]
        ocap = db("SELECT username FROM sports_users WHERE team=? AND roles LIKE '%captain%' AND disabled=0 LIMIT 1",
                  (other_team,))
        pwmap = {"captain_hammers": "nicknick", "captain_warriors": "nicknick",
                 "captain_titans": "nicknick", "play_cap": "nicknick"}
        oop, _ = login(ocap[0]["username"], pwmap.get(ocap[0]["username"], "x"))
        before = db("SELECT COUNT(*) n FROM sports_notifications WHERE type='admin' AND link='/approvals'")[0]["n"]
        oop.req("/approvals/%s/vote" % s["id"], {"decision": "disagree"})
        st = db("SELECT approval_status FROM sports_sport_age_categories WHERE id=?", (s["id"],))[0]["approval_status"]
        ck("captain disagree -> disputed", st == "disputed", st)
        after = db("SELECT COUNT(*) n FROM sports_notifications WHERE type='admin' AND link='/approvals'")[0]["n"]
        ck("disagree notifies admin", after > before, "%d->%d" % (before, after))
        admin.req("/approvals/%s/admin" % s["id"], {"action": "approve"})
        st2 = db("SELECT approval_status, finalised FROM sports_sport_age_categories WHERE id=?", (s["id"],))[0]
        ck("admin override -> approved+finalised",
           st2["approval_status"] == "approved" and st2["finalised"] == 1, str(dict(st2)))

    # NEGATIVE CASE 4: single-team event (no other captain) -> ADMIN decides, NOT auto-published.
    single = None
    for s in draft:
        if s["id"] in used:
            continue
        elig = elig_for(s)
        teams = set(e["team"] for e in elig if e["team"])
        if teams == {"smashers"} and len(elig) >= 1:
            single = (s, [e["id"] for e in elig]); break
    if single:
        s, eids = single
        used.add(s["id"])
        before = db("SELECT COUNT(*) n FROM sports_notifications WHERE type='admin' AND link='/approvals'")[0]["n"]
        cap.req("/score/%s" % s["id"], score_form(s, eids))
        cap.req("/score/%s/submit" % s["id"], {})
        r3 = db("SELECT approval_status, finalised FROM sports_sport_age_categories WHERE id=?", (s["id"],))[0]
        ck("single-captain entry -> admin pending (not auto-published)",
           r3["approval_status"] == "pending" and r3["finalised"] == 0, str(dict(r3)))
        after = db("SELECT COUNT(*) n FROM sports_notifications WHERE type='admin' AND link='/approvals'")[0]["n"]
        ck("single-captain entry notifies admin", after > before, "%d->%d" % (before, after))
        # admin then decides -> published
        admin.req("/approvals/%s/admin" % s["id"], {"action": "approve"})
        r4 = db("SELECT approval_status, finalised FROM sports_sport_age_categories WHERE id=?", (s["id"],))[0]
        ck("admin approves single-captain -> published",
           r4["approval_status"] == "approved" and r4["finalised"] == 1, str(dict(r4)))

    # captain cannot score an event their team isn't in
    notmine = None
    for s in draft:
        if s["id"] in used:
            continue
        teams = set(e["team"] for e in elig_for(s) if e["team"])
        if teams and "smashers" not in teams:
            notmine = s; break
    if notmine:
        code, _, _ = cap.req("/score/%s" % notmine["id"])
        ck("captain BLOCKED scoring uninvolved SAC", code == 403, str(code))

    # ---- pending roster flow ----
    freep = db("SELECT id FROM sports_participants WHERE team IS NULL AND pending_team IS NULL AND archived=0 ORDER BY id")
    if len(freep) >= 2:
        claim = freep[0]["id"]
        cap.req("/participants/%s/assign" % claim, {"team": "smashers"})
        row = db("SELECT team, pending_team, name FROM sports_participants WHERE id=?", (claim,))[0]
        ck("captain claim -> pending (not on team)",
           row["team"] is None and row["pending_team"] == "smashers", str(dict(row)))
        # roster request now shows on the Approvals page (consolidated)
        code, _, body = admin.req("/approvals")
        ck("roster request shows on Approvals page",
           "Roster requests" in body and row["name"] in body, str(code))
        admin.req("/participants/%s/roster/approve" % claim, {})
        row = db("SELECT team, pending_team FROM sports_participants WHERE id=?", (claim,))[0]
        ck("admin approves roster -> on team",
           row["team"] == "smashers" and row["pending_team"] is None, str(dict(row)))
        claim2 = freep[1]["id"]
        cap.req("/participants/%s/assign" % claim2, {"team": "smashers"})
        admin.req("/participants/%s/roster/reject" % claim2, {})
        row = db("SELECT team, pending_team FROM sports_participants WHERE id=?", (claim2,))[0]
        ck("admin rejects roster -> still free",
           row["team"] is None and row["pending_team"] is None, str(dict(row)))

    # captain new player -> pending
    cap.req("/participants/new", {"name": "QA Pending Kid", "division": "Male", "birth_year": "2010"})
    npd = db("SELECT team, pending_team FROM sports_participants WHERE name='QA Pending Kid'")
    ck("captain new player held pending",
       bool(npd) and npd[0]["team"] is None and npd[0]["pending_team"] == "smashers", str(npd))

    # ---- parent-child manage pages ----
    cat_inuse = db("SELECT id FROM sports_sport_categories WHERE id IN (SELECT category_id FROM sports_sports) LIMIT 1")
    if cat_inuse:
        cid = cat_inuse[0]["id"]
        code, _, body = admin.req("/admin/sport-categories/%s/manage" % cid)
        ck("category manage page 200", code == 200 and "In use" in body, str(code))
        admin.req("/admin/sport-categories", {"action": "delete", "id": cid})
        ck("in-use category not hard-deleted",
           db("SELECT COUNT(*) n FROM sports_sport_categories WHERE id=?", (cid,))[0]["n"] == 1)
    inuse = db("SELECT id FROM sports_sports WHERE id IN (SELECT sport_id FROM sports_sport_age_categories) LIMIT 1")
    if inuse:
        sid = inuse[0]["id"]
        code, _, body = admin.req("/admin/sports/%s/manage" % sid)
        ck("sport manage page 200", code == 200 and "In use" in body, str(code))
        admin.req("/admin/sports_sports/%s/delete" % sid, {})
        ck("in-use sport delete blocked", db("SELECT COUNT(*) n FROM sports_sports WHERE id=?", (sid,))[0]["n"] == 1)

    # ---- archive: sport archive cascades to its Sport Event rows, and back ----
    sp = db("SELECT s.id FROM sports_sports s WHERE s.archived=0 AND EXISTS "
            "(SELECT 1 FROM sports_sport_age_categories sac WHERE sac.sport_id=s.id AND sac.archived=0) LIMIT 1")
    if sp:
        spid = sp[0]["id"]
        admin.req("/admin/sports/%s/archive" % spid, {})
        ck("sport archive sets archived",
           db("SELECT archived FROM sports_sports WHERE id=?", (spid,))[0]["archived"] == 1)
        ck("sport archive cascades to its events",
           db("SELECT COUNT(*) n FROM sports_sport_age_categories WHERE sport_id=? AND archived=0", (spid,))[0]["n"] == 0)
        admin.req("/admin/sports/%s/archive" % spid, {})  # toggle back
        ck("sport unarchive cascades back",
           db("SELECT archived FROM sports_sports WHERE id=?", (spid,))[0]["archived"] == 0
           and db("SELECT COUNT(*) n FROM sports_sport_age_categories WHERE sport_id=? AND archived=1", (spid,))[0]["n"] == 0)

    # ---- archive: a single Sport Event (SAC) toggles ----
    sac1 = db("SELECT id FROM sports_sport_age_categories WHERE archived=0 LIMIT 1")
    if sac1:
        scid = sac1[0]["id"]
        admin.req("/sac/%s/archive" % scid, {})
        ck("sport event archive works",
           db("SELECT archived FROM sports_sport_age_categories WHERE id=?", (scid,))[0]["archived"] == 1)
        admin.req("/sac/%s/archive" % scid, {})
        ck("sport event unarchive works",
           db("SELECT archived FROM sports_sport_age_categories WHERE id=?", (scid,))[0]["archived"] == 0)

    # ---- archive: a participant toggles ----
    pa = db('SELECT id FROM sports_participants WHERE archived=0 AND "user" IS NULL AND team IS NOT NULL '
            'AND id NOT IN (SELECT participant FROM sports_results) LIMIT 1')
    if pa:
        ppid = pa[0]["id"]
        admin.req("/participants/%s/archive" % ppid, {})
        ck("participant archive works",
           db("SELECT archived FROM sports_participants WHERE id=?", (ppid,))[0]["archived"] == 1)
        admin.req("/participants/%s/archive" % ppid, {})
        ck("participant unarchive works",
           db("SELECT archived FROM sports_participants WHERE id=?", (ppid,))[0]["archived"] == 0)
        # delete with no results -> hard delete
        admin.req("/sports_participants/%s/delete" % ppid, {})
        ck("participant without results hard-deleted",
           db("SELECT COUNT(*) n FROM sports_participants WHERE id=?", (ppid,))[0]["n"] == 0)

    # ---- delete: a participant WITH results is archived, not removed ----
    pres = db("SELECT r.participant FROM sports_results r JOIN sports_participants p ON p.id=r.participant "
              "WHERE p.archived=0 LIMIT 1")
    if pres:
        rpid = pres[0]["participant"]
        admin.req("/sports_participants/%s/delete" % rpid, {})
        ck("participant with results archived not deleted",
           db("SELECT archived FROM sports_participants WHERE id=?", (rpid,))[0]["archived"] == 1)

    # ---- sports dup-name guard ----
    dup = db("SELECT name FROM sports_sports LIMIT 1")[0]["name"]
    admin.req("/admin/sports/new", {"name": dup, "category_id": ""})
    ck("sport duplicate-name blocked", db("SELECT COUNT(*) n FROM sports_sports WHERE name=?", (dup,))[0]["n"] == 1)

    # ---- age overlap rejection ----
    catlist = _j.loads(db("SELECT value FROM sports_settings WHERE key='categories'")[0]["value"])
    n_before = len(catlist)
    form = {"new_name": "QA Overlap", "new_min": "5", "new_max": "10"}
    for cobj in catlist:
        form["name_%s" % cobj["id"]] = cobj["name"]
        form["min_%s" % cobj["id"]] = cobj["min_age"]
        form["max_%s" % cobj["id"]] = cobj["max_age"]
    code, url, body = admin.req("/admin/age-categories", form)
    n_after = len(_j.loads(db("SELECT value FROM sports_settings WHERE key='categories'")[0]["value"]))
    ck("age overlap rejected (count unchanged)", n_after == n_before, "%d->%d" % (n_before, n_after))
    ck("age overlap shows message", "overlap" in body.lower())

    # ---- login throttle ----
    tj = session()
    tj.req("/login")
    for _ in range(6):
        code, url, body = tj.req("/login", {"username": "admin", "password": "x"})
    ck("login throttle after 6 fails", "Too many" in body)

    # ---- role switch safety ----
    psw, _ = login("player1", "nicknick")
    psw.req("/switch/admin")
    code, url, body = psw.req("/admin")
    ck("player can't switch to admin", code == 403, str(code))

    # ---- results filters & detail ----
    code, _, body = admin.req("/results?gender=Male&age=U18")
    ck("results filter 200", code == 200)
    done = db("SELECT id FROM sports_sport_age_categories WHERE finalised=1 LIMIT 1")
    if done:
        code, _, _ = admin.req("/results/%s" % done[0]["id"])
        ck("result_detail 200", code == 200)

    # ---- team-event model ----
    team_done = db("SELECT id FROM sports_sport_age_categories WHERE finalised=1 "
                   "AND event_format IN ('team','doubles') LIMIT 1")
    if team_done:
        code, _, body = admin.req("/results/%s" % team_done[0]["id"])
        ck("team result_detail 200 (Players column)", code == 200 and "Players" in body, str(code))
        # team results are scored per team (participant holds a team id)
        tr = db("SELECT participant FROM sports_results WHERE sac_id=?", (team_done[0]["id"],))
        team_ids = set(t["id"] for t in db("SELECT id FROM sports_teams"))
        ck("team results keyed by team", bool(tr) and all(r["participant"] in team_ids for r in tr),
           str([r["participant"] for r in tr][:3]))
    ck("team points 10/5 default",
       any(_j.loads(s["points"]).get("1") == 10 for s in
           db("SELECT points FROM sports_sport_age_categories WHERE event_format='team' AND points IS NOT NULL")), "")
    ck("measured points 3/2/1 default",
       any(_j.loads(s["points"]).get("1") == 3 for s in
           db("SELECT points FROM sports_sport_age_categories WHERE scoring_mode='measured' "
              "AND event_format='individual' AND points IS NOT NULL")), "")

    # ---- demo logins across age/gender exist ----
    ck("age/gender demo logins seeded",
       db("SELECT COUNT(*) n FROM sports_users WHERE username IN ('u18_male','a70_female','u9_male')")[0]["n"] == 3)

    return R


def main():
    proc, base, db_path = start_server()
    try:
        results = run_checks(base, db_path)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    fails = [r for r in results if not r[0]]
    print("\n==== TEST RESULTS ====")
    for ok, name, detail in results:
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else "   <<< " + detail))
    print("\n%d passed, %d FAILED, %d total" % (len(results) - len(fails), len(fails), len(results)))
    return 1 if fails else 0


def test_full_suite():
    """pytest entry point."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
