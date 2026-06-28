# Deployment Guide — Sports Meet on GoDaddy cPanel

The app is a Python 3 / Flask application. The production target is GoDaddy shared hosting
using cPanel's **Setup Python App** (Passenger/WSGI) with a **MySQL** database.

---

## Quick reference — env vars

| Variable | Required | Example |
|----------|----------|---------|
| `SPORTS_SECRET_KEY` | ✅ | 48-char random string (see below) |
| `SPORTS_DEBUG` | ✅ | `0` |
| `SPORTS_DB_ENGINE` | ✅ | `mysql` |
| `SPORTS_MYSQL_HOST` | ✅ | `localhost` |
| `SPORTS_MYSQL_USER` | ✅ | `cpanelusername_sports` |
| `SPORTS_MYSQL_PASSWORD` | ✅ | your DB password |
| `SPORTS_MYSQL_DB` | ✅ | `cpanelusername_sports` |

Generate a secret key:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

## Stage 1 — MySQL database (cPanel)

1. cPanel → **MySQL Databases**
2. **Create database** — e.g. `cpanelusername_sports`
3. **Create user** — e.g. `cpanelusername_sports` with a strong password
4. **Add user to database** → grant **All Privileges**
5. Note: host is always `localhost` on GoDaddy shared hosting

> Tables are created automatically on first startup — no SQL import needed.

---

## Stage 2 — Upload code

**Option A — git clone via SSH** (recommended once GitHub push is complete):
```bash
ssh cpanelusername@yourdomain.com
mkdir ~/sports_meet && cd ~/sports_meet
git clone https://github.com/Akilan-Ramesh/sports.git .
```

**Option B — File Manager zip upload** (use if SSH/git not available):
1. Locally: zip the project, excluding `venv/`, `data/`, `__pycache__/`
   ```bash
   zip -r sports_meet.zip . -x "venv/*" -x "data/*" -x "__pycache__/*" -x "*.pyc"
   ```
2. cPanel → **File Manager** → navigate to target folder → **Upload** the zip → **Extract**

---

## Stage 3 — Setup Python App (cPanel)

1. cPanel → **Setup Python App** → **Create Application**
2. Fill in:
   - **Python version**: highest available (3.9+)
   - **Application root**: path to the uploaded folder (e.g. `sports_meet`)
   - **Application URL**: your domain or subdirectory
   - **Application startup file**: `passenger_wsgi.py`
   - **Application Entry point**: `application`
3. Click **Create**

---

## Stage 4 — Install dependencies

In the **Setup Python App** interface, click **"Enter to the virtual environment"** to get a
command, or SSH and run:
```bash
source /home/cpanelusername/virtualenv/sports_meet/3.x/bin/activate
cd ~/sports_meet
pip install -r requirements.txt
pip install pymysql
```

---

## Stage 5 — Set environment variables

In **Setup Python App** → **Environment Variables** section, add each variable:

```
SPORTS_DEBUG             = 0
SPORTS_SECRET_KEY        = <generated 48-char key>
SPORTS_DB_ENGINE         = mysql
SPORTS_MYSQL_HOST        = localhost
SPORTS_MYSQL_USER        = cpanelusername_sports
SPORTS_MYSQL_PASSWORD    = <your db password>
SPORTS_MYSQL_DB          = cpanelusername_sports
```

Click **Save** after adding all variables.

---

## Stage 6 — First startup & DB initialisation

1. Click **Restart** in Setup Python App
2. Visit your app URL in a browser
3. On first request, `passenger_wsgi.py` calls `seed.ensure_seed()` which runs `init_db()`:
   - Creates all 15 `sports_*` tables in MySQL
   - Creates the `sports_users` VIEW
   - Creates all 12 named indexes
   - Seeds sample data (admin login, demo participants)
4. Log in with `admin` / `password` — you will be forced to set a new password immediately

---

## Stage 7 — Domain & HTTPS

1. cPanel → **Domains** — ensure domain points to the app directory
2. cPanel → **SSL/TLS** → **AutoSSL** → enable (free Let's Encrypt certificate)
3. With `SPORTS_DEBUG=0`, all session cookies are automatically `Secure` + `HttpOnly` + `SameSite=Lax`

---

## Stage 8 — Verify

- [ ] App loads at `https://yourdomain.com`
- [ ] Login with `admin` / `password` → forced password change
- [ ] Admin → Self-tests panel → **Run tests** → all pass
- [ ] HTTPS padlock visible in browser
- [ ] On mobile: browser offers "Add to Home Screen" (PWA)

---

## Ongoing deploys (after initial setup)

```bash
ssh cpanelusername@yourdomain.com
cd ~/sports_meet
git pull origin main
touch tmp/restart.txt        # signals Passenger to reload
```

Or: cPanel → Setup Python App → **Restart**.

---

## Security notes

- `SPORTS_DEBUG=0` enables secure cookies, enforces secret key, disables debugger/reloader
- CSRF tokens are auto-injected on every POST form — always on regardless of debug mode
- Login throttling (5 attempts → 60s lockout) and 30-minute session timeout are always on
- Admin → Import/Backup lets you download a JSON export for off-site backup

---

## Local development (SQLite, no MySQL needed)

```bash
./run.sh          # starts on http://127.0.0.1:3003
```

Leave `SPORTS_DB_ENGINE` unset (or set to `sqlite`) — the app uses a local SQLite file at
`data/sportsmeet.db`. No MySQL required for local dev.
