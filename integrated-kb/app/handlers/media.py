import os
from .. import db, rbac
from ..router import Response
from ..utils import json_response

CHUNK_SIZE = 1024 * 1024 # 1 МБ

def register(r):
    r.route("POST", "/api/media/upload")(upload)
    r.route("GET", "/api/media/<mid>")(stream)

def upload(req):
    # Проверяем только авторизацию, без CSRF (так как это файл)
    if not req.user:
        return json_response({"error": "Не авторизован"}, "401 Unauthorized")

    # Читаем данные из потока напрямую
    length = int(req.environ.get('CONTENT_LENGTH', 0))
    if length == 0:
        return json_response({"error": "Файл пуст"}, "400 Bad Request")

    # Читаем весь поток (для теста, если видео будет > 500 МБ, нужно будет чанками)
    body = req.environ['wsgi.input'].read(length)

    # Извлекаем файл из multipart вручную (упрощенно)
    boundary = req.environ['CONTENT_TYPE'].split('boundary=')[1].encode()
    parts = body.split(b'--' + boundary)
    
    file_data = None
    filename = "video.mp4"
    
    for part in parts:
        if b'Content-Disposition' in part and b'filename=' in part:
            header_end = part.find(b'\r\n\r\n')
            if header_end != -1:
                file_data = part[header_end+4:]
                if file_data.endswith(b'\r\n'):
                    file_data = file_data[:-2]
                # Достаем имя файла
                disp = part[:header_end].decode('utf-8', errors='ignore')
                for line in disp.split('\r\n'):
                    if 'filename=' in line:
                        filename = line.split('filename=')[1].strip('"')
                        break
                break

    if not file_data:
        return json_response({"error": "Файл не найден"}, "400 Bad Request")

    # Создаем Large Object
    lo_oid = db.query("SELECT lo_creat(-1) AS oid", one=True)["oid"]
    
    # Пишем в базу
    db.execute("SELECT lo_open(%s, 131072)", (lo_oid,))
    db.execute("SELECT lowrite(lo_open(%s, 131072), %s)", (lo_oid, file_data))
    db.execute("SELECT lo_close(lo_open(%s, 131072))", (lo_oid,))

    # Сохраняем в таблицу
    aid = db.query(
        "INSERT INTO attachments (filename, mime_type, byte_size, storage_key, lo_oid) "
        "VALUES (%s, 'video/mp4', %s, 'lo', %s) RETURNING id",
        (filename, len(file_data), lo_oid),
        one=True
    )["id"]

    return json_response({"id": aid, "url": f"/api/media/{aid}"})

def stream(req, mid):
    row = db.query("SELECT lo_oid FROM attachments WHERE id=%s::uuid", (mid,), one=True)
    if not row or not row["lo_oid"]:
        return Response("Not Found", "404 Not Found")
    
    lo_oid = row["lo_oid"]
    size_row = db.query("SELECT lo_lseek(lo_open(%s, 262144), 0, 2) AS size", (lo_oid,), one=True)
    total_size = size_row["size"] if size_row else 0

    range_header = req.environ.get("HTTP_RANGE")
    start, end = 0, total_size - 1

    if range_header and range_header.startswith("bytes="):
        parts = range_header.replace("bytes=", "").split("-")
        start = int(parts[0]) if parts[0] else 0
        if parts[1]:
            end = int(parts[1])
        if end >= total_size:
            end = total_size - 1

    length = end - start + 1
    db.execute("SELECT lo_open(%s, 262144)", (lo_oid,))
    db.execute("SELECT lo_lseek(lo_open(%s, 262144), %s, 0)", (lo_oid, start))

    def generate():
        remaining = length
        while remaining > 0:
            read_size = min(CHUNK_SIZE, remaining)
            chunk = db.query("SELECT loread(lo_open(%s, 262144), %s) AS data", (lo_oid, read_size), one=True)
            if not chunk or not chunk["data"]:
                break
            yield bytes(chunk["data"])
            remaining -= len(chunk["data"])
        db.execute("SELECT lo_close(lo_open(%s, 262144))", (lo_oid,))

    headers = [("Content-Type", "video/mp4"), ("Accept-Ranges", "bytes"), ("Content-Length", str(length))]
    if range_header:
        headers.append(("Content-Range", f"bytes {start}-{end}/{total_size}"))
        return Response(generate(), "206 Partial Content", "video/mp4", headers)
    else:
        return Response(generate(), "200 OK", "video/mp4", headers)
