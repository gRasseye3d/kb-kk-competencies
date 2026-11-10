"""Fail-closed space and page access checks."""
from . import db


def space(user, space_id, write=False, conn=None):
    if not user:
        return False
    row = db.query('''SELECT s.id FROM spaces s
        LEFT JOIN space_members sm ON sm.space_id=s.id AND sm.user_id=%s
        WHERE s.id=%s AND (s.owner_id=%s OR %s OR
          (sm.role IS NOT NULL AND (NOT %s OR sm.role IN ('admin','editor'))) OR
          (NOT %s AND s.visibility='internal'))''',
        (user['user_id'], space_id, user['user_id'], 'admin' in user['roles'],
         write, write), one=True, conn=conn)
    return bool(row)


def page(user, p, write=False, conn=None):
    if not user or not p or p['deleted_at'] is not None or not space(user, p['space_id'], write, conn):
        return False
    if 'admin' in user['roles']:
        return True
    if write:
        if p['created_by'] != user['user_id'] or p['status'] in ('pending_review', 'archived'):
            return False
    elif p['status'] != 'published' and p['created_by'] != user['user_id']:
        if not (p['status'] == 'pending_review' and 'moderator' in user['roles']
                and space(user, p['space_id'], write=True, conn=conn)):
            return False
    restricted = db.query('''SELECT EXISTS(SELECT 1 FROM page_permissions WHERE page_id=%s) AS restricted,
        EXISTS(SELECT 1 FROM page_permissions pp WHERE pp.page_id=%s AND
            (pp.user_id=%s OR EXISTS(SELECT 1 FROM group_members gm WHERE gm.group_id=pp.group_id AND gm.user_id=%s))
            AND pp.permission IN ('view','edit','admin')) AS allowed''',
        (p['id'], p['id'], user['user_id'], user['user_id']), one=True, conn=conn)
    return not restricted['restricted'] or restricted['allowed'] or p['created_by'] == user['user_id']


def csrf(req):
    data = req.json()
    if not isinstance(data, dict):
        raise ValueError('JSON object required')
    if not req.user or data.get('csrf') != req.user['csrf_token']:
        raise PermissionError('Authentication or CSRF required')
    return data
