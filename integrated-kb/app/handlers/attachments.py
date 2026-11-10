import os
import uuid
import mimetypes
import hashlib
from .. import db, rbac, audit
from ..utils import json_response

# Папка для хранения видео и картинок
UPLOAD_DIR = '/opt/kb/static/videos'
os.makedirs(UPLOAD_DIR, exist_ok=True)

def register(r):
    r.route("POST", "/api/attachments/temp")(upload_temp)
    r.route("POST", "/api/pages/<pid>/attachments")(upload)
    r.route("GET", "/api/pages/<pid>/attachments")(list_att)

def _parse_multipart(environ):
    try:
        content_type = environ.get('CONTENT_TYPE', '')
        if 'boundary=' not in content_type:
            return None, None
        
        boundary = content_type.split('boundary=')[1].encode()
        length = int(environ.get('CONTENT_LENGTH', 0))
        body = environ['wsgi.input'].read(length)
        
        parts = body.split(b'--' + boundary)
        for part in parts:
            if b'Content-Disposition' in part and b'filename=' in part:
                header_end = part.find(b'\r\n\r\n')
                if header_end == -1:
                    continue
                
                file_data = part[header_end+4:]
                if file_data.endswith(b'\r\n'):
                    file_data = file_data[:-2]
                
                disp = part[:header_end].decode('utf-8', errors='ignore')
                filename = "video.mp4"
                for line in disp.split('\r\n'):
                    if 'filename=' in line:
                        filename = line.split('filename=')[1].strip('"')
                        break
                return filename, file_data
    except Exception as e:
        print("Multipart parse error:", e)
    return None, None

def _save_file(filename, file_data):
    """Сохраняет файл на диск и возвращает URL"""
    ext = os.path.splitext(filename)[1].lower()
    if not ext:
        ext = '.mp4' if 'video' in filename else '.bin'
    
    # Генерируем уникальное имя файла
    unique_name = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(UPLOAD_DIR, unique_name)
    
    with open(filepath, 'wb') as f:
        f.write(file_data)
    
    return f"/static/videos/{unique_name}"

def upload_temp(req):
    """Временная загрузка файла до сохранения статьи"""
    if not req.user: raise PermissionError("Не авторизован")
    
    filename, file_data = _parse_multipart(req.environ)
    if not filename or not file_data:
        return json_response({"error": "Файл не передан"}, "400 Bad Request")
    
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    byte_size = len(file_data)
    checksum = hashlib.sha256(file_data).hexdigest()
    
    # Сохраняем файл на диск
    url = _save_file(filename, file_data)
    
    # Пишем в базу (для истории), привязываем к скрытой странице
    temp_page = db.query("SELECT id FROM pages WHERE title = 'Временные файлы (скрыто)' LIMIT 1", one=True)
    
    if not temp_page:
        space = db.query("SELECT id FROM spaces LIMIT 1", one=True)
        if not space:
            space_id = db.query("INSERT INTO spaces(name, space_key, visibility) VALUES('Общее', 'GEN', 'private') RETURNING id", one=True)["id"]
        else:
            space_id = space["id"]
            
        temp_page_id = db.query(
            "INSERT INTO pages(space_id, title, created_by, status) VALUES(%s::uuid, 'Временные файлы (скрыто)', %s::uuid, 'draft') RETURNING id",
            (space_id, req.user["user_id"]), one=True
        )["id"]
    else:
        temp_page_id = temp_page["id"]

    # Записываем только путь к файлу, а не Base64!
    db.query(
        """INSERT INTO attachments(page_id, filename, media_type, byte_size, storage_key, checksum_sha256, uploaded_by) 
           VALUES(%s::uuid, %s, %s, %s, %s, %s, %s::uuid) RETURNING id""",
        (temp_page_id, filename, mime, byte_size, url, checksum, req.user["user_id"]),
        one=True,
    )

    audit.log(req.user["user_id"], "attachment.upload_temp", "attachment", url)
    return json_response({"url": url})

def upload(req, pid):
    rbac.require(req.user, "author", "moderator", "admin")
    filename, file_data = _parse_multipart(req.environ)
    if not filename or not file_data:
        return json_response({"error": "Файл не передан"}, "400 Bad Request")
    
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    byte_size = len(file_data)
    checksum = hashlib.sha256(file_data).hexdigest()
    
    # Сохраняем файл на диск
    url = _save_file(filename, file_data)
    
    aid = db.query(
        """INSERT INTO attachments(page_id, filename, media_type, byte_size, storage_key, checksum_sha256, uploaded_by) 
           VALUES(%s::uuid, %s, %s, %s, %s, %s, %s::uuid) RETURNING id""",
        (pid, filename, mime, byte_size, url, checksum, req.user["user_id"]),
        one=True,
    )["id"]
    
    audit.log(req.user["user_id"], "attachment.add", "attachment", str(aid))
    return json_response({"id": aid, "url": url})

def list_att(req, pid):
    rows = db.query(
        "SELECT id, filename, media_type, byte_size, storage_key AS url FROM attachments WHERE page_id=%s::uuid",
        (pid,),
    )
    return json_response(rows)
