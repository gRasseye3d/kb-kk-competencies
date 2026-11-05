# Интеграция в полный хост

## Контракт

Совместимый хост: полный снимок `kb-kk`, идентифицируемый **хешами shared-файлов**
в `component-manifest.json`, а не произвольная версия исходного kb.
Ожидаемое расположение: соседние `kb/` и `docs/` в корне полного репозитория.
Python 3.11/3.12, зависимости **из `kb/requirements.txt` хоста**, Node.js 18+.

Обязательны `db`, `access`, `audit`, `utils`, `auth`, `router`, `wsgi`, регистрация
handlers, административный API отделов/ролей, общие frontend app/index/style и
auth/admin views. WSGI импортирует также другие handlers, поэтому manifest
фиксирует общий Python-код и frontend хоста целиком без дублирования их исходников.
Не заменяйте зависимости заглушками и не создавайте второй механизм входа.

Хост должен регистрировать оба handler в `kb/app/handlers/__init__.py`, подключать
views и CSS в frontend и сохранять единый контракт сессий, CSRF, ролей,
назначений в отделах, HTTP 409/428. Совместимый снимок это уже делает. Если hashes
не совпали, остановитесь и вручную проверьте изменения API/схемы/прав доступа —
верификатор не выполняет принудительную установку и не обновляет manifest.

Сначала полная исходная v4 схема в **новой пустой PostgreSQL 15+ БД**, затем
миграции 03 → 04 → 05 → 06. Ни схема, ни SQL здесь не запускаются автоматически;
копирование файлов не является миграцией действующей БД. Tables пользователей,
ролей, отделов, назначения, каталог/карты и аудит принадлежат общему хосту.

## Безопасный локальный overlay

Из корня **этого компонента**, при полном экспорте в соседнем `../kb-kk`:

```sh
python3 tools/verify_manifest.py --host ../kb-kk
python3 - <<'PY'
import json
from pathlib import Path
import shutil
import tempfile
component = Path.cwd()
host = (component.parent / 'kb-kk').resolve()
base = Path('/tmp/opencode')
base.mkdir(parents=True, exist_ok=True)
work = Path(tempfile.mkdtemp(prefix='kb-overlay-', dir=base)) / 'host'
shutil.copytree(host, work, ignore=shutil.ignore_patterns(
    '.git', '.env', '.env.*', '.venv', 'venv', '__pycache__', '*.pyc',
    'node_modules', 'tls', '*credentials*', '*secrets*', '.owner',
    'demo-extension-*', '*.dump', '*.log', 'uploads', 'videos'))
manifest = json.loads((component / 'component-manifest.json').read_text())
for relative in manifest['component_files']:
    target = work / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(component / relative, target)
print(work)
PY
```

Команда выводит новый временный путь. Исходный хост и компонент не меняются.
Укажите этот путь как `WORK` и свою тестовую venv как `PYTHON` (либо создайте её
по инструкции полного хоста). Все дальнейшие команды выполняют только тесты:

```sh
WORK=/tmp/opencode/kb-overlay-REPLACE/host
PYTHON=/tmp/opencode/kb-test-venv/bin/python
cd "$WORK/kb"
env -u DATABASE_URL PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m unittest discover -s tests -v
node --experimental-vm-modules --test tests/*.test.mjs
env -u DATABASE_URL PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m unittest discover -s deploy/poc -p 'test_*.py' -v
```

`test_competencies.py`, `test_testing.py`, `test_admin_department_projection.py`
импортируют реальный хост. `frontend.test.mjs` проверяет также admin/router/auth
и **тоже требует полный хост**, не только две views модуля. Вторая frontend suite
`moderation.frontend.test.mjs` остаётся в хосте; при overlay запускаются обе.
PostgreSQL safety guards работают без БД, но API integration по умолчанию skipped.
Для отдельного настоящего DB-теста см. заголовок `test_pg_testing_integration.py`:
только новая пустая локальная `kb_it_*` БД. В проверках упаковки она не создавалась.

Manifest не хеширует себя и редакционные README: `component_files` — точный
allowlist overlay, `compatible_host_shared_files` — только проверки совместимости.
Ни одна запись shared не копируется из этого репозитория. Для новой согласованной
версии manifest обновляется только после ручной проверки и повторного тестирования.
