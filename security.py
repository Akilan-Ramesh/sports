"""Password hashing and strength validation.

Uses PBKDF2-HMAC-SHA256 from the standard library (hashlib) -- a strong,
salted, one-way hash equivalent to bcrypt for this purpose, with the practical
advantage of needing no compiled C extension on shared hosting.
"""
import hashlib
import hmac
import os
import re

_ITERATIONS = 200_000
_ALGO = "sha256"


def hash_password(password):
    """Return a self-describing hash string: pbkdf2$algo$iters$salt$hash."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac(_ALGO, password.encode("utf-8"), salt, _ITERATIONS)
    return "pbkdf2${}${}${}${}".format(
        _ALGO, _ITERATIONS, salt.hex(), dk.hex()
    )


def verify_password(password, stored):
    try:
        scheme, algo, iters, salt_hex, hash_hex = stored.split("$")
        if scheme != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac(
            algo, password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# --- Password strength (mirrors BRD 3.2.1) ---------------------------------

# Simple rule: a minimum length only (no complexity requirements).
PASSWORD_RULES = [
    ("length", "At least 6 characters", lambda p: len(p) >= 6),
]


def password_errors(password):
    """Return a list of unmet rule labels (empty list == strong enough)."""
    return [label for _key, label, test in PASSWORD_RULES if not test(password or "")]
