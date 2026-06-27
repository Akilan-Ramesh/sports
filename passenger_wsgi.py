"""Passenger WSGI entry point for cPanel "Setup Python App" (GoDaddy shared).

Point the application's startup file at this module; Passenger imports the
`application` callable. On first run we seed initial data.
"""
import os
import sys

# Ensure the app directory is importable.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seed
seed.ensure_seed()

from app import app as application  # noqa: E402

if __name__ == "__main__":
    application.run()
