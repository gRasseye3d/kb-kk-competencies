import json
import re
import urllib.parse

from .auth import current_user


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


class Request:
    def __init__(self, environ):
        self.environ = environ
        self.method = environ["REQUEST_METHOD"]
        self.path = environ.get("PATH_INFO", "/")
        self.query = urllib.parse.parse_qs(environ.get("QUERY_STRING", ""))
        self.headers = {
            k[5:].replace("_", "-").lower(): v
            for k, v in environ.items()
            if k.startswith("HTTP_")
        }
        self.cookies = {}
        for part in environ.get("HTTP_COOKIE", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k:
                self.cookies[k] = v
        self.user = current_user(environ)
        self._body = None

    @property
    def body(self):
        if self._body is None:
            length = int(self.environ.get("CONTENT_LENGTH") or 0)
            raw = self.environ["wsgi.input"].read(length) if length else b""
            ctype = self.environ.get("CONTENT_TYPE", "")
            if ctype.startswith("application/json") and raw:
                self._body = json.loads(raw, object_pairs_hook=unique_object)
            else:
                self._body = raw
        return self._body

    def json(self):
        body = self.body
        if not isinstance(body, bytes):
            return body
        return json.loads(body or b"{}", object_pairs_hook=unique_object)

    def q(self, name, default=None):
        v = self.query.get(name)
        return v[0] if v else default

class Response:
    def __init__(self, body=b"", status="200 OK",
                 content_type="text/html; charset=utf-8", headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.body = body
        self.status = status
        self.headers = [("Content-Type", content_type)]
        if headers:
            self.headers.extend(headers)

class Router:
    def __init__(self):
        self.routes = []

    def route(self, method, pattern):
        def deco(fn):
            regex = re.compile(
                "^" + re.sub(r"<(\w+)>", r"(?P<\1>[^/]+)", pattern) + "$"
            )
            self.routes.append((method, regex, fn))
            return fn
        return deco

    def dispatch(self, req):
        for method, regex, fn in self.routes:
            if method != req.method:
                continue
            m = regex.match(req.path)
            if m:
                kwargs = {k: urllib.parse.unquote(v)
                          for k, v in m.groupdict().items()}
                return fn(req, **kwargs)
        return Response("Not Found", "404 Not Found")
