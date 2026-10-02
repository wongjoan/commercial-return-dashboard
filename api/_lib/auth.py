"""Authentication: HMAC-signed, HttpOnly session cookie.

The token carries only the user id and expiry. Level and scope are re-read from the server-side directory on
every request, so editing the cookie, URL or frontend state cannot change what a user is authorised to see.

Prototype sign-in = shared demo access code + persona choice (see SECURITY.md). Production replaces
login() with Supabase Auth / company SSO; current_user() stays the single entry point for the API.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from . import store

COOKIE = "gc_session"
TTL_SECONDS = 8 * 3600
_dev_secret = secrets.token_bytes(32)
_attempts = {}  # ip -> [timestamps]; in-memory login throttle


class ConfigError(Exception):
    pass


def is_production():
    return bool(os.environ.get("VERCEL"))


def _secret():
    s = os.environ.get("SESSION_SECRET")
    if s:
        return s.encode()
    if is_production():
        raise ConfigError("SESSION_SECRET is not configured")
    return _dev_secret  # local dev: random per process, so sessions reset on restart


def access_code():
    code = os.environ.get("DEMO_ACCESS_CODE")
    if code:
        return code
    if is_production():
        raise ConfigError("DEMO_ACCESS_CODE is not configured")
    return "glocomp-demo"


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue(uid):
    payload = _b64(json.dumps({"sub": uid, "exp": int(time.time()) + TTL_SECONDS, "n": secrets.token_hex(6)}).encode())
    sig = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
    return payload + "." + sig


def verify(token):
    try:
        payload, sig = token.split(".", 1)
        good = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(_unb64(payload))
        if data.get("exp", 0) < time.time():
            return None
        return data.get("sub")
    except (ValueError, KeyError, TypeError):
        return None


def parse_cookies(header):
    out = {}
    for part in (header or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def current_user(headers):
    token = parse_cookies(headers.get("cookie")).get(COOKIE)
    uid = verify(token) if token else None
    return store.user(uid) if uid else None


def cookie_header(token, secure):
    attrs = [COOKIE + "=" + token, "Path=/", "HttpOnly", "SameSite=Strict", "Max-Age=%d" % TTL_SECONDS]
    if secure:
        attrs.append("Secure")
    return "; ".join(attrs)


def clear_cookie_header(secure):
    return COOKIE + "=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0" + ("; Secure" if secure else "")


def throttle_ok(ip):
    now = time.time()
    hits = [t for t in _attempts.get(ip, []) if now - t < 300]
    _attempts[ip] = hits
    return len(hits) < 10


def record_attempt(ip):
    _attempts.setdefault(ip, []).append(time.time())


def check_code(given):
    return hmac.compare_digest((given or "").encode(), access_code().encode())
