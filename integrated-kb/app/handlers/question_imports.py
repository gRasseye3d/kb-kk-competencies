"""KK-only staged question imports. Locks always acquire import before item."""
import hashlib
import json

from .. import access, db, question_import_validation as validation
from ..utils import ConflictError, PreconditionRequired, json_response
from .competencies import ident
from .testing import own_department


def pagination(req):
    try:
        limit = int(req.q('limit') or 50)
        offset = int(req.q('offset') or 0)
    except (ValueError, TypeError):
        raise ValueError('Invalid pagination')
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError('Invalid pagination')
    return limit, offset


def meta(conn, did, iid=None, limit=50, offset=0):
    sql = '''SELECT i.id,i.filename,i.status,i.created_at,
        count(x.id)::integer AS total,
        count(x.id) FILTER (WHERE x.status='pending')::integer AS pending,
        count(x.id) FILTER (WHERE x.status='invalid')::integer AS invalid,
        count(x.id) FILTER (WHERE x.status='approved')::integer AS approved,
        count(x.id) FILTER (WHERE x.status='rejected')::integer AS rejected
        FROM question_imports i LEFT JOIN question_import_items x ON x.import_id=i.id
        WHERE i.department_id=%s'''
    params = [did]
    if iid:
        sql += ' AND i.id=%s'
        params.append(ident(iid))
    sql += ''' GROUP BY i.id ORDER BY i.created_at DESC,i.id DESC LIMIT %s OFFSET %s'''
    return db.query(sql, (*params, limit, offset), conn=conn)


def item_view(row):
    return {key: row.get(key) for key in ('id', 'ordinal', 'external_id', 'status',
        'payload', 'edited_payload', 'validation_errors', 'rejection_reason', 'reviewed_by',
        'reviewed_at', 'question_id')}


def token(row):
    fields = {key: row.get(key) for key in ('id', 'status', 'edited_payload', 'validation_errors',
        'reviewed_by', 'reviewed_at', 'rejection_reason', 'question_id')}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(',', ':'),
                                     default=str, ensure_ascii=True).encode('utf-8')).hexdigest()


def conditional(data, row):
    if 'expected_token' not in data:
        raise PreconditionRequired('expected_token required')
    if not isinstance(data['expected_token'], str):
        raise ValueError('Invalid expected_token')
    if data['expected_token'] != token(row):
        raise ConflictError('Import item changed; reload')


def owned_import(conn, did, iid, lock=False):
    row = db.query('SELECT id,payload FROM question_imports WHERE id=%s AND department_id=%s' +
                   (' FOR UPDATE' if lock else ''), (ident(iid), did), one=True, conn=conn)
    if not row:
        raise PermissionError('Import unavailable')
    return row


def locked_item(conn, iid, itemid):
    row = db.query('''SELECT x.*,q.id AS question_id FROM question_import_items x
        LEFT JOIN questions q ON q.import_item_id=x.id
        WHERE x.import_id=%s AND x.id=%s FOR UPDATE OF x''',
        (iid, ident(itemid)), one=True, conn=conn)
    if not row:
        raise PermissionError('Item unavailable')
    return row


def list_imports(req):
    limit, offset = pagination(req)
    with db.transaction() as conn:
        did = own_department(conn, req, 'responsible')
        rows = meta(conn, did, limit=limit, offset=offset)
    return json_response(rows)


def detail(req, iid):
    limit, offset = pagination(req)
    # Editors request 50 rows. Do not let a caller multiply the response size
    # by requesting 100 individually edited JSON documents at once.
    if limit > 50:
        raise ValueError('Invalid pagination')
    with db.transaction() as conn:
        did = own_department(conn, req, 'responsible')
        source_import = owned_import(conn, did, iid)
        result = meta(conn, did, iid)[0]
        result['payload'] = source_import['payload']
        rows = db.query('''SELECT x.*,q.id AS question_id FROM question_import_items x
            LEFT JOIN questions q ON q.import_item_id=x.id
            WHERE x.import_id=%s ORDER BY x.ordinal LIMIT %s OFFSET %s''',
            (iid, limit, offset), conn=conn)
        result['items'] = [dict(item_view(row), expected_token=token(row)) for row in rows]
    return json_response(result)


def checked_body(req, limit):
    # Request.body reads unbounded input if Content-Length is supplied. Guard
    # the import-upload and item-edit paths before csrf(req) calls req.json().
    length = req.environ.get('CONTENT_LENGTH')
    try:
        size = int(length)
    except (ValueError, TypeError):
        raise ValueError('Content-Length required')
    if size > limit:
        size_name = '1 MiB' if limit == 1024 * 1024 else '512 KiB'
        return None, json_response({'error': 'Request exceeds ' + size_name}, '413 Payload Too Large')
    if size < 1:
        raise ValueError('JSON body required')
    try:
        data = access.csrf(req)
    except RecursionError as exc:
        raise ValueError('JSON nesting exceeds 20 levels') from exc
    validation.safe_json(data, max_depth=22)
    return data, None


def upload(req):
    data, error = checked_body(req, 1024 * 1024)
    if error is not None:
        return error
    name = data.get('filename')
    if (not isinstance(name, str) or not name.strip() or len(name) > 254
            or name in ('.', '..') or '/' in name or '\\' in name or ':' in name
            or any(ord(char) < 32 for char in name)):
        raise ValueError('Invalid filename')
    _, items = validation.parse(data.get('json_text'))
    repeated = validation.duplicates(items)
    with db.transaction() as conn:
        did = own_department(conn, req, 'responsible')
        iid = db.query('''INSERT INTO question_imports(uploaded_by,department_id,filename,payload,status)
            VALUES(%s,%s,%s,%s::jsonb,'validated') RETURNING id''',
            (req.user['user_id'], did, name, data['json_text']), one=True, conn=conn)['id']
        for ordinal, item in enumerate(items):
            external = item.get('external_id') if isinstance(item, dict) else None
            if not isinstance(external, str) or len(external) > 128:
                external = None
            errors = validation.validate(conn, did, req.user, item, external in repeated)
            db.execute('''INSERT INTO question_import_items(import_id,ordinal,external_id,payload,validation_errors,status)
                SELECT i.id,%s,%s,(CASE WHEN jsonb_typeof(i.payload)='array' THEN i.payload
                    ELSE i.payload->'questions' END)->%s,%s::jsonb,%s
                FROM question_imports i WHERE i.id=%s''',
                (ordinal, external, ordinal, json.dumps(errors),
                 'invalid' if errors else 'pending', iid), conn=conn)
    return json_response({'id': iid, 'status': 'validated', 'total': len(items)})


def source_page(conn, did, user, page_id, revision_id):
    """Lock the page against concurrent edits while verifying the selected revision."""
    page = db.query('''SELECT p.*,u.department_id AS author_department FROM pages p
        LEFT JOIN users u ON u.id=p.created_by WHERE p.id=%s FOR SHARE OF p''',
        (page_id,), one=True, conn=conn)
    if (not page or page['status'] != 'published' or page['deleted_at'] is not None
            or str(page['author_department']) != str(did)
            or not access.page(user, page, conn=conn)):
        raise ConflictError('Selected source page unavailable')
    revision = db.query('''SELECT id FROM page_revisions WHERE page_id=%s AND version=%s''',
                        (page_id, page['current_version']), one=True, conn=conn)
    if not revision or str(revision['id']) != str(revision_id):
        raise ConflictError('Selected source revision changed; select the article again')


def generated_source(import_row):
    payload = import_row['payload']
    if isinstance(payload, dict) and payload.get('kind') == 'generated_response_v1':
        return payload['source']
    return None


def guarded_source(conn, did, user, import_row, item=None):
    source = generated_source(import_row)
    if source:
        if item is not None and (str(item.get('source_page_id')) != source['page_id']
                                 or str(item.get('source_revision_id')) != source['revision_id']):
            raise ConflictError('Selected source cannot be changed')
        source_page(conn, did, user, source['page_id'], source['revision_id'])
    return source


def source_pages(req):
    q = req.q('q') or ''
    if len(q) > 100 or '\x00' in q:
        raise ValueError('Invalid search query (max 100 characters)')
    try:
        limit = int(req.q('limit') or 20)
        offset = int(req.q('offset') or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError('Invalid pagination') from exc
    if not 1 <= limit <= 50 or offset < 0:
        raise ValueError('Invalid pagination')
    pattern = '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    # Keep the full space/page ACL within the SQL relation, before COUNT or LIMIT.
    visible = '''FROM pages p JOIN spaces s ON s.id=p.space_id
        JOIN page_revisions r ON r.page_id=p.id AND r.version=p.current_version
        JOIN users author ON author.id=p.created_by
        WHERE author.department_id=%(did)s AND p.status='published' AND p.deleted_at IS NULL
          AND (s.owner_id=%(uid)s OR %(admin)s OR s.visibility='internal'
               OR EXISTS(SELECT 1 FROM space_members sm WHERE sm.space_id=s.id AND sm.user_id=%(uid)s))
          AND (%(admin)s OR p.created_by=%(uid)s
               OR NOT EXISTS(SELECT 1 FROM page_permissions pp WHERE pp.page_id=p.id)
               OR EXISTS(SELECT 1 FROM page_permissions pp WHERE pp.page_id=p.id
                   AND pp.permission IN ('view','edit','admin')
                   AND (pp.user_id=%(uid)s OR EXISTS(SELECT 1 FROM group_members gm
                       WHERE gm.group_id=pp.group_id AND gm.user_id=%(uid)s))))
          AND p.title ILIKE %(pattern)s ESCAPE '\\' '''
    with db.transaction() as conn:
        did = own_department(conn, req, 'responsible')
        params = {'did': did, 'uid': req.user['user_id'], 'admin': 'admin' in req.user['roles'],
                  'pattern': pattern, 'limit': limit, 'offset': offset}
        total = db.query('SELECT count(*)::integer AS total ' + visible,
                         params, one=True, conn=conn)['total']
        rows = db.query('''SELECT p.id,p.title,r.id AS current_revision_id,s.name AS space_name ''' +
                        visible + ''' ORDER BY p.title,p.id LIMIT %(limit)s OFFSET %(offset)s''',
                        params, conn=conn)
    return json_response({'items': rows, 'total': total})


def generated_item(raw, ordinal, source):
    errors = []
    obj = raw if isinstance(raw, dict) else {}
    question = obj.get('question')
    if not isinstance(question, str) or not question.strip() or len(question.strip()) > 5000:
        errors.append('question must be nonempty text (max 5000)')
    options = obj.get('options')
    if not isinstance(options, list) or not 2 <= len(options) <= 8:
        errors.append('options must contain 2 to 8 entries')
        options = []
    if any(not isinstance(option, str) or not option.strip() or len(option.strip()) > 1000
           for option in options):
        errors.append('options must be nonempty text (max 1000 each)')
    correct = obj.get('correct_answer')
    if type(correct) is not int or not 0 <= correct < len(options):
        errors.append('correct_answer must be a zero-based option index')
        correct = None
    if not isinstance(raw, dict):
        errors.append('Question must be an object')
    result = {'external_id': f'gen-{ordinal + 1}', 'competency_code': '',
              'question': question if isinstance(question, str) else '', 'type': 'single',
              'options': [{'id': str(index + 1), 'text': option if isinstance(option, str) else '',
                           'is_correct': index == correct} for index, option in enumerate(options)],
              'source_page_id': source['page_id'], 'source_revision_id': source['revision_id']}
    return result, errors


def from_generated(req):
    data, error = checked_body(req, 512 * 1024)
    if error is not None:
        return error
    page_id, revision_id = ident(data.get('source_page_id')), ident(data.get('source_revision_id'))
    settings = data.get('settings')
    if not isinstance(settings, dict):
        raise ValueError('settings object required')
    count, answers = settings.get('questions_count', 5), settings.get('answers_count', 4)
    if type(count) is not int or not 1 <= count <= 30 or type(answers) is not int or not 2 <= answers <= 8:
        raise ValueError('Invalid questions_count or answers_count')
    topics = settings.get('topics', [])
    if (not isinstance(topics, list) or len(topics) > 10 or any(
            not isinstance(topic, str) or not topic.strip() or len(topic) > 200 for topic in topics)):
        raise ValueError('Invalid topics')
    response = data.get('response')
    if not isinstance(response, dict) or not isinstance(response.get('questions'), list):
        raise ValueError('response.questions array required')
    items = response['questions']
    if not 1 <= len(items) <= 30:
        raise ValueError('Expected 1 to 30 generated questions')
    source = {'page_id': page_id, 'revision_id': revision_id, 'provenance': 'selection_only'}
    envelope = {'kind': 'generated_response_v1', 'source': source,
                'settings': settings, 'response': response}
    with db.transaction() as conn:
        did = own_department(conn, req, 'responsible')
        source_page(conn, did, req.user, page_id, revision_id)
        iid = db.query('''INSERT INTO question_imports(uploaded_by,department_id,filename,payload,status)
            VALUES(%s,%s,%s,%s::jsonb,'validated') RETURNING id''',
            (req.user['user_id'], did, 'generated-response.json', json.dumps(envelope, ensure_ascii=True)),
            one=True, conn=conn)['id']
        for ordinal, raw in enumerate(items):
            edited, errors = generated_item(raw, ordinal, source)
            errors.extend(validation.validate(conn, did, req.user, edited))
            db.execute('''INSERT INTO question_import_items(import_id,ordinal,external_id,payload,
                edited_payload,validation_errors,status) VALUES(%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s)''',
                (iid, ordinal, edited['external_id'], json.dumps(raw, ensure_ascii=True),
                 json.dumps(edited, ensure_ascii=True), json.dumps(errors),
                 'invalid' if errors else 'pending'), conn=conn)
    result = {'id': iid, 'status': 'validated', 'total': len(items)}
    if len(items) != count:
        result['warning'] = f'Received {len(items)} questions; requested {count}'
    return json_response(result)


def effective_duplicates(conn, iid, itemid, item):
    if not isinstance(item, dict):
        return False
    external = item.get('external_id')
    if not isinstance(external, str) or not external.strip():
        return False
    # external_id is updated with each edit, so the indexed scalar is always
    # the effective ID. Never fetch 199 potentially 512 KiB JSONB payloads.
    return bool(db.query('''SELECT 1 FROM question_import_items
        WHERE import_id=%s AND id<>%s AND external_id=%s LIMIT 1''',
        (iid, itemid, external), one=True, conn=conn))


def edit(req, iid, itemid):
    data, error = checked_body(req, 512 * 1024)
    if error is not None:
        return error
    payload = data.get('edited_payload')
    if not isinstance(payload, dict):
        raise ValueError('edited_payload object required')
    validation.safe_json(payload)
    # The request cap covers the upload, not the size of each persisted edit.
    # Bound a single edited document so GET detail cannot aggregate dozens of
    # nearly-512-KiB items. Use UTF-8 wire size, without altering the payload.
    if len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > 32 * 1024:
        return json_response({'error': 'edited_payload exceeds 32 KiB'}, '413 Payload Too Large')
    with db.transaction() as conn:
        did = own_department(conn, req, 'responsible')
        import_row = owned_import(conn, did, iid, lock=True)
        row = locked_item(conn, iid, itemid)
        conditional(data, row)
        if row['status'] not in ('pending', 'invalid'):
            raise ConflictError('Item already reviewed')
        guarded_source(conn, did, req.user, import_row, payload)
        errors = validation.validate(conn, did, req.user, payload,
                                     effective_duplicates(conn, iid, itemid, payload))
        external = payload.get('external_id')
        if not isinstance(external, str) or len(external) > 128:
            external = None
        updated = db.query('''UPDATE question_import_items SET edited_payload=%s::jsonb,
            external_id=%s,validation_errors=%s::jsonb,status=%s WHERE id=%s RETURNING *''',
            (json.dumps(payload, ensure_ascii=True), external, json.dumps(errors),
             'invalid' if errors else 'pending', itemid), one=True, conn=conn)
        updated['question_id'] = None
    return json_response(dict(item_view(updated), expected_token=token(updated)))


def review(req, iid, itemid):
    data, error = checked_body(req, 512 * 1024)
    if error is not None:
        return error
    if data.get('decision') not in ('approve', 'reject'):
        raise ValueError('Invalid decision')
    reason = data.get('reason')
    if data['decision'] == 'reject' and (not isinstance(reason, str) or not reason.strip() or len(reason) > 5000):
        raise ValueError('Rejection reason required (max 5000)')
    with db.transaction() as conn:
        did = own_department(conn, req, 'responsible')
        import_row = owned_import(conn, did, iid, lock=True)
        row = locked_item(conn, iid, itemid)
        conditional(data, row)
        if row['status'] in ('approved', 'rejected'):
            raise ConflictError('Item already reviewed')
        qid = None
        if data['decision'] == 'approve':
            if row['status'] == 'invalid':
                raise ConflictError('Question invalid; edit before approving')
            item = row['edited_payload'] if row['edited_payload'] is not None else row['payload']
            source = guarded_source(conn, did, req.user, import_row, item)
            errors = validation.validate(conn, did, req.user, item,
                                         effective_duplicates(conn, iid, itemid, item))
            if errors:
                raise ConflictError('Question invalid; edit before approving')
            if (source or (item.get('source_page_id') and not item.get('source_revision_id'))) and data.get('source_confirmed') is not True:
                raise ValueError('source_confirmed true required for selected or auto-assigned source')
            comp = db.query('''SELECT c.id FROM competencies c
                JOIN profile_competencies pc ON pc.competency_id=c.id
                JOIN competency_profiles p ON p.id=pc.profile_id
                WHERE c.code=%s AND c.is_active AND p.department_id=%s AND p.is_active AND p.status='approved' ''',
                (item['competency_code'], did), one=True, conn=conn)
            page_id = item.get('source_page_id')
            revision_id = item.get('source_revision_id')
            if page_id and not revision_id:
                revision_id = db.query('''SELECT r.id FROM pages p JOIN page_revisions r
                    ON r.page_id=p.id AND r.version=p.current_version WHERE p.id=%s''',
                    (page_id,), one=True, conn=conn)['id']
            qid = db.query('''INSERT INTO questions(competency_id,body,question_type,created_by,
                bank_department_id,import_item_id,moderation_status,reviewed_by,reviewed_at,
                source_page_id,source_revision_id,explanation)
                VALUES(%s,%s,%s,%s,%s,%s,'approved',%s,now(),%s,%s,%s) RETURNING id''',
                (comp['id'], (item.get('question') if item.get('question') is not None else item['body']).strip(),
                 item['type'], req.user['user_id'], did, itemid, req.user['user_id'], page_id,
                 revision_id, item.get('explanation')), one=True, conn=conn)['id']
            correct_ids = []
            for position, option in enumerate(item['options'], 1):
                correct = option.get('is_correct', option.get('correct'))
                oid = db.query('''INSERT INTO answer_options(question_id,position,body,is_correct)
                    VALUES(%s,%s,%s,%s) RETURNING id''',
                    (qid, position, option.get('text', option.get('body')).strip(), correct), one=True, conn=conn)['id']
                if correct:
                    correct_ids.append(str(oid))
            db.execute('''INSERT INTO question_revisions(question_id,version,body,question_type,
                answer_key_snapshot,created_by) VALUES(%s,1,%s,%s,%s::jsonb,%s)''',
                (qid, (item.get('question') if item.get('question') is not None else item['body']).strip(),
                 item['type'], json.dumps(correct_ids), req.user['user_id']), conn=conn)
        updated = db.query('''UPDATE question_import_items SET status=%s,reviewed_by=%s,
            reviewed_at=now(),rejection_reason=%s WHERE id=%s RETURNING *''',
            ('approved' if qid else 'rejected', req.user['user_id'],
             reason.strip() if not qid else None, itemid), one=True, conn=conn)
        if not db.query("SELECT 1 FROM question_import_items WHERE import_id=%s AND status IN ('pending','invalid') LIMIT 1",
                        (iid,), one=True, conn=conn):
            db.execute("UPDATE question_imports SET status='processed' WHERE id=%s", (iid,), conn=conn)
        updated['question_id'] = qid
    return json_response(dict(item_view(updated), expected_token=token(updated)))


def register(r):
    for method, path, fn in (
        ('GET', '/api/testing/imports/source-pages', source_pages),
        ('POST', '/api/testing/imports/from-generated', from_generated),
        ('POST', '/api/testing/imports', upload),
        ('GET', '/api/testing/imports', list_imports),
        ('GET', '/api/testing/imports/<iid>', detail),
        ('PUT', '/api/testing/imports/<iid>/items/<itemid>', edit),
        ('POST', '/api/testing/imports/<iid>/items/<itemid>/review', review),
    ):
        r.route(method, path)(fn)
