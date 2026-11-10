import json
from .. import db, rbac, audit
from ..utils import json_response

def register(r):
    r.route("GET", "/api/pages/<pid>/comments")(list_comments)
    r.route("POST", "/api/pages/<pid>/comments")(add_comment)
    r.route("DELETE", "/api/comments/<cid>")(delete_comment)

def _build_tree(rows):
    by_id = {row["id"]: {**row, "children": []} for row in rows}
    roots = []
    for row in by_id.values():
        if row["parent_id"] and row["parent_id"] in by_id:
            by_id[row["parent_id"]]["children"].append(row)
        else:
            roots.append(row)
    return roots

def list_comments(req, pid):
    # Достаем текст из JSONB поля body (извлекаем ключ 'text' или берем всё как строку)
    rows = db.query(
        """SELECT c.id, c.parent_id, 
                  COALESCE(c.body->>'text', c.body::text, '[пусто]') AS content, 
                  c.created_at, 
                  CASE WHEN c.deleted_at IS NOT NULL THEN TRUE ELSE FALSE END AS deleted,
                  u.display_name AS author, u.id AS author_id
           FROM comments c LEFT JOIN users u ON u.id=c.author_id
           WHERE c.page_id=%s::uuid ORDER BY c.created_at""",
        (pid,),
    )
    return json_response(_build_tree(rows))

def add_comment(req, pid):
    rbac.require(req.user, "reader", "author", "moderator", "admin", "employee", "god")
    data = req.json()
    if data.get("csrf") != req.user["csrf_token"]: raise PermissionError("CSRF")
    text = (data.get("content") or "").strip()
    if not text: return json_response({"error": "empty"}, "400 Bad Request")
    
    # Упаковываем текст в JSON для поля body
    body_json = json.dumps({"text": text})
    
    cid = db.query(
        "INSERT INTO comments(page_id, parent_id, author_id, body) VALUES(%s::uuid, %s::uuid, %s::uuid, %s::jsonb) RETURNING id",
        (pid, data.get("parent_id") or None, req.user["user_id"], body_json), one=True,
    )["id"]
    
    audit.log(req.user["user_id"], "comment.add", "comment", str(cid))
    return json_response({"id": cid})

def delete_comment(req, cid):
    if not req.user: raise PermissionError("Не авторизован")
    c = db.query("SELECT * FROM comments WHERE id=%s::uuid", (cid,), one=True)
    if not c: raise PermissionError("Нет комментария")
    if c["author_id"] != req.user["user_id"] and not rbac.has_role(req.user, "admin"):
        raise PermissionError("Нельзя удалять чужой комментарий")
    
    # Ставим дату удаления и меняем текст в JSONB
    db.execute("UPDATE comments SET deleted_at=now(), body=%s::jsonb WHERE id=%s::uuid", 
               (json.dumps({"text": "[удалён]"}), cid))
    
    audit.log(req.user["user_id"], "comment.delete", "comment", str(cid))
    return json_response({"ok": True})
