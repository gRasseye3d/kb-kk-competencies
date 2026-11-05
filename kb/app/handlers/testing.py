"""Department-only primary tests. Published versions and attempt snapshots are write-once."""
import json
from datetime import datetime

from .. import access, db
from ..utils import ConflictError, json_response
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
    with db.transaction() as conn:
        did=own_department(conn,req,'responsible')
        rows=db.query('''SELECT q.id,q.competency_id,c.name AS competency_name,q.body,q.question_type,q.moderation_status
            FROM questions q JOIN profile_competencies pc ON pc.competency_id=q.competency_id
            JOIN competencies c ON c.id=q.competency_id
            JOIN competency_profiles p ON p.id=pc.profile_id
            WHERE p.department_id=%s AND p.is_active AND q.bank_department_id=%s
            ORDER BY q.created_at DESC LIMIT 200''',(did,did),conn=conn)
    return json_response(rows)


def question_detail(req, qid):
    with db.transaction() as conn:
        did=own_department(conn,req,'responsible')
        q=db.query('''SELECT id,competency_id,body,question_type,moderation_status FROM questions
            WHERE id=%s AND bank_department_id=%s''',(ident(qid),did),one=True,conn=conn)
        if not q: raise PermissionError('Question unavailable')
        q['options']=db.query('''SELECT id,position,body,is_correct FROM answer_options
            WHERE question_id=%s ORDER BY position''',(qid,),conn=conn)
    return json_response(q)


def tests(req):
    actor(req)
    with db.transaction() as conn:
        # Roles alone do not grant access. A failed appointment check must not
        # hide the rights granted by another role in the same own department.
        for kind in ('responsible','manager'):
            try:
                did = own_department(conn,req,kind)
            except PermissionError:
                continue
            break
        else:
            raise PermissionError('Department test access denied')
        rows = db.query('''SELECT t.id,t.title,t.status,
            (SELECT max(v.version) FROM test_versions v WHERE v.test_id=t.id) AS version
            FROM tests t WHERE t.department_id=%s AND
            (t.status='published' OR (%s AND t.status IN ('draft','archived')))
            ORDER BY t.title,t.id''',(did,kind=='responsible'),conn=conn)
        for row in rows:
            row['competencies'] = db.query('''SELECT DISTINCT c.id,c.name
                FROM test_block_competencies bc JOIN competencies c ON c.id=bc.competency_id
                WHERE bc.test_id=%s ORDER BY c.name,c.id''',(row['id'],),conn=conn)
    return json_response(rows)


def create_test(req):
    data = access.csrf(req)
    ids = data.get('competency_ids')
    if not isinstance(ids,list) or len(ids)<2 or len(ids)>20:
        raise ValueError('At least two competencies required')
    ids = [ident(x) for x in ids]
    if len(set(ids))!=len(ids): raise ValueError('Duplicate competency')
    with db.transaction() as conn:
        did = own_department(conn,req,'responsible')
        rows = db.query('''SELECT pc.competency_id FROM competency_profiles p JOIN profile_competencies pc ON pc.profile_id=p.id
            JOIN competencies c ON c.id=pc.competency_id WHERE p.department_id=%s AND p.is_active AND c.is_active AND pc.competency_id=ANY(%s::uuid[])''',
            (did,ids),conn=conn)
        if len(rows)!=len(ids): raise PermissionError('Competency outside profile')
        tid = db.query('''INSERT INTO tests(title,scope,department_id,created_by) VALUES(%s,'department',%s,%s) RETURNING id''',
                       (text(data.get('title'),250),did,req.user['user_id']),one=True,conn=conn)['id']
        bid = db.query('INSERT INTO test_blocks(test_id,title,position) VALUES(%s,%s,1) RETURNING id',
                       (tid,'Основной блок'),one=True,conn=conn)['id']
        for pos,cid in enumerate(ids,1):
            db.query('INSERT INTO test_block_competencies(test_id,block_id,competency_id,position) VALUES(%s,%s,%s,%s) RETURNING id',
                     (tid,bid,cid,pos),one=True,conn=conn)
    return json_response({'id':tid})


def publish(req, tid):
    access.csrf(req)
    with db.transaction() as conn:
        did = own_department(conn,req,'responsible')
        t = db.query('SELECT * FROM tests WHERE id=%s AND department_id=%s FOR UPDATE', (ident(tid),did),one=True,conn=conn)
        if not t: raise PermissionError('Test unavailable')
        if t['status']!='draft': raise ConflictError('Published test cannot be modified')
        comps = db.query('''SELECT bc.competency_id,bc.block_id FROM test_block_competencies bc
            WHERE bc.test_id=%s ORDER BY bc.position''',(tid,),conn=conn)
        if len(comps)<2: raise ValueError('At least two competencies required')
        # Select candidates without locking, then acquire ALL question row locks in
        # UUID order. A publication of another test sharing questions cannot race
        # the max(version)+1 allocation or deadlock on reversed competency order.
        candidates = []
        for comp in comps:
            qs = db.query('''SELECT q.id,q.body,q.question_type FROM questions q
                WHERE q.competency_id=%s AND q.moderation_status='approved' AND q.is_active
                  AND q.bank_department_id=%s
                ORDER BY q.created_at,q.id LIMIT 50''', (comp['competency_id'],did),conn=conn)
            if len(qs)<2: raise ValueError('Two approved questions per competency required')
            candidates.append(qs)
        ids=sorted(str(q['id']) for qs in candidates for q in qs)
        locked=db.query('''SELECT id,competency_id,body,question_type FROM questions
            WHERE id=ANY(%s::uuid[]) AND moderation_status='approved' AND is_active
              AND bank_department_id=%s ORDER BY id FOR UPDATE''',(ids,did),conn=conn)
        by_id={str(q['id']):q for q in locked}
        if len(by_id)!=len(ids): raise ConflictError('Question bank changed; retry publishing')
        frozen_by_id={}
        for qid in ids:
            q=by_id[qid]
            opts=db.query('SELECT id,body,is_correct FROM answer_options WHERE question_id=%s ORDER BY position FOR SHARE', (qid,),conn=conn)
            if len(opts)<2 or not any(o['is_correct'] for o in opts): raise ValueError('Incomplete question')
            revision=db.query('''INSERT INTO question_revisions(question_id,version,body,question_type,answer_key_snapshot,created_by)
                VALUES(%s,COALESCE((SELECT max(version)+1 FROM question_revisions WHERE question_id=%s),1),%s,%s,%s::jsonb,%s) RETURNING id''',
                (qid,qid,q['body'],q['question_type'],json.dumps([str(o['id']) for o in opts if o['is_correct']]),req.user['user_id']),one=True,conn=conn)['id']
            frozen_by_id[qid]={'id':qid,'revision_id':str(revision),'body':q['body'],'type':q['question_type'],
                'options':[{'id':str(o['id']),'body':o['body'],'correct':o['is_correct']} for o in opts]}
        structure = []
        for comp,qs in zip(comps,candidates):
            frozen=[]
            for q in qs:
                if str(by_id[str(q['id'])]['competency_id'])!=str(comp['competency_id']):
                    raise ConflictError('Question competency changed; retry publishing')
                frozen.append(frozen_by_id[str(q['id'])])
            structure.append({'competency_id':str(comp['competency_id']),'block_id':str(comp['block_id']),'questions':frozen})
        db.query('INSERT INTO test_versions(test_id,version,frozen_structure,published_by) VALUES(%s,1,%s::jsonb,%s) RETURNING id',
                 (tid,json.dumps({'competencies':structure}),req.user['user_id']),one=True,conn=conn)
        db.execute("UPDATE tests SET status='published' WHERE id=%s",(tid,),conn=conn)
    return json_response({'version':1})


def assign(req):
    data=access.csrf(req)
    uid,tid=ident(data.get('user_id')),ident(data.get('test_id'))
    due=data.get('due_at')
    if due is not None:
        if not isinstance(due,str): raise ValueError('Invalid deadline')
        try:
            due=datetime.fromisoformat(due.replace('Z','+00:00'))
            if due.tzinfo is None or due.utcoffset() is None: raise ValueError()
        except ValueError:
            raise ValueError('Deadline must be future ISO 8601 with timezone')
    with db.transaction() as conn:
        did=own_department(conn,req,'manager')
        if not db.query('SELECT 1 FROM users WHERE id=%s AND department_id=%s AND is_active',(uid,did),one=True,conn=conn):
            raise PermissionError('Employee outside department')
        if not db.query("SELECT 1 FROM tests WHERE id=%s AND department_id=%s AND status='published'",(tid,did),one=True,conn=conn):
            raise PermissionError('Test unavailable')
        row=db.query('''INSERT INTO test_assignments(test_id,user_id,assigned_by,test_version,due_at)
            SELECT %s,%s,%s,1,%s WHERE %s::timestamptz IS NULL OR %s::timestamptz > clock_timestamp()
            RETURNING id''',(tid,uid,req.user['user_id'],due,due,due),one=True,conn=conn)
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
        if a['due_at'] and not db.query('SELECT %s::timestamptz > clock_timestamp() AS valid',(a['due_at'],),one=True,conn=conn)['valid']:
            raise ConflictError('Deadline passed')
        comps=a['frozen_structure']['competencies']
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
                     'competency_name': names.get(str(comp['competency_id']), 'Компетенция'),
                     'reason': reason,
                     'available_at': available_at.isoformat() if available_at else None}
                    for comp,reason,available_at in availability]})
        result=db.query('INSERT INTO test_attempts(assignment_id,immutable_snapshot) VALUES(%s,%s::jsonb) RETURNING id',
                        (aid,json.dumps(a['frozen_structure'])),one=True,conn=conn)
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
    return {k:v for k,v in q.items() if k in ('id','body','type','options')} | {'options':[
        {'id':o['id'],'body':o['body']} for o in q['options']]}


def attempt_view(req, attempt_id):
    uid=actor(req,'employee')
    a=db.query('''SELECT t.status,t.immutable_snapshot,test.title AS test_title
        FROM test_attempts t JOIN test_assignments x ON x.id=t.assignment_id
        JOIN tests test ON test.id=x.test_id
        WHERE t.id=%s AND x.user_id=%s''',(ident(attempt_id),uid),one=True)
    if not a: raise PermissionError('Attempt unavailable')
    comps=db.query('''SELECT ac.competency_id,c.name AS competency_name,ac.state,ac.exclusion_reason,ac.available_at
        FROM attempt_competencies ac JOIN competencies c ON c.id=ac.competency_id
        WHERE ac.attempt_id=%s''',(attempt_id,))
    included={str(c['competency_id']) for c in comps if c['state']=='included'}
    return json_response({'status':a['status'],'test_title':a.get('test_title'),'competencies':comps,'questions':[
        visible(q) for c in a['immutable_snapshot']['competencies'] if c['competency_id'] in included for q in c['questions']
    ] if a['status']=='in_progress' else []})


def submit(req, attempt_id):
    data=access.csrf(req)
    answers=data.get('answers')
    if not isinstance(answers,dict): raise ValueError('Answers object required')
    uid=actor(req,'employee')
    with db.transaction() as conn:
        lock_user(conn,uid)
        a=db.query('''SELECT t.*,x.user_id FROM test_attempts t JOIN test_assignments x ON x.id=t.assignment_id
            WHERE t.id=%s AND x.user_id=%s FOR UPDATE OF t''',(ident(attempt_id),uid),one=True,conn=conn)
        if not a: raise PermissionError('Attempt unavailable')
        if a['status']!='in_progress': raise ConflictError('Already submitted')
        comps=db.query("SELECT * FROM attempt_competencies WHERE attempt_id=%s AND state='included'",(attempt_id,),conn=conn)
        qs=db.query('SELECT question_id,competency_id,frozen_question FROM attempt_questions WHERE attempt_id=%s',(attempt_id,),conn=conn)
        if set(answers)!={str(q['question_id']) for q in qs}: raise ValueError('Answer every included question exactly once')
        scores={str(c['competency_id']):0 for c in comps}
        for q in qs:
            qid=str(q['question_id']); selected=answers[qid]; frozen=q['frozen_question']
            if not isinstance(selected,list) or not selected or len(selected)>20: raise ValueError('Invalid selection')
            selected=[ident(x) for x in selected]
            if len(selected)!=len(set(selected)): raise ValueError('Duplicate option')
            valid={o['id'] for o in frozen['options']}
            if not set(selected)<=valid or (frozen['type']=='single' and len(selected)!=1): raise ValueError('Invalid option')
            correct=set(selected)=={o['id'] for o in frozen['options'] if o['correct']}
            scores[str(q['competency_id'])]+=int(correct)
            answer=db.query('''INSERT INTO attempt_answers(attempt_id,question_id,is_correct,awarded_points)
                VALUES(%s,%s,%s,%s) RETURNING id''',(attempt_id,qid,correct,int(correct)),one=True,conn=conn)['id']
            for option in selected:
                db.execute('INSERT INTO attempt_selected_options(attempt_answer_id,question_id,option_id) VALUES(%s,%s,%s)',(answer,qid,option),conn=conn)
        results=[]
        for c in comps:
            cid=str(c['competency_id']); count=c['question_count']; correct=scores[cid]
            level=0 if correct==0 else (2 if correct==count else 1)
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
        rows=db.query('''SELECT a.id AS assignment_id,a.status,a.due_at,a.assigned_at,test.title AS test_title,
            t.id AS attempt_id,t.status AS attempt_status,
            r.competency_id,c.name AS competency_name,r.correct_count,r.total_count,r.level FROM test_assignments a
            JOIN tests test ON test.id=a.test_id
            LEFT JOIN test_attempts t ON t.assignment_id=a.id
            LEFT JOIN test_competency_results r ON r.attempt_id=t.id
            LEFT JOIN competencies c ON c.id=r.competency_id
            WHERE a.user_id=%s AND (%s OR test.department_id=%s)
            ORDER BY a.assigned_at DESC LIMIT 200''',(target,own,did),conn=conn)
    return json_response(rows)


def register(r):
    for method,path,fn in (
        ('GET','/api/testing/questions',bank),('GET','/api/testing/questions/<qid>',question_detail),
        ('POST','/api/testing/questions',create_question),('POST','/api/testing/questions/<qid>/review',review_question),
        ('GET','/api/testing/tests',tests),('POST','/api/testing/tests',create_test),('POST','/api/testing/tests/<tid>/publish',publish),
        ('POST','/api/testing/assignments',assign),('POST','/api/testing/assignments/<aid>/start',start),
        ('GET','/api/testing/attempts/<attempt_id>',attempt_view),('POST','/api/testing/attempts/<attempt_id>/submit',submit),
        ('GET','/api/testing/results',results)):
        r.route(method,path)(fn)
