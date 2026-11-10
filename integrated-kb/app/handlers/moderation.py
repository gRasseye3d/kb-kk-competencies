from .. import db, access, audit
from ..utils import json_response
from .pages import _page, valid


def register(r):
    r.route('GET', '/api/moderation/queue')(queue)
    r.route('POST', '/api/moderation/submit/<pid>')(submit)
    r.route('POST', '/api/moderation/take/<rid>')(take)
    r.route('POST', '/api/moderation/decide/<rid>')(decide)


def _moderator(req, page, conn=None):
    return req.user and ('admin' in req.user['roles'] or
        (access.space(req.user, page['space_id'], write=True, conn=conn) and
         'moderator' in req.user['roles']))


def _self_review(req, row, page):
    return req.user['user_id'] in (row['submitted_by'], page['created_by'])


def _stale():
    return json_response({'error': 'Approval is no longer current',
                          'code': 'approval_stale'}, '409 Conflict')


def submit(req, pid):
    access.csrf(req)
    with db.transaction() as conn:
        p = _page(pid, conn, lock=True)
        # Only this endpoint may acknowledge an author's already pending submit.
        # Recheck the normal author/space/page permissions without allowing edits.
        permission_page = {**p, 'status': 'draft'} if p and p['status']=='pending_review' else p
        if not access.page(req.user, permission_page, write=True, conn=conn):
            raise PermissionError('Page access denied')
        rev = db.query('SELECT id FROM page_revisions WHERE page_id=%s AND version=%s',
                       (pid,p['current_version']),one=True,conn=conn)
        if not rev:
            return _stale()
        if p['status']=='pending_review':
            row = db.query('''SELECT id FROM page_approvals
                WHERE page_id=%s AND revision_id=%s AND status='pending'
                ORDER BY submitted_at,id LIMIT 1''', (pid,rev['id']),one=True,conn=conn)
            return json_response({'request_id':row['id']}) if row else _stale()
        row = db.query('''INSERT INTO page_approvals(page_id,revision_id,submitted_by)
            VALUES(%s,%s,%s) RETURNING id''', (pid,rev['id'],req.user['user_id']),one=True,conn=conn)
        db.execute("UPDATE pages SET status='pending_review' WHERE id=%s",(pid,),conn)
        audit.log(req.user['user_id'],'page.submit','page',pid,conn=conn)
    return json_response({'request_id':row['id']})


def queue(req):
    if not req.user or 'moderator' not in req.user['roles'] and 'admin' not in req.user['roles']:
        raise PermissionError('Moderator required')
    rows = db.query('''SELECT pa.id,pa.status,pa.page_id,pa.submitted_by,pr.version,p.title,pa.submitted_at AS created_at,
        u.display_name AS author FROM page_approvals pa JOIN pages p ON p.id=pa.page_id
        JOIN page_revisions pr ON pr.id=pa.revision_id AND pr.page_id=p.id AND pr.version=p.current_version
        LEFT JOIN users u ON u.id=pa.submitted_by WHERE pa.status='pending'
        AND p.status='pending_review' AND p.deleted_at IS NULL''')
    result = []
    for row in rows:
        p = _page(row['page_id'])
        if (not p or p['status']!='pending_review' or p['current_version']!=row['version']
                or not _moderator(req, p) or not access.page(req.user, p)):
            continue
        own = _self_review(req, row, p)
        row['can_approve'] = not own or 'admin' in req.user['roles']
        row['can_reject'] = not own
        row['reason'] = 'self_review' if own else None
        result.append(row)
    return json_response(result)


def take(req, rid):
    access.csrf(req)
    return json_response({'error':'Assignment unavailable'},'501 Not Implemented')


def decide(req, rid):
    data = access.csrf(req)
    if not valid(rid) or data.get('action') not in ('approve','reject'):
        return json_response({'error':'Invalid decision'},'400 Bad Request')
    comment = (data.get('comment') or '').strip()
    if data['action']=='reject' and not comment:
        return json_response({'error':'Comment required'},'400 Bad Request')
    with db.transaction() as conn:
        row = db.query('SELECT * FROM page_approvals WHERE id=%s FOR UPDATE',(rid,),one=True,conn=conn)
        p = _page(row['page_id'],conn,lock=True) if row else None
        # Check review visibility even if the page has since returned to draft;
        # authorized reviewers receive a conflict, not a misleading access error.
        if (not row or not p or not _moderator(req,p,conn)
                or not access.page(req.user,{**p, 'status':'pending_review'},conn=conn)):
            raise PermissionError('Approval unavailable')
        if row['status']!='pending' or p['status']!='pending_review':
            return _stale()
        rev = db.query('SELECT id FROM page_revisions WHERE page_id=%s AND version=%s',
                       (p['id'],p['current_version']),one=True,conn=conn)
        if not rev or rev['id'] != row['revision_id']:
            return _stale()
        if _self_review(req,row,p) and not (data['action']=='approve' and 'admin' in req.user['roles']):
            return json_response({'error':'Self review is not allowed',
                                  'code':'self_review'}, '403 Forbidden')
        db.execute('''UPDATE page_approvals SET status=%s,reviewed_by=%s,reviewed_at=now(),comment=%s WHERE id=%s''',
                   ('approved' if data['action']=='approve' else 'rejected',req.user['user_id'],comment or None,rid),conn)
        db.execute('UPDATE pages SET status=%s WHERE id=%s',
                   ('published' if data['action']=='approve' else 'rejected',p['id']),conn)
        audit.log(req.user['user_id'],'page.'+data['action'],'page',p['id'],conn=conn)
    return json_response({'ok':True})
