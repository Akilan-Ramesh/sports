# Deployment

The app runs on Python 3 + Flask with a SQLite database (no external services).
The built-in `python app.py` server is for **development only**. Use gunicorn (or
Passenger on cPanel) in production.

## 1. Install
```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

## 2. Required environment variables (production)
| Var | Value | Why |
|-----|-------|-----|
| `SPORTS_DEBUG` | `0` | Turns off the debugger/reloader, **enables secure cookies**, and enforces the secret key. |
| `SPORTS_SECRET_KEY` | a long random string | Signs session cookies. The app **refuses to start** in production without it. |
| `SPORTS_DB` | absolute path | Where the SQLite file lives (defaults to `data/sportsmeet.db`). |
| `SPORTS_PORT` | e.g. `8000` | Only used by the dev server. |

Generate a secret key:
```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## 3. Run with gunicorn
```bash
SPORTS_DEBUG=0 \
SPORTS_SECRET_KEY="<paste-generated-key>" \
./venv/bin/gunicorn -w 4 -b 127.0.0.1:8000 wsgi:application
```
`wsgi:application` runs the additive DB migration + idempotent sample seed on startup.
Put nginx/Apache (HTTPS) in front and proxy to `127.0.0.1:8000`.

## 4. Security notes (what `SPORTS_DEBUG=0` switches on)
- **Secure session cookies** — `Secure` (HTTPS-only), `HttpOnly`, `SameSite=Lax`.
- **Secret-key enforcement** — startup fails fast if the dev key is still in use.
- **CSRF protection** — every POST/PUT/PATCH/DELETE requires the per-session token
  (auto-added to forms; also accepted via the `X-CSRFToken` header). Always on.
- Login throttling and a 30-minute idle session timeout are always on.

## 5. Backups
Admin → Import / Backup downloads the full `.db` file. For a real deployment, also
schedule an OS-level copy of `SPORTS_DB` (e.g. a nightly `cron` job) off the box.

## cPanel / GoDaddy (Passenger)
Point "Setup Python App" at `passenger_wsgi.py`. Set the same environment variables in
the cPanel UI. Passenger imports `application` and runs the seed/migration on first load.
