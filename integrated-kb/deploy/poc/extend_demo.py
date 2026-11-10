"""Add an isolated demo cohort through a LOCAL disposable PoC API; never reseed existing data.

Run on the dev host, not inside the app container. See EXTEND_DEMO.md.
Database access is exclusively read-only evidence collection and pg_dump.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import subprocess
import urllib.request


ROOT = Path(os.environ.get('KB_DEMO_ROOT', Path(__file__).resolve().parents[2])).resolve()
BASE = os.environ.get('KB_DEMO_BASE', 'https://localhost:18575')
HUMANS = ['demo_employee_02', 'demo_employee_03', 'demo_employee_04']
EPHEMERAL = {'sessions', 'login_rate_limits'}
COMPETENCIES = {
    'SEARCH': 'Поиск информации', 'EDIT': 'Подготовка инструкций',
    'REVIEW': 'Проверка актуальности', 'COLLAB': 'Совместная работа',
    'ACCESS': 'Управление доступом', 'INCIDENT': 'Реагирование на инциденты',
}
TESTS = {
    'A': ('Поиск и подготовка', ['SEARCH', 'EDIT']),
    'B': ('Совместная проверка', ['EDIT', 'REVIEW', 'COLLAB']),
    'C': ('Доступ и инциденты', ['ACCESS', 'INCIDENT']),
}
# Each competency has a single-choice and an exact-set multiple-choice question.
QUESTIONS = {
    'SEARCH': [
        ('Где в первую очередь искать действующую инструкцию по рабочему процессу?',
         ['В опубликованной базе знаний своего подразделения'],
         ['В старой личной переписке', 'В случайной статье без указания владельца']),
        ('Что помогает найти нужную инструкцию в базе знаний? Выберите все подходящие действия.',
         ['Искать по ключевым словам задачи', 'Уточнять раздел и метки документа'],
         ['Считать первый результат всегда подходящим']),
    ],
    'EDIT': [
        ('Как лучше описать последовательность действий в рабочей инструкции?',
         ['Пронумеровать шаги и указать ожидаемый результат'],
         ['Заменить шаги общим пожеланием', 'Оставить только название процесса']),
        ('Что следует включить в новую инструкцию? Выберите все подходящие пункты.',
         ['Условия начала работы и необходимые права', 'Проверку результата и действия при ошибке'],
         ['Пароль общего служебного аккаунта']),
    ],
    'REVIEW': [
        ('Вы заметили, что скриншот в инструкции не соответствует текущему интерфейсу. Что сделать?',
         ['Проверить шаги и сообщить владельцу о расхождении'],
         ['Молча считать всю инструкцию правильной', 'Удалить все документы раздела']),
        ('Как проверить актуальность инструкции? Выберите все подходящие действия.',
         ['Сверить описанные шаги с действующим процессом', 'Проверить дату пересмотра и ответственного владельца'],
         ['Оценить только красивое оформление']),
    ],
    'COLLAB': [
        ('Как предложить исправление документа коллеге, который отвечает за него?',
         ['Оставить конкретный комментарий с причиной и предложенным изменением'],
         ['Переписать документ без обсуждения спорных мест', 'Отправить замечание без ссылки и контекста']),
        ('Что помогает совместной подготовке инструкции? Выберите все подходящие действия.',
         ['Согласовать ответственного и порядок проверки', 'Фиксировать обсуждаемые изменения и решения'],
         ['Одновременно править разные копии без сверки']),
    ],
    'ACCESS': [
        ('Какой доступ следует выдать сотруднику для выполнения конкретной задачи?',
         ['Минимально необходимый доступ по согласованной заявке'],
         ['Права администратора на всякий случай', 'Доступ через чужую учётную запись']),
        ('Что необходимо сделать при изменении обязанностей сотрудника? Выберите все подходящие действия.',
         ['Пересмотреть необходимость ранее выданных прав', 'Согласовать новые права с владельцем ресурса'],
         ['Сохранить все старые права навсегда']),
    ],
    'INCIDENT': [
        ('Вы обнаружили, что ссылка открывает конфиденциальный документ посторонним. Как поступить?',
         ['Сообщить ответственным по установленному каналу и ограничить распространение ссылки'],
         ['Опубликовать ссылку в общем чате для проверки', 'Игнорировать проблему до следующего месяца']),
        ('Какие сведения полезны при сообщении об инциденте? Выберите все подходящие пункты.',
         ['Время обнаружения и затронутый ресурс', 'Наблюдаемые признаки и уже выполненные действия'],
         ['Действующие пароли пользователей']),
    ],
}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def save(path, value, exclusive=False):
    """Atomic private JSON; first creation must never overwrite prior credentials."""
    raw = encoded(value) + b'\n'
    if exclusive:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    else:
        tmp = path.with_suffix(path.suffix + '.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)


def private_json(path):
    if path.is_symlink() or (os.name != 'nt' and path.stat().st_mode & 0o077):
        raise RuntimeError('Credential file must be private and not a symlink')
    return json.loads(path.read_text())


class Client:
    def __init__(self, login, password):
        self.csrf = None
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl._create_unverified_context()),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = self.call('POST', '/api/login', {'login': login, 'password': password})['csrf']

    def call(self, method, path, data=None):
        # No transport retries, especially for POST/PUT. TLS exemption is dev-only.
        if data is not None:
            data = dict(data)
            if self.csrf:
                data['csrf'] = self.csrf
        request = urllib.request.Request(BASE + path, method=method,
            data=encoded(data) if data is not None else None,
            headers={'Content-Type': 'application/json'})
        with self.opener.open(request, timeout=30) as response:
            return json.load(response)


class Journal:
    def __init__(self, path, data):
        self.path, self.data = path, data

    def persist(self):
        save(self.path, self.data)

    def step(self, key, client, method, path, payload):
        fingerprint = digest([method, path, payload])
        prior = self.data['steps'].get(key)
        if prior:
            if prior['fingerprint'] != fingerprint:
                raise RuntimeError('Step payload changed: ' + key)
            if prior['state'] != 'done':
                raise RuntimeError('Ambiguous prior write; reconcile manually, never retry: ' + key)
            return prior['response']
        self.data['steps'][key] = {'state': 'pending', 'method': method, 'path': path,
                                   'fingerprint': fingerprint}
        self.persist()  # Durable intent BEFORE the potentially ambiguous network write.
        result = client.call(method, path, payload)
        self.data['steps'][key].update(state='done', response=result)
        self.persist()
        return result


class Database:
    def __init__(self):
        self.command = ['docker', 'compose', '--env-file', '.env', '-f',
                        'deploy/poc/compose.yaml', '-p', 'kb_kk_poc', 'exec', '-T', 'db']

    def query(self, sql):
        # Explicit read-only transaction, including for all verification queries.
        result = subprocess.run(self.command + ['psql', '-X', '-qAt', '-U', 'kb_poc',
            '-d', 'kb_poc', '-v', 'ON_ERROR_STOP=1'], cwd=ROOT,
            input='BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;\n' + sql + '\nCOMMIT;',
            text=True, capture_output=True, check=True)
        return json.loads(result.stdout)

    def snapshot(self):
        tables = self.query("SELECT json_agg(tablename ORDER BY tablename) FROM pg_tables WHERE schemaname='public';")
        if any(not re.fullmatch('[a-z_]+', table) for table in tables):
            raise RuntimeError('Unexpected table identifier')
        # One transaction / consistent MVCC snapshot for every public table.
        parts = ["SELECT '" + table + "' AS name, COALESCE(jsonb_agg(to_jsonb(t)), '[]'::jsonb) AS rows FROM public.\"" + table + '\" t' for table in tables]
        return self.query('SELECT jsonb_object_agg(name, rows) FROM (' + ' UNION ALL '.join(parts) + ') s;')

    def backup(self, path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            subprocess.run(self.command + ['pg_dump', '-U', 'kb_poc', '-d', 'kb_poc', '-Fc'],
                           cwd=ROOT, stdout=stream, check=True)
            stream.flush()
            os.fsync(stream.fileno())
        return hashlib.sha256(path.read_bytes()).hexdigest()


def checksums(snapshot):
    return {table: {'rows': len(rows), 'sha256': digest(sorted(digest(row) for row in rows))}
            for table, rows in snapshot.items()}


def preserved(before, after, archived_id):
    """Every old business row must remain, byte-equivalent in canonical JSON.

    The ONLY business exception is the approved map's normal archival flags.
    Auth sessions/rate windows are captured but excluded as necessarily volatile.
    """
    report = {}
    for table, rows in before.items():
        if table in EPHEMERAL:
            continue
        expected = []
        for original in rows:
            row = dict(original)
            if table == 'competency_profiles' and row['id'] == archived_id:
                row.update(status='archived', is_active=False)
            expected.append(digest(row))
        missing = Counter(expected) - Counter(digest(row) for row in after.get(table, []))
        if missing:
            raise RuntimeError('Existing records changed or disappeared: ' + table)
        report[table] = {'old_rows_preserved': len(rows), 'added_rows': len(after[table]) - len(rows)}
    return report


def question_payload(cid, item):
    body, correct, wrong = item
    return {'competency_id': cid, 'body': body, 'type': 'single' if len(correct) == 1 else 'multiple',
            'options': [{'body': value, 'correct': True} for value in correct] +
                       [{'body': value, 'correct': False} for value in wrong]}


def preflight(admin, kk, manager, before, suffix):
    logins = HUMANS + ['smoke_employee_' + suffix]
    if any(row['username'] in logins for row in before['users']):
        raise RuntimeError('Requested new login already exists; never change its password')
    codes = {'DEMO_' + suffix.upper() + '_' + code for code in COMPETENCIES}
    if any(row['code'] in codes for row in before['competencies']):
        raise RuntimeError('Competency code collision')
    department = [d for d in admin.call('GET', '/api/competencies/departments') if d['code'] == 'DEMO']
    if len(department) != 1:
        raise RuntimeError('Expected exactly one existing DEMO department')
    did = department[0]['id']
    # These routes verify real departmental appointments, not just role labels.
    profiles = kk.call('GET', f'/api/competencies/departments/{did}/profiles')
    active = manager.call('GET', f'/api/competencies/departments/{did}/active')
    old = [p for p in profiles if p['id'] == active['id'] and p['is_active'] and p['status'] == 'approved']
    if len(old) != 1 or old[0]['version'] != active['version']:
        raise RuntimeError('Expected an approved active map')
    catalog = admin.call('GET', '/api/competencies/catalog')
    categories = [c for c in catalog['categories'] if c['code'] == 'DEMO']
    if len(categories) != 1:
        raise RuntimeError('Expected active DEMO category')
    active_ids = {c['id'] for c in catalog['competencies']}
    if not all(c['competency_id'] in active_ids for c in old[0]['competencies']):
        raise RuntimeError('Cannot faithfully copy old map: inactive competency')
    manager.call('GET', f'/api/competencies/departments/{did}/members')
    kk.call('GET', '/api/testing/questions')
    manager.call('GET', '/api/testing/tests')
    return {'department': did, 'category': categories[0]['id'], 'old_profile': old[0],
            'next_version': max(p['version'] for p in profiles if p['name'] == old[0]['name']) + 1}


def seed(journal, credentials, admin, kk, manager):
    m = journal.data
    p, suffix = m['preflight'], m['suffix']
    step = journal.step
    users, comps, tests, assignments = {}, {}, {}, {}
    question_ids = {code: [] for code in QUESTIONS}
    for login in HUMANS + ['smoke_employee_' + suffix]:
        users[login] = step('user:' + login, admin, 'POST', '/api/admin/users', {
            'login': login, 'email': login + '@example.invalid', 'full_name': 'Демо сотрудник ' + login,
            'password': credentials[login], 'department_id': p['department'], 'roles': ['employee']})['id']
    for code, name in COMPETENCIES.items():
        comps[code] = step('competency:' + code, admin, 'POST', '/api/competencies/catalog', {
            'category_id': p['category'], 'code': 'DEMO_' + suffix.upper() + '_' + code, 'name': name})['id']
    profile = step('profile:create', kk, 'POST', f"/api/competencies/departments/{p['department']}/profiles",
                   {'name': p['old_profile']['name']})
    if profile['version'] != p['next_version']:
        raise RuntimeError('Unexpected new map version; stop without approval')
    pid = profile['id']
    composition = {c['competency_id']: c['target_level'] for c in p['old_profile']['competencies']}
    composition.update({cid: 2 for cid in comps.values()})
    for cid, target in composition.items():
        step('profile:competency:' + cid, kk, 'PUT', f'/api/competencies/profiles/{pid}/competencies',
             {'competency_id': cid, 'target_level': target})
    step('profile:submit', kk, 'POST', f'/api/competencies/profiles/{pid}/submit', {})
    # Never refresh these optimistic-lock expectations or retry approval conflicts.
    step('profile:approve', manager, 'POST', f'/api/competencies/profiles/{pid}/review', {
        'decision': 'approve', 'expected_active_id': p['old_profile']['id'],
        'expected_active_version': p['old_profile']['version']})
    for code, items in QUESTIONS.items():
        for index, item in enumerate(items):
            key = f'question:{code}:{index}'
            qid = step(key, kk, 'POST', '/api/testing/questions', question_payload(comps[code], item))['id']
            step(key + ':review', kk, 'POST', f'/api/testing/questions/{qid}/review', {'decision': 'approve'})
            question_ids[code].append(qid)
    for code, (title, keys) in TESTS.items():
        blocks = [{'title': title, 'competencies': [
            {'competency_id': comps[key], 'question_ids': question_ids[key]} for key in keys]}]
        tid = step('test:' + code, kk, 'POST', '/api/testing/tests', {
            'title': code + ' — ' + title, 'blocks': blocks})['id']
        tokens = m.setdefault('publish_tokens', {})
        if code not in tokens:
            tokens[code] = kk.call('GET', f'/api/testing/tests/{tid}/draft')['expected_token']
            journal.persist()
        step('test:' + code + ':publish', kk, 'POST', f'/api/testing/tests/{tid}/publish', {
            'expected_token': tokens[code]})
        tests[code] = tid
    for login, uid in users.items():
        assignments[login] = {}
        for code in ('ABC' if login in HUMANS else 'A'):
            assignments[login][code] = step('assignment:' + login + ':' + code, manager,
                'POST', '/api/testing/assignments', {'test_id': tests[code], 'user_id': uid,
                                                   'due_at': m['due_at']})['id']
    m['ids'] = {'users': users, 'competencies': comps, 'profile': pid, 'tests': tests,
                'assignments': assignments}
    journal.persist()


def verify(journal, manager, kk, database, directory):
    m = journal.data
    ids, p = m['ids'], m['preflight']
    profiles = kk.call('GET', f"/api/competencies/departments/{p['department']}/profiles")
    current = next(row for row in profiles if row['id'] == ids['profile'])
    expected = {c['competency_id']: c['target_level'] for c in p['old_profile']['competencies']}
    expected.update({cid: 2 for cid in ids['competencies'].values()})
    assert current['is_active'] and current['status'] == 'approved'
    assert current['name'] == p['old_profile']['name'] and current['version'] == p['next_version']
    assert {c['competency_id']: c['target_level'] for c in current['competencies']} == expected
    results = {}
    for login, uid in ids['users'].items():
        rows = manager.call('GET', '/api/testing/results?user_id=' + uid)
        assert len(rows) == (3 if login in HUMANS else 1)
        assert {r['assignment_id'] for r in rows} == set(ids['assignments'][login].values())
        assert all(r['status'] == 'assigned' and r['attempt_id'] is None for r in rows)
        assert all(datetime.fromisoformat(r['due_at'].replace('Z', '+00:00')) >
                   datetime.now(timezone.utc) + timedelta(days=14) for r in rows)
        results[login] = rows
    published = {r['id']: r for r in kk.call('GET', '/api/testing/tests')}
    for code, tid in ids['tests'].items():
        assert published[tid]['status'] == 'published' and published[tid]['version'] == 1
        assert {c['id'] for c in published[tid]['competencies']} == {
            ids['competencies'][key] for key in TESTS[code][1]}
    before = private_json(directory / 'before.json')
    after = database.snapshot()
    preservation = preserved(before, after, p['old_profile']['id'])
    expected_additions = {'users': 4, 'competencies': 6, 'competency_profiles': 1,
                          'questions': 12, 'tests': 3, 'test_versions': 3, 'test_assignments': 10,
                          'test_attempts': 0, 'assessment_cooldowns': 0,
                          'test_competency_results': 0, 'employee_competencies': 0}
    for table, count in expected_additions.items():
        assert preservation[table]['added_rows'] == count, table
    for uid in ids['users'].values():
        user = next(r for r in after['users'] if r['id'] == uid)
        assert user['department_id'] == p['department'] and user['is_active']
        role_ids = {r['role_id'] for r in after['user_roles'] if r['user_id'] == uid}
        assert {r['code'] for r in after['roles'] if r['id'] in role_ids} == {'employee'}
    for cid in ids['competencies'].values():
        assert next(r for r in after['competencies'] if r['id'] == cid)['is_active']
        questions = [r for r in after['questions'] if r['competency_id'] == cid]
        assert len(questions) == 2 and all(r['moderation_status'] == 'approved' and r['is_active'] for r in questions)
    report = {'verified_at': datetime.now(timezone.utc).isoformat(), 'preservation': preservation,
              'excluded_volatile_tables': sorted(EPHEMERAL), 'checksums_after': checksums(after),
              'assignments': results, 'map_competencies': len(expected), 'ids': ids}
    save(directory / 'after.json', after)
    save(directory / 'verification.json', report)
    m['status'] = 'verified'
    journal.persist()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suffix', required=True, help='Unique lowercase alphanumeric cohort suffix (6-24 chars)')
    args = parser.parse_args()
    if not re.fullmatch('[a-z0-9]{6,24}', args.suffix):
        parser.error('suffix must have 6-24 lowercase alphanumeric characters')
    if Path.cwd().resolve() != ROOT or not (ROOT / '.owner').is_file():
        raise RuntimeError('Run only from an explicitly marked disposable local deployment')
    os.umask(0o077)
    directory = ROOT / ('demo-extension-' + args.suffix)
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_mode & 0o077:
        raise RuntimeError('Unsafe state directory permissions')
    # Single process per cohort; a crash deliberately requires manual lock inspection.
    lock = directory / 'running.lock'
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    clients = []
    try:
        db = Database()
        manifest_path = directory / 'manifest.json'
        if manifest_path.exists():
            manifest = private_json(manifest_path)
            if any(s['state'] != 'done' for s in manifest['steps'].values()):
                raise RuntimeError('Pending mutation: reconcile manually; no automatic POST retry')
        else:
            backup_sha = db.backup(directory / 'before.dump')
            before = db.snapshot()
            save(directory / 'before.json', before, exclusive=True)
            manifest = {'suffix': args.suffix, 'status': 'prepared', 'steps': {},
                        'due_at': (datetime.now(timezone.utc) + timedelta(days=21)).isoformat(),
                        'backup_sha256': backup_sha, 'checksums_before': checksums(before)}
            save(manifest_path, manifest, exclusive=True)
        journal = Journal(manifest_path, manifest)
        old_credentials = private_json(ROOT / 'demo-credentials.json')
        for login in ('demo_admin', 'demo_kk', 'demo_manager'):
            clients.append(Client(login, old_credentials[login]))
        admin, kk, manager = clients
        if 'preflight' not in manifest:
            manifest['preflight'] = preflight(admin, kk, manager, private_json(directory / 'before.json'), args.suffix)
            journal.persist()
        credential_path = directory / 'new-credentials.json'
        if not credential_path.exists():
            if manifest['steps']:
                raise RuntimeError('Missing cohort credentials; never regenerate existing user passwords')
            save(credential_path, {login: secrets.token_urlsafe(18) for login in
                HUMANS + ['smoke_employee_' + args.suffix]}, exclusive=True)
        new_credentials = private_json(credential_path)
        if manifest['status'] != 'verified':
            seed(journal, new_credentials, admin, kk, manager)
        report = verify(journal, manager, kk, db, directory)
        print(json.dumps({'status': 'verified', 'directory': str(directory),
            'new_users': 4, 'new_competencies': 6, 'new_questions': 12, 'published_tests': 3,
            'unstarted_assignments': 10, 'map_competencies': report['map_competencies']}, ensure_ascii=False))
    finally:
        for client in clients:
            try:
                client.call('POST', '/api/logout', {})
            except Exception:
                pass  # No write retry; sessions naturally expire if logout is unavailable.
        lock.unlink()


if __name__ == '__main__':
    main()
