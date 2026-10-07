"""Logins: email + password accounts for the people who use ProfitDesk.

Passwords are never stored, only a salted scrypt hash of them. A successful
login hands the browser a random session token in an HttpOnly cookie; only a
SHA-256 of that token is kept, so a copy of the database can't be used to log
in. Sessions last 30 days. Too many wrong passwords from one address locks it
out for 15 minutes.
"""
import hashlib
import hmac
import re
import secrets
import time

import db

SESSION_COOKIE = "pd_session"
SESSION_DAYS = 30
MIN_PASSWORD = 10

_LOCK_WINDOW = 15 * 60
_LOCK_AFTER = 10
_failures: dict[str, list[float]] = {}


class AuthError(RuntimeError):
    pass


# ---------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                             n=2**14, r=8, p=1, dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), digest)


def _check_new_password(password: str):
    if len(password or "") < MIN_PASSWORD:
        raise AuthError(f"Use at least {MIN_PASSWORD} characters for the password.")


def _clean_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise AuthError("That doesn't look like an email address.")
    return email


# ---------------------------------------------------------------- accounts

def has_users() -> bool:
    return bool(db.list_users())


def create_user(email: str, name: str, password: str) -> int:
    email = _clean_email(email)
    _check_new_password(password)
    if db.get_user_by_email(email):
        raise AuthError("Someone with that email already has an account.")
    return db.create_user(email, (name or "").strip() or email.split("@")[0],
                          hash_password(password))


def change_password(user_id: int, current: str, new: str):
    user = db.get_user(user_id)
    if not user or not check_password(current or "", user["password_hash"]):
        raise AuthError("Your current password isn't right.")
    _check_new_password(new)
    db.set_user_password(user_id, hash_password(new))
    db.delete_sessions_for(user_id, keep=None)


# ---------------------------------------------------------------- logging in

def _locked(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _failures.get(ip, []) if now - t < _LOCK_WINDOW]
    _failures[ip] = recent
    return len(recent) >= _LOCK_AFTER


def log_in(email: str, password: str, ip: str) -> str:
    """Check the details and return a new session token."""
    if _locked(ip):
        raise AuthError("Too many wrong attempts. Wait 15 minutes and try again.")
    user = db.get_user_by_email((email or "").strip().lower())
    # Hash even when there's no such user, so timing doesn't reveal who exists.
    ok = check_password(password or "", user["password_hash"] if user
                        else "scrypt$00$00")
    if not (user and ok):
        _failures.setdefault(ip, []).append(time.time())
        raise AuthError("That email and password don't match.")
    _failures.pop(ip, None)
    token = secrets.token_urlsafe(32)
    db.create_session(_digest(token), user["id"], time.time() + SESSION_DAYS * 86400)
    return token


def log_out(token: str):
    if token:
        db.delete_session(_digest(token))


def user_for(token: str):
    """The logged-in user for a session token, or None."""
    if not token:
        return None
    return db.get_session_user(_digest(token), time.time())


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
