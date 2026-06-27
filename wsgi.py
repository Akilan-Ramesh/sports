"""WSGI entry point for production servers (gunicorn / uWSGI).

Initialises the database (additive migrations + idempotent sample seed) on import,
then exposes the Flask app as ``application``.

Run in production, e.g.:
    SPORTS_DEBUG=0 SPORTS_SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')" \
        gunicorn -w 4 -b 127.0.0.1:8000 wsgi:application

See DEPLOY.md for the full checklist.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seed
seed.ensure_seed()

from app import app as application  # noqa: E402
app = application
