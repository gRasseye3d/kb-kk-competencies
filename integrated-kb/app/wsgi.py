import json
import mimetypes
import os
import traceback

from .router import Router, Request, Response
from .utils import ConflictError, PreconditionRequired
from . import handlers

app_router = Router()
handlers.register(app_router)

STATIC_DIR = os.environ.get("STATIC_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'static')))

def serve_file(req):
    base = STATIC_DIR
    rel = req.path[len('/static/'):]
    if rel.split('/', 1)[0].lower() == 'videos':
        return Response('Not Found', '404 Not Found')
    path = os.path.realpath(os.path.join(base, rel))
    video_dir = os.path.realpath(os.path.join(base, 'videos'))
    if (path == video_dir or path.startswith(video_dir + os.sep)
            or not path.startswith(os.path.realpath(base) + os.sep) or not os.path.isfile(path)):
        return Response("Not Found", "404 Not Found")
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        return Response(f.read(), "200 OK", ctype)

def application(environ, start_response):
    try:
        req = Request(environ)
        if req.path.startswith("/static/"):
            resp = serve_file(req)
        elif req.path in ("/", "/index.html"):
            index_path = os.path.join(STATIC_DIR, "index.html")
            with open(index_path, "rb") as f:
                resp = Response(f.read(), "200 OK", "text/html; charset=utf-8")
        else:
            resp = app_router.dispatch(req)
    except ConflictError as e:
        payload = {'error': str(e)}
        # Only public conflict contracts may expose codes/details, never snapshots/keys.
        if e.code == 'due_missing':
            payload['code'] = e.code
        if e.code == 'assessment_unavailable':
            payload['code'] = e.code
            payload['details'] = {'competencies': [
                {key: row.get(key) for key in
                 ('competency_id', 'competency_name', 'reason', 'available_at')}
                for row in (e.details or {}).get('competencies', [])
                if row.get('reason') in ('cooldown_30d', 'reserved_active')]}
        resp = Response(json.dumps(payload, ensure_ascii=False),
                        '409 Conflict', 'application/json; charset=utf-8')
    except PreconditionRequired as e:
        resp = Response(json.dumps({'error': str(e)}, ensure_ascii=False),
                        '428 Precondition Required', 'application/json; charset=utf-8')
    except ValueError as e:
        resp = Response(json.dumps({'error': str(e)}, ensure_ascii=False),
                        '400 Bad Request', 'application/json; charset=utf-8')
    except PermissionError as e:
        resp = Response(
            json.dumps({"error": str(e)}, ensure_ascii=False),
            "403 Forbidden", "application/json; charset=utf-8",
        )
    except Exception:
        traceback.print_exc()
        resp = Response(
            json.dumps({"error": "internal"}),
            "500 Internal Server Error", "application/json",
        )
    if environ.get('PATH_INFO', '/').startswith('/api/'):
        resp.headers.extend([('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff')])
    start_response(resp.status, resp.headers)

    # WSGI requires an iterable yielding byte strings.
    # Ordinary responses contain bytes; streaming responses
    # provide an iterable that yields chunks of bytes.
    if isinstance(resp.body, bytes):
        return [resp.body]

    return resp.body
