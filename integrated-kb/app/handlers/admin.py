import os

from .. import db, auth, audit, access
from ..utils import json_response
from .pages import valid


def uuid_id(value):
    parsed = valid(value)
    if not parsed:
        raise ValueError('Invalid UUID')
    return parsed


def register(r):
    for method, path, handler in (
        ('POST','/api/login',login), ('POST','/api/logout',logout), ('GET','/api/me',me),
        ('GET','/api/admin/spaces',list_spaces), ('POST','/api/admin/spaces',create_space),
        ('GET','/api/admin/users',list_users), ('POST','/api/admin/users',create_user),
        ('GET','/api/admin/departments',list_departments),
        ('POST','/api/admin/competency-roles',seed_competency_roles),
        ('PUT','/api/admin/users/<uid>/department',set_department),
        ('PUT','/api/admin/users/<uid>/roles',set_roles), ('DELETE','/api/admin/users/<uid>',delete_user),
        ('GET','/api/admin/avatar',get_avatar), ('PUT','/api/admin/profile',update_profile)):
        r.route(method,path)(handler)


def _admin(req):
    access.csrf(req)
    if not req.user or 'admin' not in req.user['roles']:
        raise PermissionError('Admin required')


def login(req):
    if auth_dev_is_remote(req):
        raise PermissionError('HTTP development mode only accepts local connections')
    data = req.json()
    if not auth.allow_login(data.get('login'), req.environ.get('REMOTE_ADDR')):
        return json_response({'error': 'Too many login attempts'}, '429 Too Many Requests',
                             headers=[('Retry-After', '300')])
    user = db.query('SELECT id,username,display_name,password_hash,is_active FROM users WHERE username=%s',
                    (data.get('login'),), one=True)
    if not user or not user['is_active'] or not auth.verify_password(data.get('password', ''), user['password_hash']):
        return json_response({'error': 'Invalid credentials'}, '401 Unauthorized')
    session = auth.create_session(user['id'])
    return json_response({'ok': True, 'csrf': session['csrf'], 'user': {'id': user['id'],
        'login': user['username'], 'full_name': user['display_name']}},
        headers=[('Set-Cookie', f"{auth.cookie_name()}={session['token']}; {auth.cookie_attributes(req.environ)}")])


def auth_dev_is_remote(req):
    return os.environ.get('KB_DEV_HTTP_COOKIE') == '1' and not auth.dev_http_cookie(req.environ)


def logout(req):
    access.csrf(req)
    auth.destroy_session(req.cookies.get(auth.cookie_name()))
    return json_response({'ok': True}, headers=[('Set-Cookie',
        f"{auth.cookie_name()}=; Max-Age=0; {auth.cookie_attributes(req.environ)}")])


def me(req):
    if not req.user:
        return json_response({'user': None})
    return json_response({'user': {'id': req.user['user_id'], 'login': req.user['username'],
        'full_name': req.user['display_name'], 'roles': req.user['roles'], 'csrf': req.user['csrf_token']}})


def list_spaces(req):
    if not req.user:
        return json_response([])
    spaces = db.query('''SELECT DISTINCT s.id,s.name,s.space_key FROM spaces s
        LEFT JOIN space_members sm ON sm.space_id=s.id AND sm.user_id=%s
        WHERE s.visibility='internal' OR s.owner_id=%s OR sm.user_id IS NOT NULL OR %s
        ORDER BY s.name''', (req.user['user_id'], req.user['user_id'], 'admin' in req.user['roles']))
    return json_response([dict(s, can_create=access.space(req.user, s['id'], write=True))
                          for s in spaces])


def create_space(req):
    _admin(req)
    data = req.json()
    name = data.get('name', '').strip()
    key = data.get('space_key', '').strip().upper()
    if not name or len(name) > 200 or not key or len(key) > 40 or not all(c.isascii() and (c.isalnum() or c == '_') for c in key):
        return json_response({'error': 'name and space_key required'}, '400 Bad Request')
    with db.transaction() as conn:
        sid = db.query('INSERT INTO spaces(space_key,name,owner_id) VALUES(%s,%s,%s) RETURNING id',
                       (key, name, req.user['user_id']), one=True, conn=conn)['id']
        audit.log(req.user['user_id'], 'space.create', 'space', sid, conn=conn)
    return json_response({'id': sid})


def get_avatar(req):
    return json_response({'avatar_url': None})


def update_profile(req):
    # Arbitrary data URLs were previously persisted and rendered as HTML; disabled until validated uploads exist.
    access.csrf(req)
    return json_response({'error': 'Avatar upload unavailable'}, '501 Not Implemented')


def list_users(req):
    if not req.user or 'admin' not in req.user['roles']:
        raise PermissionError('Admin required')
    return json_response(db.query('''SELECT u.id,u.username AS login,u.display_name AS full_name,
        CASE WHEN u.is_active THEN 'active' ELSE 'blocked' END AS status,u.department_id,
        d.name AS department_name,COALESCE(array_agg(r.code) FILTER(WHERE r.code IS NOT NULL),'{}') AS roles
        FROM users u LEFT JOIN departments d ON d.id=u.department_id
        LEFT JOIN user_roles ur ON ur.user_id=u.id LEFT JOIN roles r ON r.id=ur.role_id
        GROUP BY u.id,d.name ORDER BY u.username'''))


def list_departments(req):
    if not req.user or 'admin' not in req.user['roles']:
        raise PermissionError('Admin required')
    return json_response(db.query('''SELECT d.id,d.code,d.name,d.is_active,d.manager_id,
        COALESCE(array_agg(x.user_id::text) FILTER (WHERE x.user_id IS NOT NULL),'{}') AS responsible_ids
        FROM departments d LEFT JOIN department_kk_responsibles x ON x.department_id=d.id
        GROUP BY d.id ORDER BY d.name,d.id'''))


def seed_competency_roles(req):
    _admin(req)
    with db.transaction() as conn:
        db.execute('''INSERT INTO roles(code,name) VALUES
            ('manager','Руководитель подразделения'),('kk_responsible','Ответственный за КК')
            ON CONFLICT(code) DO NOTHING''',conn=conn)
        audit.log(req.user['user_id'],'roles.competency.seed',conn=conn)
    return json_response({'ok':True})


def department_id(conn, value):
    if value is None:
        return None
    did = uuid_id(value)
    if not db.query('SELECT 1 FROM departments WHERE id=%s AND is_active',
                    (did,),one=True,conn=conn):
        raise ValueError('Unknown or inactive department')
    return did


def create_user(req):
    _admin(req)
    data = req.json()
    if not all(isinstance(data.get(k),str) for k in ('login','email','full_name','password')):
        raise ValueError('login, email, name and password required')
    username = data['login'].strip()
    email = data['email'].strip()
    password = data.get('password', '')
    name = data['full_name'].strip()
    if not username or len(username) > 80 or not email or len(email) > 320 or not name or len(name) > 160 or len(password) < 12:
        return json_response({'error': 'login, email, name and password (12+ chars) required'}, '400 Bad Request')
    with db.transaction() as conn:
        did = department_id(conn,data.get('department_id'))
        uid = db.query('''INSERT INTO users(username,email,display_name,full_name,password_hash,department_id)
            VALUES(%s,%s,%s,%s,%s,%s) RETURNING id''',
            (username,email,name,name,auth.hash_password(password),did), one=True, conn=conn)['id']
        _assign_roles(conn, uid, data.get('roles', ['employee']))
        audit.log(req.user['user_id'], 'user.create', 'user', uid, conn=conn)
    return json_response({'id': uid})


def _assign_roles(conn, uid, roles):
    if (not isinstance(roles, list) or not roles or len(roles) > 10 or
            any(not isinstance(code,str) or not code for code in roles)):
        raise ValueError('Invalid roles')
    for code in set(roles):
        role = db.query('SELECT id FROM roles WHERE code=%s', (code,), one=True, conn=conn)
        if not role:
            raise ValueError('Unknown role')
        db.execute('INSERT INTO user_roles(user_id,role_id) VALUES(%s,%s)', (uid,role['id']), conn)


def set_roles(req, uid):
    _admin(req)
    uid = uuid_id(uid)
    if uid == uuid_id(str(req.user['user_id'])):
        raise PermissionError('Cannot change own roles')
    roles = req.json().get('roles')
    if not isinstance(roles,list):
        raise ValueError('Invalid roles')
    with db.transaction() as conn:
        if 'manager' not in roles and db.query('SELECT 1 FROM departments WHERE manager_id=%s',(uid,),one=True,conn=conn):
            raise ValueError('Remove manager assignment first')
        if 'kk_responsible' not in roles and db.query('SELECT 1 FROM department_kk_responsibles WHERE user_id=%s',
                                                     (uid,),one=True,conn=conn):
            raise ValueError('Remove responsible assignment first')
        db.execute('DELETE FROM user_roles WHERE user_id=%s', (uid,), conn)
        _assign_roles(conn, uid, roles)
        audit.log(req.user['user_id'], 'user.roles', 'user', uid, conn=conn)
    return json_response({'ok': True})


def delete_user(req, uid):
    _admin(req)
    uid = uuid_id(uid)
    if uid == uuid_id(str(req.user['user_id'])):
        raise PermissionError('Cannot disable self')
    with db.transaction() as conn:
        if db.query('SELECT 1 FROM departments WHERE manager_id=%s',(uid,),one=True,conn=conn) or db.query(
                'SELECT 1 FROM department_kk_responsibles WHERE user_id=%s',(uid,),one=True,conn=conn):
            raise ValueError('Remove department assignments first')
        db.execute('UPDATE users SET is_active=false WHERE id=%s', (uid,), conn)
        db.execute('DELETE FROM sessions WHERE user_id=%s', (uid,), conn)
        audit.log(req.user['user_id'], 'user.disable', 'user', uid, conn=conn)
    return json_response({'ok': True})


def set_department(req,uid):
    _admin(req)
    uid = uuid_id(uid)
    data = req.json()
    if 'department_id' not in data:
        raise ValueError('department_id required (null to clear)')
    with db.transaction() as conn:
        did = department_id(conn,data['department_id'])
        if db.query('SELECT 1 FROM departments WHERE manager_id=%s AND id IS DISTINCT FROM %s::uuid',
                    (uid,did),one=True,conn=conn):
            raise ValueError('Remove manager assignment first')
        if db.query('SELECT 1 FROM department_kk_responsibles WHERE user_id=%s AND department_id IS DISTINCT FROM %s::uuid',
                    (uid,did),one=True,conn=conn):
            raise ValueError('Remove responsible assignment first')
        if not db.execute('UPDATE users SET department_id=%s,updated_at=now() WHERE id=%s AND is_active',
                          (did,uid),conn):
            raise ValueError('Unknown or inactive user')
        audit.log(req.user['user_id'],'user.department','user',uid,conn=conn)
    return json_response({'ok':True})
