"""Authenticated video uploads and streaming."""

import os
import stat
import uuid
from pathlib import Path

from .. import db, access
from ..video_multipart import (
    parse_video_upload,
    VideoUploadError,
    VIDEO_DIR,
)

from .video_hls import get_hls
from ..router import Response
from ..utils import json_response


MAX_VIDEO_SIZE = 500 * 1024 * 1024
MAX_REQUEST_SIZE = MAX_VIDEO_SIZE + 1024 * 1024


def register(router):
    router.route('POST', '/api/videos/upload')(upload_video)
    router.route('GET', '/api/videos/<vid>')(get_video)
    router.route('GET', '/api/videos/<vid>/hls/<filename>')(get_hls)


def require_user(req):
    if not req.user:
        raise PermissionError('Authentication required')
    return req.user


def require_upload_csrf(req):
    user = require_user(req)

    supplied = req.headers.get('x-csrf-token', '')

    if not supplied or supplied != user['csrf_token']:
        raise PermissionError('Invalid CSRF token')

    # Reject cross-origin requests when Origin is present.
    origin = req.headers.get('origin')
    if origin:
        from urllib.parse import urlsplit

        parsed = urlsplit(origin)
        host = req.headers.get('host', '')

        if (
            parsed.scheme != 'https'
            or parsed.netloc != host
        ):
            raise PermissionError('Cross-origin upload denied')

    return user


def upload_video(req):
    user = require_upload_csrf(req)

    content_type = req.environ.get("CONTENT_TYPE", "")

    if not content_type.lower().startswith("multipart/form-data;"):
        raise ValueError("multipart/form-data required")

    try:
        content_length = int(req.environ.get("CONTENT_LENGTH", ""))
    except (ValueError, TypeError):
        raise ValueError("Content-Length required")

    if content_length <= 0:
        raise ValueError("Empty upload")

    if content_length > MAX_REQUEST_SIZE:
        return json_response(
            {"error": "Upload exceeds 500 MiB limit"},
            "413 Content Too Large",
        )

    upload = parse_video_upload(req.environ)

    temp_path = Path(upload["temp_path"])
    extension = (
        ".mp4"
        if upload["media_type"] == "video/mp4"
        else ".webm"
    )

    storage_name = uuid.uuid4().hex + extension
    final_path = Path(VIDEO_DIR) / storage_name

    moved = False

    try:
        with db.transaction() as conn:
            if final_path.exists():
                raise FileExistsError("Video storage collision")

            os.link(temp_path, final_path)
            moved = True
            temp_path.unlink()

            db.execute(
                """
                INSERT INTO video_uploads (
                    id,
                    uploaded_by,
                    page_id,
                    filename,
                    storage_name,
                    media_type,
                    byte_size
                )
                VALUES (%s, %s, NULL, %s, %s, %s, %s)
                """,
                (
                    upload["id"],
                    user["user_id"],
                    upload["filename"],
                    storage_name,
                    upload["media_type"],
                    upload["byte_size"],
                ),
                conn=conn,
            )

    except BaseException:
        # Remove the uploaded file if database registration fails.
        if moved:
            final_path.unlink(missing_ok=True)

        temp_path.unlink(missing_ok=True)

        raise

    return json_response(
        {
            "id": upload["id"],
            "url": "/api/videos/" + upload["id"],
            "filename": upload["filename"],
            "media_type": upload["media_type"],
            "byte_size": upload["byte_size"],
        },
        "201 Created",
    )


def get_video(req, vid):
    user = require_user(req)

    try:
        video_id = str(uuid.UUID(vid))
    except (ValueError, TypeError):
        return json_response(
            {"error": "Video not found"},
            "404 Not Found",
        )

    video = db.query(
        """
        SELECT
            id,
            uploaded_by,
            page_id,
            storage_name,
            media_type,
            byte_size
        FROM video_uploads
        WHERE id = %s
        """,
        (video_id,),
        one=True,
    )

    if not video:
        return json_response(
            {"error": "Video not found"},
            "404 Not Found",
        )

    if video["page_id"] is None:
        # Unattached videos are accessible only to their uploader.
        if str(video["uploaded_by"]) != str(user["user_id"]):
            return json_response(
                {"error": "Video not found"},
                "404 Not Found",
            )

    else:
        page = db.query(
            "SELECT * FROM pages WHERE id = %s",
            (video["page_id"],),
            one=True,
        )

        if not page or not access.page(user, page):
            return json_response(
                {"error": "Video not found"},
                "404 Not Found",
            )

    storage_name = video["storage_name"]

    expected_extension = (
        ".mp4"
        if video["media_type"] == "video/mp4"
        else ".webm"
    )

    # Never trust a database filename as a filesystem path.
    if not isinstance(storage_name, str):
        return json_response(
            {"error": "Video not found"}, "404 Not Found"
        )

    if not (
        len(storage_name) == 32 + len(expected_extension)
        and storage_name.endswith(expected_extension)
        and all(c in "0123456789abcdef" for c in storage_name[:32])
    ):
        return json_response(
            {"error": "Video not found"}, "404 Not Found"
        )

    file_path = Path(VIDEO_DIR) / storage_name

    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW
        flags |= getattr(os, "O_NONBLOCK", 0)

        fd = os.open(file_path, flags)

        try:
            file_stat = os.fstat(fd)

            if not stat.S_ISREG(file_stat.st_mode):
                raise OSError("Video is not a regular file")

            file = os.fdopen(fd, "rb")
        except BaseException:
            os.close(fd)
            raise

    except OSError:
        return json_response(
            {"error": "Video not found"}, "404 Not Found"
        )

    try:
        size = os.fstat(file.fileno()).st_size

        if size <= 0 or size != video["byte_size"]:
            return json_response(
                {"error": "Video unavailable"},
                "404 Not Found",
            )

        # Authentication, page access, filename and file type
        # have already been validated.
        # Nginx handles the actual file transfer and Range requests.
        return Response(
            b"",
            "200 OK",
            content_type=video["media_type"],
            headers=[
                ("X-Accel-Redirect", "/_protected_videos/" + storage_name),
                ("Content-Disposition", "inline"),
                ("Cache-Control", "private, no-store"),
            ],
        )
    finally:
        file.close()
