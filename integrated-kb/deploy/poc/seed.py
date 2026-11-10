"""Seed business objects via real HTTPS API. Run once after bootstrap."""
import http.cookiejar
import json
import ssl
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

base = sys.argv[1]
credentials = json.load(sys.stdin)


class Client:
    def __init__(self, login):
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl._create_unverified_context()),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = None
        self.csrf = self.call('POST', '/api/login', {'login': login, 'password': credentials[login]})['csrf']

    def call(self, method, path, data=None):
        if data is not None:
            data = dict(data)
            if self.csrf:
                data['csrf'] = self.csrf
        req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                                     headers={'Content-Type': 'application/json'}, method=method)
        with self.opener.open(req) as response:
            return json.load(response)


admin = Client('demo_admin')
did = admin.call('POST', '/api/competencies/departments', {'code': 'DEMO', 'name': 'Демонстрационный отдел'})['id']
users = {}
for login, role, label in [('demo_manager', 'manager', 'Руководитель'), ('demo_kk', 'kk_responsible', 'Ответственный КК'),
                            ('demo_employee', 'employee', 'Сотрудник'), ('smoke_employee', 'employee', 'Smoke сотрудник')]:
    users[login] = admin.call('POST', '/api/admin/users', {'login': login, 'email': login+'@example.invalid',
        'full_name': 'Демо '+label, 'password': credentials[login], 'department_id': did, 'roles': [role]})['id']
admin.call('PUT', f'/api/competencies/departments/{did}/manager', {'manager_id': users['demo_manager']})
admin.call('POST', f'/api/competencies/departments/{did}/responsibles', {'user_id': users['demo_kk']})
category = admin.call('POST', '/api/competencies/categories', {'code': 'DEMO', 'name': 'Демонстрационные навыки'})['id']
comps = [admin.call('POST', '/api/competencies/catalog', {'category_id': category, 'code': code, 'name': name})['id']
         for code, name in [('WIKI', 'Работа с базой знаний'), ('SAFE', 'Безопасность данных')]]
kk, manager = Client('demo_kk'), Client('demo_manager')
pid = kk.call('POST', f'/api/competencies/departments/{did}/profiles', {'name': 'Демо карта компетенций'})['id']
for cid in comps:
    kk.call('PUT', f'/api/competencies/profiles/{pid}/competencies', {'competency_id': cid, 'target_level': 2})
kk.call('POST', f'/api/competencies/profiles/{pid}/submit', {})
manager.call('POST', f'/api/competencies/profiles/{pid}/review', {'decision': 'approve', 'expected_active_id': None, 'expected_active_version': None})
questions = [
    ('Где искать опубликованные инструкции?', 'В базе знаний', 'В случайных сообщениях'),
    ('Нужно ли проверять актуальность инструкции?', 'Да', 'Нет'),
    ('Можно ли публиковать пароли на странице?', 'Нет', 'Да'),
    ('Какие данные используются в демо?', 'Только вымышленные', 'Настоящие персональные данные'),
]
question_ids = [[], []]
for i, (body, correct, wrong) in enumerate(questions):
    qid = kk.call('POST', '/api/testing/questions', {'competency_id': comps[i//2], 'body': body, 'type': 'single',
        'options': [{'body': correct, 'correct': True}, {'body': wrong, 'correct': False}]})['id']
    kk.call('POST', f'/api/testing/questions/{qid}/review', {'decision': 'approve'})
    question_ids[i // 2].append(qid)
blocks = [{'title': 'Основной блок', 'competencies': [
    {'competency_id': cid, 'question_ids': question_ids[index]}
    for index, cid in enumerate(comps)]}]
tid = kk.call('POST', '/api/testing/tests', {
    'title': 'Демонстрационный тест: 4 вопроса', 'blocks': blocks})['id']
draft = kk.call('GET', f'/api/testing/tests/{tid}/draft')
kk.call('POST', f'/api/testing/tests/{tid}/publish', {'expected_token': draft['expected_token']})
due_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
assignments = {login: manager.call('POST', '/api/testing/assignments', {
                   'test_id': tid, 'user_id': users[login], 'due_at': due_at})['id']
               for login in ('demo_employee', 'smoke_employee')}
sid = admin.call('GET', '/api/admin/spaces')[0]['id']
page = admin.call('GET', '/api/tree')[0]['id']
print(json.dumps({'department': did, 'profile': pid, 'competencies': comps, 'test': tid, 'users': users,
                  'assignments': assignments, 'space': sid, 'page': page}, ensure_ascii=False, indent=2))
