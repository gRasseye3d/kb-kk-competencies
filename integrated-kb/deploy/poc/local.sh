#!/bin/sh
# Explicit local actions only. No automatic migration, deployment or publication.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$ROOT"
compose() {
    docker compose --env-file .env -f deploy/poc/compose.yaml -p kb_kk_poc "$@"
}
case "${1:-help}" in
    prepare)
        command -v openssl >/dev/null
        command -v python3 >/dev/null
        if [ -e .env ] || [ -e tls ]; then
            echo 'Refusing to overwrite .env or tls; inspect existing local state.' >&2
            exit 1
        fi
        umask 077
        python3 - <<'PY'
import secrets
with open('.env', 'x') as stream:
    stream.write('DB_PASSWORD=' + secrets.token_hex(32) + '\n')
PY
        mkdir tls
        openssl req -x509 -newkey rsa:2048 -nodes -days 30 \
            -keyout tls/key.pem -out tls/cert.pem -subj /CN=localhost \
            -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1'
        ;;
    build) compose build app ;;
    schema)
        # Only a disposable fresh database. Stop on any SQL error.
        compose up -d --wait db
        for sql in sql/schema_conflunce_v4.sql sql/03_sessions_v4.sql \
            sql/04_testing.sql sql/05_testing_review.sql sql/06_login_rate_limits.sql \
            sql/07_video_uploads.sql sql/08_video_size_500mb.sql sql/09_question_imports.sql; do
            compose exec -T db psql -X -U kb_poc -d kb_poc -v ON_ERROR_STOP=1 < "$sql"
        done
        ;;
    admin)
        # Credentials enter via stdin; not command arguments or logs.
        python3 - <<'PY' | compose run --rm -T --no-deps app python -m deploy.poc.bootstrap
import getpass
import json
password = getpass.getpass('New local demo_admin password (12+ characters): ')
if len(password) < 12:
    raise SystemExit('Password too short; no credentials emitted')
print(json.dumps({'demo_admin': password}))
PY
        ;;
    start) compose up -d ;;
    stop) compose down ;;
    *) echo 'Usage: sh deploy/poc/local.sh {prepare|build|schema|admin|start|stop}' ;;
esac
