import hashlib
import hmac
import ipaddress
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

from . import db

ITERATIONS = 600_000


def cookie_name():
    name = os.environ.get('KB_SESSION_COOKIE_NAME', 'sid')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name):
        raise ValueError('Invalid session cookie name')
    return name


def dev_http_cookie(environ):
    """Opt-in HTTP PoC on the local machine only; never use proxy headers."""
    if os.environ.get('KB_DEV_HTTP_COOKIE') != '1':
        return False
    try:
        return (ipaddress.ip_address(environ.get('REMOTE_ADDR', '')).is_loopback
                and environ.get('wsgi.url_scheme') == 'http')
    except ValueError:
        return False


def cookie_attributes(environ):
    return 'HttpOnly; Path=/; SameSite=Lax' + ('' if dev_http_cookie(environ) else '; Secure')


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ITERATIONS)
    return f'pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}'


def verify_password(password, stored_hash):
    if not stored_hash:
        return False
    try:
        algorithm, count, salt, digest = stored_hash.split('$')
        if algorithm != 'pbkdf2_sha256' or not 100_000 <= int(count) <= 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(count))
        return hmac.compare_digest(actual, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


def allow_login(login, remote_addr):
    """Count every login attempt, including successes, across all WSGI workers."""
    name = login if isinstance(login, str) and len(login) <= 256 else '<invalid>'
    try:
        ip = str(ipaddress.ip_address(remote_addr))
    except (ValueError, TypeError):
        ip = '<unknown>'  # Never trust client-supplied forwarding headers.
    keys = (('account', name, 12), ('ip', ip, 30))
    allowed = True
    with db.transaction() as conn:
        for scope, key, limit in keys:
            digest = hashlib.sha256(key.encode()).hexdigest()
            # Atomic UPSERT serializes concurrent attempts for each key. A fixed
            # five-minute window expires using the database clock, not worker time.
            row = db.query('''INSERT INTO login_rate_limits(scope,key_hash,window_started,attempts)
                VALUES(%s,%s,statement_timestamp(),1)
                ON CONFLICT(scope,key_hash) DO UPDATE SET
                    attempts=CASE WHEN login_rate_limits.window_started <= statement_timestamp() - interval '5 minutes'
                        THEN 1 ELSE login_rate_limits.attempts+1 END,
                    window_started=CASE WHEN login_rate_limits.window_started <= statement_timestamp() - interval '5 minutes'
                        THEN statement_timestamp() ELSE login_rate_limits.window_started END
                RETURNING attempts''', (scope, digest), one=True, conn=conn)
            if row['attempts'] > limit:
                allowed = False
        # Opportunistic bounded cleanup, without a separate scheduler.
        if secrets.randbelow(128) == 0:
            db.execute("DELETE FROM login_rate_limits WHERE window_started < statement_timestamp() - interval '1 day'", conn=conn)
    return allowed


def create_session(user_id):
    token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    db.execute('INSERT INTO sessions(token,user_id,csrf_token,expires_at) VALUES(%s,%s,%s,%s)',
               (hashlib.sha256(token.encode()).hexdigest(), user_id, csrf,
                datetime.now(timezone.utc) + timedelta(hours=12)))
    return {'token': token, 'csrf': csrf}


def get_session(token):
    if not token or len(token) > 256:
        return None
    return db.query('''SELECT s.user_id, s.csrf_token, u.username, u.display_name
        FROM sessions s JOIN users u ON u.id=s.user_id
        WHERE s.token=%s AND s.expires_at > now() AND u.is_active''',
        (hashlib.sha256(token.encode()).hexdigest(),), one=True)


def destroy_session(token):
    if token:
        db.execute('DELETE FROM sessions WHERE token=%s', (hashlib.sha256(token.encode()).hexdigest(),))


def current_user(environ):
    # If development mode was enabled, never authenticate a non-loopback client.
    if os.environ.get('KB_DEV_HTTP_COOKIE') == '1' and not dev_http_cookie(environ):
        return None
    from http.cookies import SimpleCookie
    cookie = SimpleCookie()
    try:
        cookie.load(environ.get('HTTP_COOKIE', ''))
    except Exception:
        return None
    name = cookie_name()
    session = get_session(cookie[name].value) if name in cookie else None
    if session:
        session['roles'] = [r['code'] for r in db.query('''SELECT r.code FROM user_roles ur
            JOIN roles r ON r.id=ur.role_id WHERE ur.user_id=%s''', (session['user_id'],))]
    return session
