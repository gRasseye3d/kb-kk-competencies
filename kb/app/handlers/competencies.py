"""Catalog and departmental competency maps (schema v4 only)."""
import re
import uuid

from .. import db, access, audit
from ..utils import json_response, ConflictError, PreconditionRequired


def ident(value):
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise ValueError('Invalid UUID')


def text(value, length):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > length:
        raise ValueError('Invalid text')
    return value.strip()


def code(value, length):
    value = text(value, length)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('Invalid code')
    return value


def actor(req, role=None):
    if not req.user:
        raise PermissionError('Authentication required')
    if role and role not in req.user['roles']:
        raise PermissionError('Role required')
    return req.user['user_id']


def department(conn, did, user, kind):
    # Lock the department for all map mutations: serializes version allocation and active switching.
    d = db.query('SELECT id,manager_id,is_active FROM departments WHERE id=%s FOR UPDATE',
                 (ident(did),), one=True, conn=conn)
    if not d or not d['is_active']:
        raise PermissionError('Department unavailable')
    if kind == 'manager':
        if 'manager' not in user['roles'] or str(d['manager_id']) != str(user['user_id']):
            raise PermissionError('Not department manager')
    elif kind == 'responsible':
        if 'kk_responsible' not in user['roles'] or not db.query(
                '''SELECT 1 FROM department_kk_responsibles x JOIN users u ON u.id=x.user_id
                   WHERE x.department_id=%s AND x.user_id=%s AND u.department_id=x.department_id AND u.is_active''',
                (did, user['user_id']), one=True, conn=conn):
            raise PermissionError('Not department responsible')
    return d


def profile(conn, pid):
    p = db.query('SELECT * FROM competency_profiles WHERE id=%s FOR UPDATE', (ident(pid),), one=True, conn=conn)
    if not p:
        raise PermissionError('Profile unavailable')
    return p


def register(r):
    for method, path, fn in (
        ('GET','/api/competencies/catalog',catalog),
        ('POST','/api/competencies/categories',create_category),
        ('POST','/api/competencies/catalog',create_competency),
        ('GET','/api/competencies/departments',departments),
        ('GET','/api/competencies/departments/<did>/members',members),
        ('POST','/api/competencies/departments',create_department),
        ('PUT','/api/competencies/departments/<did>/manager',set_manager),
        ('POST','/api/competencies/departments/<did>/responsibles',add_responsible),
        ('DELETE','/api/competencies/departments/<did>/responsibles/<uid>',remove_responsible),
        ('GET','/api/competencies/departments/<did>/profiles',profiles),
        ('GET','/api/competencies/departments/<did>/active',active_profile),
        ('POST','/api/competencies/departments/<did>/profiles',create_profile),
        ('PUT','/api/competencies/profiles/<pid>/competencies',add_to_profile),
        ('POST','/api/competencies/profiles/<pid>/submit',submit),
        ('POST','/api/competencies/profiles/<pid>/review',review),
        ('GET','/api/competencies/mine',mine),
        ('GET','/api/competencies/departments/<did>/summary',summary),
    ):
        r.route(method,path)(fn)


def catalog(req):
    actor(req)
    return json_response({'categories': db.query('SELECT id,parent_id,code,name FROM competency_categories WHERE is_active ORDER BY position,name LIMIT 2000'),
        'competencies': db.query('SELECT id,category_id,code,name,description,assessment_criteria FROM competencies WHERE is_active ORDER BY position,name LIMIT 2000')})


def create_category(req):
    data = access.csrf(req)
    actor(req, 'admin')
    parent = ident(data['parent_id']) if data.get('parent_id') else None
    with db.transaction() as conn:
        if parent and not db.query('SELECT 1 FROM competency_categories WHERE id=%s AND is_active', (parent,), one=True, conn=conn):
            raise ValueError('Unknown parent category')
        cid = db.query('INSERT INTO competency_categories(code,name,parent_id) VALUES(%s,%s,%s) RETURNING id',
                       (code(data.get('code'),50),text(data.get('name'),200),parent),one=True,conn=conn)['id']
        audit.log(req.user['user_id'],'category.create','category',cid,conn=conn)
    return json_response({'id':cid})


def create_competency(req):
    data = access.csrf(req)
    actor(req, 'admin')
    cid = ident(data.get('category_id'))
    with db.transaction() as conn:
        if not db.query('SELECT 1 FROM competency_categories WHERE id=%s AND is_active', (cid,),one=True,conn=conn):
            raise ValueError('Unknown category')
        new = db.query('''INSERT INTO competencies(category_id,code,name,description,assessment_criteria)
            VALUES(%s,%s,%s,%s,%s) RETURNING id''', (cid,code(data.get('code'),80),text(data.get('name'),255),
            data.get('description') or None,data.get('assessment_criteria') or None),one=True,conn=conn)['id']
        audit.log(req.user['user_id'],'competency.create','competency',new,conn=conn)
    return json_response({'id':new})


def departments(req):
    actor(req)
    return json_response(db.query('''SELECT d.id,d.code,d.name,d.manager_id,
        EXISTS(SELECT 1 FROM department_kk_responsibles x JOIN users u ON u.id=x.user_id
            WHERE x.department_id=d.id AND x.user_id=%s AND u.department_id=d.id AND u.is_active) AS responsible
        FROM departments d WHERE d.is_active AND ((%s AND d.manager_id=%s) OR
        (%s AND EXISTS(SELECT 1 FROM department_kk_responsibles x JOIN users u ON u.id=x.user_id
            WHERE x.department_id=d.id AND x.user_id=%s AND u.department_id=d.id AND u.is_active)) OR %s)
        ORDER BY d.name''', (req.user['user_id'], 'manager' in req.user['roles'],req.user['user_id'],
                             'kk_responsible' in req.user['roles'],req.user['user_id'],
                             'admin' in req.user['roles'])))


def members(req,did):
    actor(req,'manager')
    with db.transaction() as conn:
        department(conn,did,req.user,'manager')
        rows = db.query('''SELECT id,display_name,username AS login FROM users
            WHERE department_id=%s AND is_active ORDER BY display_name,username,id''',
            (did,),conn=conn)
    return json_response(rows)


def create_department(req):
    data = access.csrf(req)
    actor(req,'admin')
    if data.get('manager_id'):
        raise ValueError('Create department first, then assign its manager')
    with db.transaction() as conn:
        did = db.query('INSERT INTO departments(code,name,manager_id) VALUES(%s,%s,%s) RETURNING id',
            (code(data.get('code'),50),text(data.get('name'),200),None),one=True,conn=conn)['id']
        audit.log(req.user['user_id'],'department.create','department',did,conn=conn)
    return json_response({'id':did})


def eligible_manager(conn, uid):
    if not db.query('''SELECT 1 FROM users u JOIN user_roles ur ON ur.user_id=u.id
        JOIN roles r ON r.id=ur.role_id WHERE u.id=%s AND u.is_active AND r.code='manager' ''',
        (uid,),one=True,conn=conn):
        raise ValueError('Manager must have manager role and be active')


def manager_in_department(conn, uid, did):
    eligible_manager(conn, uid)
    if not db.query('SELECT 1 FROM users WHERE id=%s AND department_id=%s',
                    (uid, did), one=True, conn=conn):
        raise ValueError('Manager must belong to department')


def set_manager(req,did):
    data = access.csrf(req)
    actor(req,'admin')
    if 'manager_id' not in data or (data['manager_id'] is not None and not data['manager_id']):
        raise ValueError('manager_id required (null to clear)')
    uid = ident(data['manager_id']) if data['manager_id'] is not None else None
    with db.transaction() as conn:
        department(conn,did,req.user,'admin')
        if uid: manager_in_department(conn,uid,did)
        db.execute('UPDATE departments SET manager_id=%s WHERE id=%s',(uid,did),conn)
        audit.log(req.user['user_id'],'department.manager','department',did,conn=conn)
    return json_response({'ok':True})


def add_responsible(req,did):
    data = access.csrf(req)
    actor(req,'admin')
    uid = ident(data.get('user_id'))
    with db.transaction() as conn:
        department(conn,did,req.user,'admin')
        if not db.query('''SELECT 1 FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN roles r ON r.id=ur.role_id
            WHERE u.id=%s AND u.department_id=%s AND u.is_active AND r.code='kk_responsible' ''',
            (uid,did),one=True,conn=conn):
            raise ValueError('Responsible must belong to department and have kk_responsible role')
        db.execute('''INSERT INTO department_kk_responsibles(department_id,user_id,appointed_by)
            VALUES(%s,%s,%s) ON CONFLICT DO NOTHING''',(did,uid,req.user['user_id']),conn)
        audit.log(req.user['user_id'],'department.responsible.add','department',did,conn=conn)
    return json_response({'ok':True})


def remove_responsible(req,did,uid):
    access.csrf(req)
    actor(req,'admin')
    with db.transaction() as conn:
        department(conn,did,req.user,'admin')
        db.execute('DELETE FROM department_kk_responsibles WHERE department_id=%s AND user_id=%s',
                   (did,ident(uid)),conn)
        audit.log(req.user['user_id'],'department.responsible.remove','department',did,conn=conn)
    return json_response({'ok':True})


def profiles(req,did):
    actor(req)
    with db.transaction() as conn:
        department_access(conn,did,req.user)
        rows = db.query('SELECT id,name,version,status,is_active,review_note FROM competency_profiles WHERE department_id=%s ORDER BY version DESC LIMIT 100',
                        (did,),conn=conn)
        for p in rows:
            p['competencies'] = db.query('''SELECT pc.competency_id,c.name,pc.target_level FROM profile_competencies pc
                JOIN competencies c ON c.id=pc.competency_id WHERE pc.profile_id=%s ORDER BY c.name''',(p['id'],),conn=conn)
    return json_response(rows)


def department_access(conn,did,user):
    department(conn,did,user,'admin')
    permitted = ('manager' in user['roles'] and db.query(
            'SELECT 1 FROM departments WHERE id=%s AND manager_id=%s',
            (did,user['user_id']),one=True,conn=conn)) or (
            'kk_responsible' in user['roles'] and db.query(
            '''SELECT 1 FROM department_kk_responsibles x JOIN users u ON u.id=x.user_id
                WHERE x.department_id=%s AND x.user_id=%s AND u.department_id=x.department_id AND u.is_active''',
            (did,user['user_id']),one=True,conn=conn))
    if not permitted:
        raise PermissionError('Department access denied')


def active_profile(req,did):
    actor(req,'manager')
    with db.transaction() as conn:
        department(conn,did,req.user,'manager')
        current = db.query('SELECT id,version FROM competency_profiles WHERE department_id=%s AND is_active',
                           (did,),one=True,conn=conn)
    return json_response(current or {'id':None,'version':None})


def create_profile(req,did):
    data = access.csrf(req)
    actor(req,'kk_responsible')
    with db.transaction() as conn:
        department(conn,did,req.user,'responsible')
        name = text(data.get('name'),200)
        version = db.query('SELECT COALESCE(MAX(version),0)+1 AS n FROM competency_profiles WHERE department_id=%s AND name=%s',
                           (did,name),one=True,conn=conn)['n']
        pid = db.query('''INSERT INTO competency_profiles(department_id,name,version,prepared_by)
            VALUES(%s,%s,%s,%s) RETURNING id''',(did,name,version,req.user['user_id']),one=True,conn=conn)['id']
        audit.log(req.user['user_id'],'profile.create','profile',pid,conn=conn)
    return json_response({'id':pid,'version':version})


def editable(conn,p,req):
    department(conn,p['department_id'],req.user,'responsible')
    if p['status'] not in ('draft','returned'):
        raise PermissionError('Profile is not editable')


def add_to_profile(req,pid):
    data = access.csrf(req)
    actor(req,'kk_responsible')
    cid = ident(data.get('competency_id'))
    level = data.get('target_level')
    if type(level) is not int or not 0 <= level <= 3:
        raise ValueError('Invalid target level')
    with db.transaction() as conn:
        p = profile(conn,pid)
        editable(conn,p,req)
        if not db.query('''SELECT 1 FROM competencies c JOIN competency_categories cat ON cat.id=c.category_id
            WHERE c.id=%s AND c.is_active AND cat.is_active''',(cid,),one=True,conn=conn):
            raise ValueError('Competency unavailable')
        db.execute('''INSERT INTO profile_competencies(profile_id,competency_id,target_level) VALUES(%s,%s,%s)
            ON CONFLICT(profile_id,competency_id) DO UPDATE SET target_level=EXCLUDED.target_level''',
            (pid,cid,level),conn)
        audit.log(req.user['user_id'],'profile.competency','profile',pid,conn=conn)
    return json_response({'ok':True})


def submit(req,pid):
    access.csrf(req)
    actor(req,'kk_responsible')
    with db.transaction() as conn:
        p = profile(conn,pid)
        editable(conn,p,req)
        if not db.query('SELECT 1 FROM profile_competencies WHERE profile_id=%s LIMIT 1',(pid,),one=True,conn=conn):
            raise ValueError('Empty profile')
        db.execute("UPDATE competency_profiles SET status='pending_approval',submitted_at=now(),review_note=NULL WHERE id=%s",(pid,),conn)
        audit.log(req.user['user_id'],'profile.submit','profile',pid,conn=conn)
    return json_response({'ok':True})


def review(req,pid):
    data = access.csrf(req)
    actor(req,'manager')
    decision = data.get('decision')
    if decision not in ('approve','return'):
        raise ValueError('Invalid decision')
    note = data.get('note','')
    if not isinstance(note,str) or len(note) > 2000 or (decision == 'return' and not note.strip()):
        raise ValueError('Review note required or too long')
    if decision == 'approve' and ('expected_active_id' not in data or 'expected_active_version' not in data):
        raise PreconditionRequired('expected_active_id and expected_active_version required (null if no active profile)')
    expected = data.get('expected_active_id')
    expected_version = data.get('expected_active_version')
    if decision == 'approve' and expected is not None:
        expected = ident(expected)
    if decision == 'approve' and ((expected is None and expected_version is not None) or
                                  (expected is not None and (type(expected_version) is not int or expected_version < 1))):
        raise ValueError('Invalid expected active version')
    with db.transaction() as conn:
        p = profile(conn,pid)
        department(conn,p['department_id'],req.user,'manager')
        if p['status'] != 'pending_approval':
            raise ConflictError('Profile not pending approval')
        if decision == 'approve':
            current = db.query('SELECT id,version FROM competency_profiles WHERE department_id=%s AND is_active',
                               (p['department_id'],),one=True,conn=conn)
            if ((ident(current['id']) if current else None) != expected or
                    (current['version'] if current else None) != expected_version):
                raise ConflictError('Active profile changed; refresh before approving')
            db.execute('''UPDATE competency_profiles SET is_active=FALSE,status='archived'
                WHERE department_id=%s AND is_active''',(p['department_id'],),conn)
            db.execute('''UPDATE competency_profiles SET status='approved',is_active=TRUE,approved_by=%s,
                approved_at=now(),review_note=%s WHERE id=%s''',(req.user['user_id'],note,pid),conn)
        else:
            db.execute("UPDATE competency_profiles SET status='returned',review_note=%s WHERE id=%s",(note,pid),conn)
        audit.log(req.user['user_id'],'profile.'+decision,'profile',pid,conn=conn)
    return json_response({'ok':True})


def mine(req):
    uid = actor(req)
    return json_response(db.query('''SELECT c.id,c.code,c.name,pc.target_level,ec.current_level,
        CASE WHEN ec.user_id IS NULL THEN 'Не оценено' ELSE ec.current_level::text END AS assessment
        FROM users u JOIN competency_profiles p ON p.department_id=u.department_id AND p.is_active
        JOIN profile_competencies pc ON pc.profile_id=p.id JOIN competencies c ON c.id=pc.competency_id
        LEFT JOIN employee_competencies ec ON ec.user_id=u.id AND ec.competency_id=c.id
        WHERE u.id=%s ORDER BY c.name''',(uid,)))


def summary(req,did):
    actor(req,'manager')
    with db.transaction() as conn:
        department(conn,did,req.user,'manager')
        rows = db.query('''SELECT u.id AS user_id,u.display_name,c.id AS competency_id,c.name AS competency_name,
            pc.target_level,ec.current_level,
            CASE WHEN ec.user_id IS NULL THEN 'Не оценено' ELSE ec.current_level::text END AS assessment
            FROM users u JOIN competency_profiles p ON p.department_id=u.department_id AND p.is_active
            JOIN profile_competencies pc ON pc.profile_id=p.id JOIN competencies c ON c.id=pc.competency_id
            LEFT JOIN employee_competencies ec ON ec.user_id=u.id AND ec.competency_id=c.id
            WHERE u.department_id=%s AND u.is_active ORDER BY u.display_name,c.name''',(did,),conn=conn)
    return json_response(rows)
