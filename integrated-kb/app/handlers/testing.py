"""Department-only primary tests. Published versions and attempt snapshots are write-once."""
import json
import hashlib
from datetime import datetime, timezone

from .. import access, db, testing_snapshots as snapshots
from ..utils import ConflictError, PreconditionRequired, json_response
from .competencies import actor, department, ident, text


def own_department(conn, req, kind):
    uid = actor(req, 'kk_responsible' if kind == 'responsible' else 'manager')
    user = db.query('SELECT department_id FROM users WHERE id=%s AND is_active', (uid,), one=True, conn=conn)
    if not user or not user['department_id']:
        raise PermissionError('Department required')
    department(conn, user['department_id'], req.user, kind)
    return str(user['department_id'])


def lock_user(conn, uid):
    # All starts and completions for this employee serialize, including across assignments.
    db.query('SELECT id FROM users WHERE id=%s FOR UPDATE', (uid,), one=True, conn=conn)


def create_question(req):
    data = access.csrf(req)
    cid = ident(data.get('competency_id'))
    kind = data.get('type')
    options = data.get('options')
    if kind not in ('single', 'multiple') or not isinstance(options, list) or not 2 <= len(options) <= 20:
        raise ValueError('Invalid options/type')
    parsed = [(text(o.get('body'), 1000), o.get('correct')) for o in options if isinstance(o, dict)]
    if len(parsed) != len(options) or any(type(v) is not bool for _, v in parsed) or not any(v for _, v in parsed) or (kind == 'single' and sum(v for _, v in parsed) != 1):
        raise ValueError('Invalid answer key')
    with db.transaction() as conn:
        did = own_department(conn, req, 'responsible')
        if not db.query('''SELECT 1 FROM competency_profiles p JOIN profile_competencies pc ON pc.profile_id=p.id
            JOIN competencies c ON c.id=pc.competency_id WHERE p.department_id=%s AND p.is_active AND c.is_active AND c.id=%s''',
            (did,cid), one=True, conn=conn):
            raise PermissionError('Competency not in active department profile')
        qid = db.query('''INSERT INTO questions(competency_id,body,question_type,created_by,bank_department_id)
            VALUES(%s,%s,%s,%s,%s) RETURNING id''', (cid,text(data.get('body'),5000),kind,req.user['user_id'],did), one=True,conn=conn)['id']
        for i,(body,correct) in enumerate(parsed,1):
            db.query('INSERT INTO answer_options(question_id,position,body,is_correct) VALUES(%s,%s,%s,%s) RETURNING id',
                     (qid,i,body,correct),one=True,conn=conn)
    return json_response({'id':qid})


def review_question(req, qid):
    data = access.csrf(req)
    if data.get('decision') not in ('approve','reject'):
        raise ValueError('Invalid decision')
    with db.transaction() as conn:
        did = own_department(conn,req,'responsible')
        q = db.query('''SELECT q.id,q.moderation_status FROM questions q JOIN competencies c ON c.id=q.competency_id
            JOIN profile_competencies pc ON pc.competency_id=c.id JOIN competency_profiles p ON p.id=pc.profile_id
            WHERE q.id=%s AND p.department_id=%s AND q.bank_department_id=%s AND p.is_active FOR UPDATE OF q''', (ident(qid),did,did),one=True,conn=conn)
        if not q: raise PermissionError('Question unavailable')
        if q['moderation_status'] != 'pending': raise ConflictError('Already moderated')
        db.execute('''UPDATE questions SET moderation_status=%s,reviewed_by=%s,reviewed_at=now() WHERE id=%s''',
                   ('approved' if data['decision']=='approve' else 'rejected',req.user['user_id'],qid),conn=conn)
    return json_response({'status':data['decision']})


def bank(req):
    try:
        limit = int(req.q('limit') or 100)
        offset = int(req.q('offset') or 0)
    except (ValueError, TypeError):
        raise ValueError('Invalid pagination')
    if not 1 <= limit <= 100 or offset < 0: raise ValueError('Invalid pagination')
    cid = ident(req.q('competency_id')) if req.q('competency_id') else None
    status = req.q('status')
    if status and status not in ('pending', 'approved', 'rejected'): raise ValueError('Invalid status')
    with db.transaction() as conn:
        did=own_department(conn,req,'responsible')
        rows=db.query('''SELECT q.id,q.competency_id,c.name AS competency_name,q.body,q.question_type,q.moderation_status,q.is_active
            FROM questions q JOIN profile_competencies pc ON pc.competency_id=q.competency_id
            JOIN competencies c ON c.id=q.competency_id
            JOIN competency_profiles p ON p.id=pc.profile_id
            WHERE p.department_id=%s AND p.is_active AND q.bank_department_id=%s
              AND c.is_active AND q.is_active
              AND (%s::uuid IS NULL OR q.competency_id=%s)
              AND (%s::text IS NULL OR q.moderation_status=%s)
            ORDER BY q.created_at DESC,q.id LIMIT %s OFFSET %s''',
            (did,did,cid,cid,status,status,limit,offset),conn=conn)
    return json_response(rows)


def question_detail(req, qid):
    with db.transaction() as conn:
        did=own_department(conn,req,'responsible')
        q=db.query('''SELECT q.id,q.competency_id,q.body,q.question_type,q.moderation_status,
            q.bank_department_id,q.import_item_id,x.import_id,q.source_page_id,q.source_revision_id
            FROM questions q LEFT JOIN question_import_items x ON x.id=q.import_item_id
            WHERE q.id=%s AND q.bank_department_id=%s''',(ident(qid),did),one=True,conn=conn)
        if not q: raise PermissionError('Question unavailable')
        q['options']=db.query('''SELECT id,position,body,is_correct FROM answer_options
            WHERE question_id=%s ORDER BY position''',(qid,),conn=conn)
    return json_response(q)


def read_department(conn, req):
    actor(req)
    # Union of actual appointments, not just role flags.
    for kind in ('responsible', 'manager'):
        try:
            return own_department(conn, req, kind), kind
        except PermissionError:
            continue
    raise PermissionError('Department test access denied')


def test_row(conn, tid, did):
    row = db.query('SELECT * FROM tests WHERE id=%s AND department_id=%s FOR UPDATE',
                   (ident(tid), did), one=True, conn=conn)
    if not row: raise PermissionError('Test unavailable')
    return row


def latest_version(conn, tid):
    return db.query('SELECT max(version) AS version FROM test_versions WHERE test_id=%s',
                    (tid,), one=True, conn=conn)['version']


def current_blocks(conn, tid):
    rows = db.query('''SELECT b.id AS block_id,b.title,bc.id AS link_id,bc.competency_id,c.name AS competency_name
        FROM test_block_competencies bc JOIN test_blocks b ON b.id=bc.block_id
        JOIN competencies c ON c.id=bc.competency_id
        WHERE bc.test_id=%s ORDER BY b.position,bc.position''', (tid,), conn=conn)
    questions = db.query('''SELECT block_competency_id,question_id FROM test_questions
        WHERE test_id=%s ORDER BY position''', (tid,), conn=conn)
    selected = {}
    for q in questions:
        selected.setdefault(str(q['block_competency_id']), []).append(str(q['question_id']))
    blocks = {}
    for row in rows:
        bid = str(row['block_id'])
        if bid not in blocks:
            blocks[bid] = {'block_id': bid, 'title': row['title'],
                           'position': len(blocks) + 1, 'competencies': []}
        blocks[bid]['competencies'].append({'competency_id': str(row['competency_id']),
            'competency_name': row['competency_name'], 'question_ids': selected.get(str(row['link_id']), [])})
    return list(blocks.values())


def draft_blocks(blocks):
    return [{'title': b['title'], 'competencies': [
        {'competency_id': c['competency_id'], 'question_ids': c['question_ids']}
        for c in b['competencies']]} for b in blocks]


def expected_token(test, blocks, latest):
    state = {'id': str(test['id']), 'title': test['title'], 'description': test.get('description'),
             'status': test['status'], 'latest': latest, 'blocks': blocks}
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def check_token(data, test, blocks, latest):
    if 'expected_token' not in data: raise PreconditionRequired('expected_token required')
    if not isinstance(data['expected_token'], str): raise ValueError('Invalid expected_token')
    if data['expected_token'] != expected_token(test, blocks, latest):
        raise ConflictError('Test changed; reload before saving')


def draft_response(test, blocks, latest):
    return {'id': test['id'], 'title': test['title'], 'description': test.get('description') or '',
            'status': test['status'], 'expected_token': expected_token(test, blocks, latest),
            'latest_published_version': latest, 'next_version': (latest or 0) + 1,
            'title_locked': latest is not None, 'blocks': draft_blocks(blocks)}


def parse_draft(data):
    title = text(data.get('title'), 250)
    description = data.get('description', '')
    if description is None: description = ''
    if not isinstance(description, str) or len(description) > 10000: raise ValueError('Invalid description')
    blocks = data.get('blocks')
    # Backwards-compatible creation syntax, deliberately with NO bank auto-selection.
    if blocks is None and isinstance(data.get('competency_ids'), list):
        blocks = [{'title': 'Основной блок', 'competencies': [
            {'competency_id': cid, 'question_ids': []} for cid in data['competency_ids']]}]
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= 10: raise ValueError('One to ten blocks required')
    parsed, seen, question_ids = [], set(), set()
    for block in blocks:
        if not isinstance(block, dict): raise ValueError('Invalid block')
        comps = block.get('competencies')
        if not isinstance(comps, list) or len(comps) < 2: raise ValueError('At least two competencies per block required')
        parsed_comps = []
        for comp in comps:
            if not isinstance(comp, dict): raise ValueError('Invalid competency')
            cid = ident(comp.get('competency_id'))
            if cid in seen: raise ValueError('Duplicate competency')
            seen.add(cid)
            qs = comp.get('question_ids')
            if not isinstance(qs, list) or len(qs) > 50: raise ValueError('Zero to fifty selected questions required')
            qs = [ident(q) for q in qs]
            if len(set(qs)) != len(qs) or question_ids.intersection(qs): raise ValueError('Duplicate question')
            question_ids.update(qs)
            parsed_comps.append({'competency_id': cid, 'question_ids': qs})
        parsed.append({'title': text(block.get('title'), 250), 'competencies': parsed_comps})
    if len(seen) > 20: raise ValueError('At most twenty competencies required')
    return title, description, parsed


def validate_profile(conn, did, ids):
    rows = db.query('''SELECT pc.competency_id FROM competency_profiles p JOIN profile_competencies pc ON pc.profile_id=p.id
        JOIN competencies c ON c.id=pc.competency_id WHERE p.department_id=%s AND p.is_active AND c.is_active
        AND pc.competency_id=ANY(%s::uuid[])''', (did, ids), conn=conn)
    if {str(row['competency_id']) for row in rows} != set(ids): raise PermissionError('Competency outside profile')


def selected_questions(conn, did, blocks, publishing=False):
    comps = [c for b in blocks for c in b['competencies']]
    validate_profile(conn, did, [c['competency_id'] for c in comps])
    if publishing and any(len(c['question_ids']) < 2 for c in comps):
        raise ValueError('Two approved questions per competency required')
    ids = sorted(qid for c in comps for qid in c['question_ids'])
    if not ids: return {}
    # Stable order serializes revision-number allocation across tests; no profile row lock.
    rows = db.query('''SELECT id,competency_id,body,question_type FROM questions
        WHERE id=ANY(%s::uuid[]) AND moderation_status='approved' AND is_active
          AND bank_department_id=%s ORDER BY id FOR UPDATE''', (ids, did), conn=conn)
    found = {str(row['id']): row for row in rows}
    if len(found) != len(ids): raise ValueError('Selected question unavailable or not approved')
    for comp in comps:
        if any(str(found[qid]['competency_id']) != comp['competency_id'] for qid in comp['question_ids']):
            raise ValueError('Question belongs to another competency')
    return found


def save_composition(conn, tid, blocks):
    # These are CURRENT authoring links only. Historical blocks, bank rows and
    # immutable versions/attempts are never deleted or updated.
    db.execute('DELETE FROM test_questions WHERE test_id=%s', (tid,), conn=conn)
    db.execute('DELETE FROM test_block_competencies WHERE test_id=%s', (tid,), conn=conn)
    position = db.query('SELECT COALESCE(max(position),0) AS position FROM test_blocks WHERE test_id=%s',
                        (tid,), one=True, conn=conn)['position']
    question_position = 0
    for block in blocks:
        position += 1
        bid = db.query('INSERT INTO test_blocks(test_id,title,position) VALUES(%s,%s,%s) RETURNING id',
                        (tid, block['title'], position), one=True, conn=conn)['id']
        for pos, comp in enumerate(block['competencies'], 1):
            link = db.query('''INSERT INTO test_block_competencies(test_id,block_id,competency_id,position)
                VALUES(%s,%s,%s,%s) RETURNING id''', (tid, bid, comp['competency_id'], pos), one=True, conn=conn)['id']
            for qid in comp['question_ids']:
                question_position += 1
                db.execute('''INSERT INTO test_questions(test_id,question_id,block_competency_id,competency_id,position)
                    VALUES(%s,%s,%s,%s,%s)''', (tid, qid, link, comp['competency_id'], question_position), conn=conn)


def tests(req):
    with db.transaction() as conn:
        did, kind = read_department(conn, req)
        rows = db.query('''SELECT t.id,t.title,t.description,t.status,
            (SELECT max(v.version) FROM test_versions v WHERE v.test_id=t.id) AS version
            FROM tests t WHERE t.department_id=%s AND (%s OR
                (t.status<>'archived' AND EXISTS (SELECT 1 FROM test_versions v WHERE v.test_id=t.id)))
            ORDER BY t.title,t.id''', (did, kind == 'responsible'), conn=conn)
        for row in rows:
            latest = row['version']
            row.update(latest_published_version=latest, has_draft=row['status'] == 'draft',
                       assignable=latest is not None and row['status'] != 'archived', next_version=(latest or 0) + 1)
            if kind == 'responsible':
                blocks = current_blocks(conn, row['id'])
                row['expected_token'] = expected_token(row, blocks, latest)
                row['competencies'] = [{'id': c['competency_id'], 'name': c['competency_name']}
                                       for b in blocks for c in b['competencies']]
            else:
                frozen = version_snapshot(conn, row['id'], latest)
                names = legacy_names(conn, frozen)
                row['competencies'] = [{'id': c['competency_id'],
                    'name': c.get('competency_name', names.get(c['competency_id'], 'Компетенция'))}
                    for c in snapshots.competencies(frozen)]
            row.pop('description', None)
    return json_response(rows)


def create_test(req):
    data = access.csrf(req)
    title, description, blocks = parse_draft(data)
    with db.transaction() as conn:
        did = own_department(conn, req, 'responsible')
        selected_questions(conn, did, blocks)
        tid = db.query('''INSERT INTO tests(title,description,scope,department_id,created_by)
            VALUES(%s,%s,'department',%s,%s) RETURNING id''',
            (title, description, did, req.user['user_id']), one=True, conn=conn)['id']
        save_composition(conn, tid, blocks)
    return json_response({'id': tid})


def get_draft(req, tid):
    with db.transaction() as conn:
        did = own_department(conn, req, 'responsible')
        test = test_row(conn, tid, did)
        if test['status'] != 'draft': raise ConflictError('No draft')
        result = draft_response(test, current_blocks(conn, tid), latest_version(conn, tid))
    return json_response(result)


def begin_draft(req, tid):
    data = access.csrf(req)
    with db.transaction() as conn:
        did = own_department(conn, req, 'responsible')
        test = test_row(conn, tid, did)
        latest = latest_version(conn, tid)
        check_token(data, test, current_blocks(conn, tid), latest)
        if test['status'] != 'published' or latest is None: raise ConflictError('Cannot begin draft')
        frozen = version_snapshot(conn, tid, latest)
        blocks = [{'title': b['title'], 'competencies': [
            {'competency_id': c['competency_id'], 'question_ids': [q['id'] for q in c['questions']]}
            for c in b['competencies']]} for b in snapshots.blocks(frozen)]
        save_composition(conn, tid, blocks)
        description = frozen.get('description', test.get('description'))
        db.execute("UPDATE tests SET status='draft',description=%s WHERE id=%s", (description, tid), conn=conn)
        test.update(status='draft', description=description)
        result = draft_response(test, current_blocks(conn, tid), latest)
    return json_response(result)


def save_draft(req, tid):
    data = access.csrf(req)
    with db.transaction() as conn:
        did = own_department(conn, req, 'responsible')
        test = test_row(conn, tid, did)
        latest = latest_version(conn, tid)
        check_token(data, test, current_blocks(conn, tid), latest)
        if test['status'] != 'draft': raise ConflictError('No editable draft')
        title, description, blocks = parse_draft(data)
        if latest is not None and title != test['title']: raise ConflictError('Published test title is locked')
        selected_questions(conn, did, blocks)
        save_composition(conn, tid, blocks)
        db.execute('UPDATE tests SET title=%s,description=%s WHERE id=%s', (title, description, tid), conn=conn)
        test.update(title=title, description=description)
        result = draft_response(test, current_blocks(conn, tid), latest)
    return json_response(result)


def archive(req, tid):
    data = access.csrf(req)
    with db.transaction() as conn:
        did = own_department(conn, req, 'responsible')
        test = test_row(conn, tid, did)
        check_token(data, test, current_blocks(conn, tid), latest_version(conn, tid))
        if test['status'] == 'archived': raise ConflictError('Already archived')
        db.execute("UPDATE tests SET status='archived' WHERE id=%s", (tid,), conn=conn)
    return json_response({'ok': True})


def publish(req, tid):
    data = access.csrf(req)
    with db.transaction() as conn:
        did = own_department(conn, req, 'responsible')
        test = test_row(conn, tid, did)
        latest = latest_version(conn, tid)
        blocks = current_blocks(conn, tid)
        check_token(data, test, blocks, latest)
        if test['status'] != 'draft': raise ConflictError('No publishable draft')
        # Recheck all limits and bank/profile eligibility at publication, not only save.
        parse_draft(dict(title=test['title'], blocks=draft_blocks(blocks)))
        by_id = selected_questions(conn, did, blocks, publishing=True)
        frozen_by_id = {}
        for qid in sorted(by_id):
            q = by_id[qid]
            opts = db.query('SELECT id,body,is_correct FROM answer_options WHERE question_id=%s ORDER BY position FOR SHARE',
                            (qid,), conn=conn)
            correct = sum(bool(o['is_correct']) for o in opts)
            if not 2 <= len(opts) <= 20 or not correct or q['question_type'] not in ('single', 'multiple') or (q['question_type'] == 'single' and correct != 1):
                raise ValueError('Incomplete question or invalid answer key')
            revision = db.query('''INSERT INTO question_revisions(question_id,version,body,question_type,answer_key_snapshot,created_by)
                VALUES(%s,COALESCE((SELECT max(version)+1 FROM question_revisions WHERE question_id=%s),1),%s,%s,%s::jsonb,%s) RETURNING id''',
                (qid,qid,q['body'],q['question_type'],json.dumps([str(o['id']) for o in opts if o['is_correct']]),req.user['user_id']),one=True,conn=conn)['id']
            frozen_by_id[qid] = {'id': qid, 'revision_id': str(revision), 'body': q['body'], 'type': q['question_type'],
                'options': [{'id': str(o['id']), 'body': o['body'], 'correct': o['is_correct']} for o in opts]}
        version = (latest or 0) + 1
        frozen = {'format_version': 2, 'test_title': test['title'], 'test_version': version,
                  'description': test.get('description') or '', 'scoring_rule': snapshots.PRIMARY_RULE,
                  'blocks': [{'block_id': b['block_id'], 'title': b['title'], 'position': b['position'],
                    'competencies': [{'competency_id': c['competency_id'], 'competency_name': c['competency_name'],
                        'questions': [frozen_by_id[qid] for qid in c['question_ids']]} for c in b['competencies']]}
                    for b in blocks]}
        db.query('INSERT INTO test_versions(test_id,version,frozen_structure,published_by) VALUES(%s,%s,%s::jsonb,%s) RETURNING id',
                 (tid,version,json.dumps(frozen),req.user['user_id']),one=True,conn=conn)
        db.execute("UPDATE tests SET status='published' WHERE id=%s", (tid,), conn=conn)
    return json_response({'id': tid, 'version': version})


def version_snapshot(conn, tid, version):
    row = db.query('SELECT frozen_structure FROM test_versions WHERE test_id=%s AND version=%s',
                   (tid, version), one=True, conn=conn)
    if not row: raise PermissionError('Version unavailable')
    return row['frozen_structure']


def legacy_names(conn, snapshot):
    if snapshot.get('format_version') == 2: return {}
    ids = [c['competency_id'] for c in snapshots.competencies(snapshot)]
    return {str(c['id']): c['name'] for c in db.query('SELECT id,name FROM competencies WHERE id=ANY(%s::uuid[])',
                                                     (ids,), conn=conn)}


def versions(req, tid):
    with db.transaction() as conn:
        did, _ = read_department(conn, req)
        test = test_row(conn, tid, did)
        rows = db.query('''SELECT version,published_at,frozen_structure FROM test_versions
            WHERE test_id=%s ORDER BY version DESC''', (tid,), conn=conn)
        result = []
        for row in rows:
            frozen = row['frozen_structure']
            comps = snapshots.competencies(frozen)
            result.append({'version': row['version'], 'title': frozen.get('test_title', test['title']),
                'published_at': row['published_at'], 'block_count': len(snapshots.blocks(frozen)),
                'competency_count': len(comps), 'question_count': sum(len(c['questions']) for c in comps)})
    return json_response(result)


def version_detail(req, tid, version):
    try:
        version = int(version)
    except (ValueError, TypeError):
        raise ValueError('Invalid version')
    if version < 1: raise ValueError('Invalid version')
    with db.transaction() as conn:
        did, _ = read_department(conn, req)
        test = test_row(conn, tid, did)
        frozen = version_snapshot(conn, tid, version)
        result = {'test_title': frozen.get('test_title', test['title']), 'test_version': version,
                  'blocks': snapshots.public_blocks(frozen, names=legacy_names(conn, frozen))}
    return json_response(result)


def assign(req):
    data=access.csrf(req)
    uid,tid=ident(data.get('user_id')),ident(data.get('test_id'))
    version = data.get('test_version')
    if 'test_version' in data and (type(version) is not int or version < 1):
        raise ValueError('Invalid test_version')
    due=data.get('due_at')
    if not isinstance(due,str) or not due.strip():
        raise ValueError('Deadline required')
    try:
        due=datetime.fromisoformat(due.replace('Z','+00:00'))
        if due.tzinfo is None or due.utcoffset() is None: raise ValueError()
        due=due.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError('Deadline must be future ISO 8601 with timezone')
    with db.transaction() as conn:
        did=own_department(conn,req,'manager')
        if not db.query('SELECT 1 FROM users WHERE id=%s AND department_id=%s AND is_active',(uid,did),one=True,conn=conn):
            raise PermissionError('Employee outside department')
        test = test_row(conn, tid, did)
        if test['status'] == 'archived': raise PermissionError('Test unavailable')
        if version is None: version = latest_version(conn, tid)
        if version is None: raise PermissionError('Test unavailable')
        frozen = version_snapshot(conn, tid, version)
        validate_profile(conn, did, [c['competency_id'] for c in snapshots.competencies(frozen)])
        row=db.query('''INSERT INTO test_assignments(test_id,user_id,assigned_by,test_version,due_at)
            SELECT %s,%s,%s,%s,%s WHERE %s::timestamptz > clock_timestamp()
            RETURNING id,test_version,due_at''',(tid,uid,req.user['user_id'],version,due,due),one=True,conn=conn)
        if not row: raise ValueError('Deadline must be in the future')
    return json_response(row)


def start(req, aid):
    access.csrf(req)
    uid=actor(req,'employee')
    with db.transaction() as conn:
        lock_user(conn,uid)
        a=db.query('''SELECT a.*,v.frozen_structure FROM test_assignments a
            JOIN test_versions v ON v.test_id=a.test_id AND v.version=a.test_version
            WHERE a.id=%s AND a.user_id=%s FOR UPDATE OF a''',(ident(aid),uid),one=True,conn=conn)
        if not a: raise PermissionError('Assignment unavailable')
        if a['status']!='assigned': raise ConflictError('Attempt already started')
        if a['assignment_type']=='manager_assigned' and a['due_at'] is None:
            raise ConflictError('Assignment deadline missing; contact your manager', code='due_missing')
        if a['due_at'] and not db.query('SELECT %s::timestamptz > clock_timestamp() AS valid',(a['due_at'],),one=True,conn=conn)['valid']:
            raise ConflictError('Deadline passed')
        comps=snapshots.competencies(a['frozen_structure'])
        snapshots.scoring(a['frozen_structure'])
        availability=[]
        for comp in comps:
            cid=comp['competency_id']
            cd=db.query('''SELECT available_at FROM assessment_cooldowns WHERE user_id=%s AND competency_id=%s AND available_at>clock_timestamp()''',
                        (uid,cid),one=True,conn=conn)
            busy=db.query('''SELECT 1 FROM test_competency_reservations WHERE user_id=%s AND competency_id=%s AND released_at IS NULL''',
                          (uid,cid),one=True,conn=conn)
            availability.append((comp, 'cooldown_30d' if cd else ('reserved_active' if busy else None),
                                 cd['available_at'] if cd else None))
        if not any(reason is None for _,reason,_ in availability):
            names = {str(row['id']): row['name'] for row in db.query(
                'SELECT id,name FROM competencies WHERE id=ANY(%s::uuid[])',
                ([comp['competency_id'] for comp,_,_ in availability],), conn=conn)}
            raise ConflictError('No competencies currently available', code='assessment_unavailable',
                details={'competencies': [
                    {'competency_id': str(comp['competency_id']),
                     'competency_name': comp.get('competency_name', names.get(str(comp['competency_id']), 'Компетенция')),
                     'reason': reason,
                     'available_at': available_at.isoformat() if available_at else None}
                    for comp,reason,available_at in availability]})
        frozen = snapshots.for_attempt(a['frozen_structure'],
            {str(comp['competency_id']) for comp, reason, _ in availability if reason is None})
        result=db.query('INSERT INTO test_attempts(assignment_id,immutable_snapshot) VALUES(%s,%s::jsonb) RETURNING id',
                        (aid,json.dumps(frozen)),one=True,conn=conn)
        attempt=result['id']
        position=0
        for comp,reason,available_at in availability:
            cid=comp['competency_id']
            excluded=reason is not None
            db.execute('''INSERT INTO attempt_competencies(attempt_id,competency_id,block_id,state,exclusion_reason,available_at,question_count)
                VALUES(%s,%s,%s,%s,%s,%s,%s)''', (attempt,cid,comp['block_id'], 'excluded' if excluded else 'included',
                 reason,available_at,
                 None if excluded else len(comp['questions'])),conn=conn)
            if excluded: continue
            db.execute('INSERT INTO test_competency_reservations(user_id,competency_id,attempt_id) VALUES(%s,%s,%s)',(uid,cid,attempt),conn=conn)
            for q in comp['questions']:
                position+=1
                db.execute('''INSERT INTO attempt_questions(attempt_id,question_id,competency_id,revision_id,position,frozen_question)
                    VALUES(%s,%s,%s,%s,%s,%s::jsonb)''',(attempt,q['id'],cid,q['revision_id'],position,json.dumps(q)),conn=conn)
        db.execute("UPDATE test_assignments SET status='in_progress' WHERE id=%s",(aid,),conn=conn)
    return json_response({'id':attempt})


def visible(q):
    return snapshots.visible_question(q)


def attempt_view(req, attempt_id):
    uid=actor(req,'employee')
    a=db.query('''SELECT t.status,t.immutable_snapshot,test.title AS test_title,x.test_version,
        v.frozen_structure AS published_snapshot
        FROM test_attempts t JOIN test_assignments x ON x.id=t.assignment_id
        JOIN tests test ON test.id=x.test_id
        JOIN test_versions v ON v.test_id=x.test_id AND v.version=x.test_version
        WHERE t.id=%s AND x.user_id=%s''',(ident(attempt_id),uid),one=True)
    if not a: raise PermissionError('Attempt unavailable')
    comps=db.query('''SELECT ac.competency_id,c.name AS competency_name,ac.state,ac.exclusion_reason,ac.available_at
        FROM attempt_competencies ac JOIN competencies c ON c.id=ac.competency_id
        WHERE ac.attempt_id=%s ORDER BY ac.competency_id''',(attempt_id,))
    included={str(c['competency_id']) for c in comps if c['state']=='included'}
    frozen = a['immutable_snapshot']
    names = {str(c['competency_id']): c.get('competency_name', 'Компетенция') for c in comps}
    for comp in snapshots.competencies(a.get('published_snapshot', frozen)):
        if comp.get('competency_name'): names[str(comp['competency_id'])] = comp['competency_name']
    for comp in comps:
        if 'competency_name' in comp: comp['competency_name'] = names[str(comp['competency_id'])]
    blocks = snapshots.public_blocks(frozen, included, a['status'] == 'in_progress', names)
    return json_response({'status': a['status'], 'test_title': frozen.get('test_title', a.get('test_title')),
        'test_version': frozen.get('test_version', a.get('test_version')), 'competencies': comps, 'blocks': blocks,
        'questions': [q for b in blocks for c in b['competencies'] for q in c['questions']]})


def submit(req, attempt_id):
    data=access.csrf(req)
    answers=data.get('answers', {})
    if not isinstance(answers,dict): raise ValueError('Answers object required')
    normalized={}
    for qid,selected in answers.items():
        if not isinstance(qid,str): raise ValueError('Invalid UUID')
        qid=ident(qid)
        if qid in normalized: raise ValueError('Duplicate question')
        normalized[qid]=selected
    answers=normalized
    uid=actor(req,'employee')
    with db.transaction() as conn:
        lock_user(conn,uid)
        a=db.query('''SELECT t.*,x.user_id FROM test_attempts t JOIN test_assignments x ON x.id=t.assignment_id
            WHERE t.id=%s AND x.user_id=%s FOR UPDATE OF t''',(ident(attempt_id),uid),one=True,conn=conn)
        if not a: raise PermissionError('Attempt unavailable')
        if a['status']!='in_progress': raise ConflictError('Already submitted')
        score_question, score_level = snapshots.scoring(a['immutable_snapshot'])
        comps=db.query("SELECT * FROM attempt_competencies WHERE attempt_id=%s AND state='included'",(attempt_id,),conn=conn)
        qs=db.query('SELECT question_id,competency_id,frozen_question FROM attempt_questions WHERE attempt_id=%s',(attempt_id,),conn=conn)
        if not set(answers)<={str(q['question_id']) for q in qs}: raise ValueError('Invalid question')
        scores={str(c['competency_id']):0 for c in comps}
        for q in qs:
            qid=str(q['question_id']); selected=answers.get(qid,[]); frozen=q['frozen_question']
            if not isinstance(selected,list) or len(selected)>20: raise ValueError('Invalid selection')
            if any(not isinstance(option,str) for option in selected): raise ValueError('Invalid UUID')
            selected=[ident(x) for x in selected]
            if len(selected)!=len(set(selected)): raise ValueError('Duplicate option')
            valid={o['id'] for o in frozen['options']}
            if not set(selected)<=valid or (frozen['type']=='single' and len(selected)>1): raise ValueError('Invalid option')
            correct=score_question(selected, frozen)
            scores[str(q['competency_id'])]+=int(correct)
            answer=db.query('''INSERT INTO attempt_answers(attempt_id,question_id,is_correct,awarded_points)
                VALUES(%s,%s,%s,%s) RETURNING id''',(attempt_id,qid,correct,int(correct)),one=True,conn=conn)['id']
            for option in selected:
                db.execute('INSERT INTO attempt_selected_options(attempt_answer_id,question_id,option_id) VALUES(%s,%s,%s)',(answer,qid,option),conn=conn)
        results=[]
        for c in comps:
            cid=str(c['competency_id']); count=c['question_count']; correct=scores[cid]
            level=score_level(correct, count)
            db.execute('''UPDATE attempt_competencies SET correct_count=%s,assessed_level=%s WHERE attempt_id=%s AND competency_id=%s''',
                       (correct,level,attempt_id,cid),conn=conn)
            db.execute('''INSERT INTO test_competency_results(attempt_id,competency_id,correct_count,total_count,level)
                VALUES(%s,%s,%s,%s,%s)''',(attempt_id,cid,correct,count,level),conn=conn)
            db.execute("INSERT INTO competency_assessment_events(user_id,competency_id,source_type,test_attempt_id,level) VALUES(%s,%s,'test',%s,%s)", (uid,cid,attempt_id,level),conn=conn)
            db.execute('''INSERT INTO employee_competencies(user_id,competency_id,current_level,last_level)
                VALUES(%s,%s,%s,%s) ON CONFLICT(user_id,competency_id) DO UPDATE SET
                last_level=EXCLUDED.current_level,
                current_level=GREATEST(employee_competencies.current_level,EXCLUDED.current_level),
                last_assessed_at=now(),assessed_at=now()''',(uid,cid,level,level),conn=conn)
            db.execute('''INSERT INTO assessment_cooldowns(user_id,competency_id,last_completed_at,available_at)
                VALUES(%s,%s,now(),now()+interval '30 days') ON CONFLICT(user_id,competency_id) DO UPDATE SET
                last_completed_at=EXCLUDED.last_completed_at,available_at=EXCLUDED.available_at''',(uid,cid),conn=conn)
            results.append({'competency_id':cid,'correct':correct,'total':count,'level':level})
        db.execute('UPDATE test_competency_reservations SET released_at=now() WHERE attempt_id=%s AND released_at IS NULL',(attempt_id,),conn=conn)
        db.execute("UPDATE test_attempts SET status='graded',finished_at=now(),score_percent=%s WHERE id=%s",
                   (round(100*sum(scores.values())/len(qs),2) if qs else 0,attempt_id),conn=conn)
        db.execute("UPDATE test_assignments SET status='completed' WHERE id=%s",(a['assignment_id'],),conn=conn)
    return json_response({'results':results})


def results(req):
    uid=actor(req)
    target=ident(req.q('user_id')) if req.q('user_id') else uid
    with db.transaction() as conn:
        own=str(target)==str(uid)
        did=None
        if not own:
            did=own_department(conn,req,'manager')
            if not db.query('SELECT 1 FROM users WHERE id=%s AND department_id=%s AND is_active',(target,did),one=True,conn=conn):
                raise PermissionError('Employee outside department')
        rows=db.query('''SELECT a.id AS assignment_id,a.status,a.assignment_type,a.due_at,a.assigned_at,test.title AS test_title,
            a.test_version,COALESCE(t.immutable_snapshot,v.frozen_structure) AS result_snapshot,
            t.id AS attempt_id,t.status AS attempt_status,
            r.competency_id,c.name AS competency_name,r.correct_count,r.total_count,r.level FROM test_assignments a
            JOIN tests test ON test.id=a.test_id
            LEFT JOIN test_attempts t ON t.assignment_id=a.id
            JOIN test_versions v ON v.test_id=a.test_id AND v.version=a.test_version
            LEFT JOIN test_competency_results r ON r.attempt_id=t.id
            LEFT JOIN competencies c ON c.id=r.competency_id
            WHERE a.user_id=%s AND (%s OR test.department_id=%s)
            ORDER BY a.assigned_at DESC LIMIT 200''',(target,own,did),conn=conn)
        for row in rows:
            frozen = row.pop('result_snapshot', None)
            if frozen and frozen.get('format_version') == 2:
                row['test_title'] = frozen['test_title']
                names = {str(c['competency_id']): c['competency_name'] for c in snapshots.competencies(frozen)}
                row['competency_name'] = names.get(str(row.get('competency_id')), row.get('competency_name'))
    return json_response(rows)


def register(r):
    from . import question_imports
    question_imports.register(r)
    for method,path,fn in (
        ('GET','/api/testing/questions',bank),('GET','/api/testing/questions/<qid>',question_detail),
        ('POST','/api/testing/questions',create_question),('POST','/api/testing/questions/<qid>/review',review_question),
        ('GET','/api/testing/tests',tests),('POST','/api/testing/tests',create_test),('POST','/api/testing/tests/<tid>/publish',publish),
        ('GET','/api/testing/tests/<tid>/draft',get_draft),('POST','/api/testing/tests/<tid>/draft',begin_draft),
        ('PUT','/api/testing/tests/<tid>/draft',save_draft),('POST','/api/testing/tests/<tid>/archive',archive),
        ('GET','/api/testing/tests/<tid>/versions',versions),('GET','/api/testing/tests/<tid>/versions/<version>',version_detail),
        ('POST','/api/testing/assignments',assign),('POST','/api/testing/assignments/<aid>/start',start),
        ('GET','/api/testing/attempts/<attempt_id>',attempt_view),('POST','/api/testing/attempts/<attempt_id>/submit',submit),
        ('GET','/api/testing/results',results)):
        r.route(method,path)(fn)
