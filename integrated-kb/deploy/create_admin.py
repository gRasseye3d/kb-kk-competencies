"""Interactive first-admin bootstrap. Run from kb/: python3 -m deploy.create_admin."""
import getpass
import os
import sys
import warnings

from app import db
from app.auth import hash_password


def create_admin(username, email, name, password):
    if (not username or len(username) > 80 or not email or len(email) > 320
            or not name or len(name) > 160 or len(password) < 12):
        raise ValueError('Login, email, name and a password of at least 12 characters required')
    with db.transaction() as conn:
        # Serialize bootstraps and block concurrent user inserts until commit.
        db.execute('LOCK TABLE users IN EXCLUSIVE MODE', conn=conn)
        if db.query('SELECT 1 FROM users LIMIT 1', one=True, conn=conn):
            raise ValueError('Refusing to bootstrap a nonempty users table')
        role = db.query("SELECT id FROM roles WHERE code='admin' FOR SHARE", one=True, conn=conn)
        if not role:
            raise ValueError('Create the admin role before running this script')
        user = db.query('''INSERT INTO users(username,email,display_name,password_hash)
            VALUES(%s,%s,%s,%s) RETURNING id''',
            (username, email, name, hash_password(password)), one=True, conn=conn)
        db.execute('INSERT INTO user_roles(user_id,role_id) VALUES(%s,%s)',
                   (user['id'], role['id']), conn=conn)


def main():
    if not os.environ.get('DATABASE_URL'):
        raise SystemExit('DATABASE_URL is required')
    if not sys.stdin.isatty():
        raise SystemExit('Use an interactive terminal without redirected stdin')
    username = input('Login: ').strip()
    email = input('Email: ').strip()
    name = input('Display name: ').strip()
    # Never fall back to an echoed password if a terminal cannot be opened.
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        password = getpass.getpass('Password (12+ characters): ')
    create_admin(username, email, name, password)
    print('First administrator created')


if __name__ == '__main__':
    main()
