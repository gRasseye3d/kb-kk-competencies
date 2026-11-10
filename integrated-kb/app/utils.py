import json
from datetime import date, datetime

from .router import Response


class ConflictError(Exception):
    """State changed since the client read it."""

    def __init__(self, message, *, code=None, details=None):
        super().__init__(message)
        self.code = code
        self.details = details


class PreconditionRequired(Exception):
    """A conditional write omitted its expected state."""

def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def json_response(obj, status="200 OK", headers=None):
    return Response(
        json.dumps(obj, ensure_ascii=False, allow_nan=False, default=_json_default),
        status,
        "application/json; charset=utf-8",
        headers=headers,
    )
