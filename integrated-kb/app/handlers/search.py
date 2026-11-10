from .. import db, content as safe_content
from ..utils import json_response


def register(r):
    r.route('GET', '/api/search')(search)


def search(req):
    q = (req.q('q') or '').strip()[:100]
    if not req.user or not q:
        return json_response([])
    escaped = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    uid = req.user['user_id']
    rows = db.query('''SELECT p.id,p.title,p.status,
             LEFT(COALESCE(pr.content->>'html',''),180) AS snippet
        FROM pages p JOIN spaces s ON s.id=p.space_id
        JOIN page_revisions pr ON pr.page_id=p.id AND pr.version=p.current_version
        WHERE p.deleted_at IS NULL
          AND (s.owner_id=%(uid)s OR %(admin)s OR s.visibility='internal'
               OR EXISTS(SELECT 1 FROM space_members sm
                         WHERE sm.space_id=s.id AND sm.user_id=%(uid)s))
          AND (%(admin)s OR p.status='published' OR p.created_by=%(uid)s
               OR (p.status='pending_review' AND %(moderator)s
                   AND (s.owner_id=%(uid)s OR EXISTS (
                     SELECT 1 FROM space_members sm WHERE sm.space_id=s.id
                     AND sm.user_id=%(uid)s AND sm.role IN ('admin','editor')))))
          AND (%(admin)s OR p.created_by=%(uid)s
               OR NOT EXISTS(SELECT 1 FROM page_permissions pp WHERE pp.page_id=p.id)
               OR EXISTS(SELECT 1 FROM page_permissions pp
                   WHERE pp.page_id=p.id AND pp.permission IN ('view','edit','admin')
                   AND (pp.user_id=%(uid)s OR EXISTS(
                     SELECT 1 FROM group_members gm WHERE gm.group_id=pp.group_id
                     AND gm.user_id=%(uid)s))))
          AND (p.title ILIKE %(pattern)s OR pr.content->>'html' ILIKE %(pattern)s)
        ORDER BY p.updated_at DESC, p.id LIMIT 50''',
        {'uid': uid, 'admin': 'admin' in req.user['roles'],
         'moderator': 'moderator' in req.user['roles'], 'pattern': '%' + escaped + '%'})
    return json_response([{'id': p['id'], 'title': p['title'], 'status': p['status'],
                           'snippet': safe_content.clean(p['snippet'])} for p in rows])
