import json
import uuid
from .. import db, audit, access, video_links, content as safe_content
from ..utils import json_response


def register(r):
    r.route('GET', '/api/tree')(tree)
    r.route('GET', '/api/pages/<pid>')(get_page)
    r.route('POST', '/api/pages')(create_page)
    r.route('PUT', '/api/pages/<pid>')(update_page)
    r.route('DELETE', '/api/pages/<pid>')(delete_page)


def valid(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def _page(pid, conn=None, lock=False):
    if not valid(pid):
        return None
    return db.query('SELECT * FROM pages WHERE id=%s' + (' FOR UPDATE' if lock else ''), (pid,), one=True, conn=conn)


def _public(p):
    return {k: p[k] for k in ('id', 'title', 'parent_id', 'space_id', 'status', 'created_by', 'current_version')}


def tree(req):
    if not req.user:
        return json_response([])
    rows = db.query('''SELECT p.* FROM pages p JOIN spaces s ON s.id=p.space_id
        LEFT JOIN space_members sm ON sm.space_id=s.id AND sm.user_id=%s
        WHERE p.deleted_at IS NULL AND (s.owner_id=%s OR %s OR sm.user_id IS NOT NULL OR s.visibility='internal')
        ORDER BY p.title LIMIT 1000''', (req.user['user_id'], req.user['user_id'], 'admin' in req.user['roles']))
    return json_response([_public(p) for p in rows if access.page(req.user, p)])


def get_page(req, pid):
    p = _page(pid)
    if not access.page(req.user, p):
        return json_response({'error': 'not found'}, '404 Not Found')
    rev = db.query('SELECT content FROM page_revisions WHERE page_id=%s AND version=%s',
                   (pid, p['current_version']), one=True)
    result = _public(p)
    result['can_edit'] = access.page(req.user, p, write=True)
    result['content'] = safe_content.clean((rev or {}).get('content', {}).get('html', ''))
    result['labels'] = [r['name'] for r in db.query('''SELECT l.name FROM labels l JOIN page_labels pl ON l.id=pl.label_id
        WHERE pl.page_id=%s''', (pid,))]
    return json_response(result)


def _payload(data, default_title=None):
    title = data.get('title', default_title)
    content = data.get('content', '')
    if not isinstance(title, str) or not title.strip() or len(title) > 500 or not isinstance(content, str) or len(content) > 500_000:
        raise ValueError('Invalid title or content')
    return title.strip(), safe_content.clean(content)


def _labels(conn, pid, names):
    if not isinstance(names, list) or len(names) > 30 or any(not isinstance(n, str) for n in names):
        raise ValueError('Invalid labels')
    db.execute('DELETE FROM page_labels WHERE page_id=%s', (pid,), conn)
    for name in set(n.strip().lower() for n in names if n.strip()):
        if len(name) > 100:
            raise ValueError('Invalid label')
        label = db.query('INSERT INTO labels(name) VALUES(%s) ON CONFLICT(name) DO UPDATE SET name=EXCLUDED.name RETURNING id',
                         (name,), one=True, conn=conn)
        db.execute('INSERT INTO page_labels(page_id,label_id) VALUES(%s,%s)', (pid, label['id']), conn)


def create_page(req):
    data = access.csrf(req)
    sid = valid(data.get('space_id'))
    if not sid or not access.space(req.user, sid, write=True):
        raise PermissionError('Space access denied')
    title, content = _payload(data)
    parent = valid(data.get('parent_id')) if data.get('parent_id') else None
    if data.get('parent_id') and not parent:
        raise PermissionError('Invalid parent')
    with db.transaction() as conn:
        if parent:
            pp = _page(parent, conn)
            if not pp or pp['space_id'] != uuid.UUID(sid) or not access.page(req.user, pp, conn=conn):
                raise PermissionError('Invalid parent')
        pid = db.query('''INSERT INTO pages(space_id,parent_id,title,created_by,updated_by,current_version)
            VALUES(%s,%s,%s,%s,%s,1) RETURNING id''',
            (sid, parent, title, req.user['user_id'], req.user['user_id']), one=True, conn=conn)['id']
        db.execute('INSERT INTO page_revisions(page_id,version,title,content,editor_id) VALUES(%s,1,%s,%s::jsonb,%s)',
                   (pid, title, json.dumps({'html': content}), req.user['user_id']), conn)
        _labels(conn, pid, data.get('labels', []))
        video_links.bind_page_videos(
            conn, pid, req.user['user_id'], content
        )
        audit.log(req.user['user_id'], 'page.create', 'page', pid, conn=conn)
    return json_response({'id': pid})


def update_page(req, pid):
    data = access.csrf(req)
    with db.transaction() as conn:
        p = _page(pid, conn, lock=True)
        if not access.page(req.user, p, write=True, conn=conn):
            raise PermissionError('Page access denied')
        title, content = _payload(data, p['title'])
        if data.get('space_id') and valid(data['space_id']) != str(p['space_id']):
            raise PermissionError('Moving pages is not supported')
        parent = valid(data.get('parent_id')) if data.get('parent_id') else None
        if data.get('parent_id') and not parent:
            raise PermissionError('Invalid parent')
        if parent and (parent == str(p['id']) or not (pp := _page(parent, conn)) or pp['space_id'] != p['space_id'] or not access.page(req.user, pp, conn=conn)):
            raise PermissionError('Invalid parent')
        version = p['current_version'] + 1
        db.execute('''UPDATE pages SET title=%s,parent_id=%s,current_version=%s,updated_by=%s,
            updated_at=now(),status='draft' WHERE id=%s''', (title, parent, version, req.user['user_id'], pid), conn)
        db.execute('INSERT INTO page_revisions(page_id,version,title,content,editor_id) VALUES(%s,%s,%s,%s::jsonb,%s)',
                   (pid, version, title, json.dumps({'html': content}), req.user['user_id']), conn)
        if 'labels' in data:
            _labels(conn, pid, data['labels'])
        video_links.bind_page_videos(
            conn, pid, req.user['user_id'], content
        )
        audit.log(req.user['user_id'], 'page.update', 'page', pid, conn=conn)
    return json_response({'ok': True})


def delete_page(req, pid):
    access.csrf(req)
    with db.transaction() as conn:
        p = _page(pid, conn, lock=True)
        if not access.page(req.user, p, write=True, conn=conn):
            raise PermissionError('Page access denied')
        db.execute('UPDATE pages SET deleted_at=now(),updated_by=%s WHERE id=%s', (req.user['user_id'], pid), conn)
        audit.log(req.user['user_id'], 'page.delete', 'page', pid, conn=conn)
    return json_response({'ok': True})
