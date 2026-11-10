import re

from .. import db, rbac, audit
from ..router import Response
from ..pdfgen import render_page_to_pdf

def register(r):
    r.route("GET", "/api/pages/<pid>/pdf")(export_pdf)

def _safe_filename(name):
    return re.sub(r"[^\w\-. ]+", "_", name)[:80] or "article"

def export_pdf(req, pid):
    if not req.user:
        raise PermissionError("Требуется вход")
    rbac.require(req.user, "author", "moderator", "admin")
    p = db.query("SELECT * FROM pages WHERE id=%s", (int(pid),), one=True)
    if not p:
        raise PermissionError("Нет страницы")
    if p["author_id"] != req.user["user_id"] \
       and not rbac.has_role(req.user, "moderator", "admin"):
        raise PermissionError("Нет доступа к экспорту")
    pdf_bytes = render_page_to_pdf(p["title"], p["content"])
    audit.log(req.user["user_id"], "page.export_pdf", "page", p["id"])
    fn = _safe_filename(p["title"]) + ".pdf"
    return Response(
        pdf_bytes, "200 OK", "application/pdf",
        headers=[("Content-Disposition",
                  f'attachment; filename="{fn}"')],
    )
