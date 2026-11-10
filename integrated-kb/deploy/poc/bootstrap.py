"""Fresh DB bootstrap only; credentials are provided on stdin, never logged."""
import json
import sys
from app import db
from app.auth import hash_password

credentials = json.load(sys.stdin)
with db.transaction() as conn:
    if db.query('SELECT 1 FROM users LIMIT 1', one=True, conn=conn):
        raise SystemExit('Refusing to bootstrap a nonempty database')
    for role in ('admin', 'manager', 'kk_responsible', 'employee', 'moderator'):
        db.execute('INSERT INTO roles(code,name) VALUES(%s,%s)', (role, role), conn)
    uid = db.query('''INSERT INTO users(username,email,display_name,password_hash)
        VALUES('demo_admin','demo_admin@example.invalid','Демо администратор',%s) RETURNING id''',
        (hash_password(credentials['demo_admin']),), one=True, conn=conn)['id']
    db.execute("INSERT INTO user_roles(user_id,role_id) SELECT %s,id FROM roles WHERE code='admin'", (uid,), conn)
    sid = db.query("INSERT INTO spaces(space_key,name,owner_id,visibility) VALUES('DEMO','Демонстрационная база знаний',%s,'internal') RETURNING id",
                   (uid,), one=True, conn=conn)['id']
    title = 'Добро пожаловать в демо'
    pid = db.query("INSERT INTO pages(space_id,title,status,current_version,created_by,updated_by) VALUES(%s,%s,'published',1,%s,%s) RETURNING id",
                   (sid,title,uid,uid), one=True, conn=conn)['id']
    db.execute('INSERT INTO page_revisions(page_id,version,title,content,editor_id) VALUES(%s,1,%s,%s::jsonb,%s)',
               (pid,title,json.dumps({'html': '<h2>Учебный стенд</h2><p>Только вымышленные данные. Откройте Компетенции и Тестирование. Основное назначение сотрудника ещё не начато.</p>'}),uid),conn)
print('Bootstrap admin created using PBKDF2')
