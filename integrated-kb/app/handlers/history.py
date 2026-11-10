import json
from .. import db, access, audit, content as safe_content
from ..utils import json_response
from .pages import _page, valid


def register(r):
    r.route('GET', '/api/pages/<pid>/history')(list_hist)
    r.route('POST', '/api/pages/<pid>/restore/<vid>')(restore)


def list_hist(req, pid):
    require_history_role(req.user)
    p = _page(pid)
    if not access.page(req.user, p):
        return json_response({'error': 'not found'}, '404 Not Found')
    return json_response(db.query('SELECT id,version,editor_id,created_at FROM page_revisions WHERE page_id=%s ORDER BY version DESC', (pid,)))


def restore(req, pid, vid):
    access.csrf(req)
    require_history_role(req.user)
    if not valid(vid):
        return json_response({'error': 'not found'}, '404 Not Found')
    with db.transaction() as conn:
        p = _page(pid, conn, lock=True)
        if not access.page(req.user, p, write=True, conn=conn):
            raise PermissionError('Page access denied')
        v = db.query('SELECT * FROM page_revisions WHERE page_id=%s AND id=%s', (pid, vid), one=True, conn=conn)
        if not v:
            return json_response({'error': 'not found'}, '404 Not Found')
        number = p['current_version'] + 1
        db.execute("UPDATE pages SET title=%s,current_version=%s,status='draft',updated_by=%s,updated_at=now() WHERE id=%s",
                   (v['title'], number, req.user['user_id'], pid), conn)
        db.execute('INSERT INTO page_revisions(page_id,version,title,content,editor_id) VALUES(%s,%s,%s,%s::jsonb,%s)',
                   (pid, number, v['title'], json.dumps({'html': safe_content.clean(v['content'].get('html', ''))}), req.user['user_id']), conn)
        audit.log(req.user['user_id'], 'page.restore', 'page', pid, {'from': str(vid)}, conn)
    return json_response({'ok': True})


def require_history_role(user):
    if not user or not set(user['roles']) & {'admin', 'moderator', 'manager', 'eol', 'EOL', 'god'}:
        raise PermissionError('Page history role required')
