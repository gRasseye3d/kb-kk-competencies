from .. import db, access
from ..utils import json_response

def register(r):
    r.route("GET", "/api/labels")(autocomplete)

def autocomplete(req):
    if not req.user:
        return json_response([])
    q = (req.q("q") or "").strip()
    rows = db.query(
        """SELECT DISTINCT l.name,p.* FROM labels l JOIN page_labels pl ON pl.label_id=l.id
           JOIN pages p ON p.id=pl.page_id WHERE l.name ILIKE %s LIMIT 200""",
        (f"%{q[:100]}%",),
    )
    return json_response(sorted({r['name'] for r in rows if access.page(req.user, r)})[:20])
