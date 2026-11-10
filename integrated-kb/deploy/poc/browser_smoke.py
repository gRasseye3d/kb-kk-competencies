"""Playwright smoke; credentials JSON on stdin. Testing data is read-only by default.

Optional --exercise-user smoke_employee_<unique_suffix> consumes one assignment of
an already provisioned, fresh smoke account. This script never creates/reseeds users
and never starts the main demo employee's tests. Login/logout still create/revoke
sessions in both modes.
"""
import argparse
import json
import sys
from urllib.parse import parse_qs, urlparse


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', help='Existing demo URL; no deployment is performed')
    parser.add_argument('--exercise-user', metavar='smoke_employee_UNIQUE',
                        help='Explicitly start/submit the fresh dedicated account assignment')
    parser.add_argument('--cookie-name', default='kb_kk_poc_sid')
    args = parser.parse_args(argv)
    if args.exercise_user and (not args.exercise_user.startswith('smoke_employee_') or
                              not args.exercise_user.removeprefix('smoke_employee_')):
        parser.error('--exercise-user must be a fresh dedicated smoke_employee_<unique_suffix> account')
    args.base = args.base.rstrip('/')
    return args


def fresh_assignment(rows):
    if len(rows) != 1 or rows[0]['status'] != 'assigned' or rows[0].get('attempt_id') is not None:
        raise ValueError('Exercise requires one untouched assignment on a fresh smoke account; '
                         'do not reset existing accounts. Default mode is read-only.')
    return rows[0]


def exercise(page, context, base, rows, user, expect):
    """Only called after explicit opt-in and validation of the dedicated login."""
    fresh_assignment(rows)
    page.get_by_role('button', name='Начать тест', exact=True).click()
    expect(page.get_by_role('button', name='Отправить ответы', exact=True)).to_be_visible()
    attempt_id = parse_qs(urlparse(page.url).fragment.partition('?')[2])['attempt'][0]
    view = context.request.get(base + '/api/testing/attempts/' + attempt_id).json()
    assert view['status'] == 'in_progress' and view['questions']
    assert all('correct' not in option and 'is_correct' not in option
               for question in view['questions'] for option in question['options'])
    expect(page.locator('fieldset')).to_have_count(len(view['questions']))
    for fieldset in page.locator('fieldset').all():
        fieldset.locator('input').first.check()
    with page.expect_response(lambda response: response.url.endswith('/submit')) as submitted:
        page.get_by_role('button', name='Отправить ответы', exact=True).click()
    assert submitted.value.status == 200
    scores = submitted.value.json()['results']
    assert len(scores) == sum(c['state'] == 'included' for c in view['competencies'])
    # First options are not assumed to be correct in an independently provisioned test.
    assert all(0 <= result['level'] <= 2 and 0 <= result['correct'] <= result['total'] for result in scores)
    expect(page.locator('#main')).to_contain_text('Завершён')
    replay = context.request.post(base + '/api/testing/attempts/' + attempt_id + '/submit',
                                  data={'csrf': user['csrf'], 'answers': {}})
    assert replay.status == 409
    print('PASS explicit smoke start -> answers -> submit -> Завершён; no answer keys; replay 409')


def run(args, credentials):
    # Lazy import allows option/safety tests without a browser dependency.
    from playwright.sync_api import sync_playwright, expect

    logins = ['demo_admin', 'demo_manager', 'demo_kk', 'demo_employee']
    if 'smoke_employee' in credentials:
        logins.append('smoke_employee')  # An already completed smoke account is fine in read-only mode.
    if args.exercise_user:
        logins.append(args.exercise_user)
    missing = [login for login in logins if login not in credentials]
    if missing:
        raise ValueError('Missing credentials for: ' + ', '.join(missing))
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for login in logins:
            context = browser.new_context(ignore_https_errors=True)
            try:
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(args.base + '/#/testing')
                page.locator('#login').fill(login)
                page.locator('#password').fill(credentials[login])
                page.get_by_role('button', name='Войти', exact=True).click()
                expect(page.get_by_role('heading', name='Мои тесты и результаты', exact=True)).to_be_visible()
                cookies = [c for c in context.cookies() if c['name'] == args.cookie_name]
                assert len(cookies) == 1 and cookies[0]['secure'] and cookies[0]['httpOnly'] and cookies[0]['sameSite'] == 'Lax'
                user = context.request.get(args.base + '/api/me').json()['user']
                assert user['login'] == login
                tree = context.request.get(args.base + '/api/tree').json()
                assert tree and all(row['status'] == 'published' for row in tree)
                assert context.request.get(args.base + '/api/pages/' + tree[0]['id']).status == 200
                print('PASS UI login, secure isolated session and published wiki:', login)
                before = context.request.get(args.base + '/api/testing/results').json()
                if login == 'demo_employee':
                    assert context.request.get(args.base + '/api/admin/users').status == 403
                    assert context.request.get(args.base + '/api/testing/questions').status == 403
                    print('PASS employee admin/bank denial (GET only)')
                if login == args.exercise_user:
                    exercise(page, context, args.base, before, user, expect)
                else:
                    after = context.request.get(args.base + '/api/testing/results').json()
                    assert after == before, 'Read-only smoke must not change assignments/results'
                    print('PASS testing read-only; assignments/results unchanged:', login)
                assert context.request.post(args.base + '/api/logout', data={'csrf': user['csrf']}).status == 200
                assert context.request.get(args.base + '/api/me').json()['user'] is None
                assert not [c for c in context.cookies() if c['name'] == args.cookie_name]
            finally:
                context.close()
        browser.close()
    assert not errors, errors
    print(f'PASS no JavaScript page errors; logout invalidated sessions for {len(logins)} accounts')


if __name__ == '__main__':
    run(parse_args(), json.load(sys.stdin))
