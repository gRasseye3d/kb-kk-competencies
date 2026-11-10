"""Streaming multipart video upload parser."""

import os
import re
import uuid

from multipart.multipart import MultipartParser, parse_options_header


MAX_VIDEO_SIZE = 500 * 1024 * 1024
CHUNK_SIZE = 64 * 1024

VIDEO_DIR = "/srv/kb/uploads/videos"


def validate_video_signature(path, media_type):
    """Check basic MP4/WebM container signatures."""

    with open(path, "rb") as file:
        header = file.read(4096)

    if media_type == "video/mp4":
        valid = (
            len(header) >= 12
            and header[4:8] == b"ftyp"
            and header[8:12] != b"\x00\x00\x00\x00"
        )
    elif media_type == "video/webm":
        valid = (
            header.startswith(b"\x1a\x45\xdf\xa3")
            and b"webm" in header[:4096]
        )
    else:
        valid = False

    if not valid:
        raise VideoUploadError(
            "File does not match declared video format"
        )


class VideoUploadError(ValueError):
    pass


def parse_video_upload(environ):
    """Read one video field without buffering the whole file."""

    content_type = environ.get("CONTENT_TYPE", "")
    media_type, params = parse_options_header(
        content_type.encode("latin-1")
    )

    if media_type != b"multipart/form-data":
        raise VideoUploadError("multipart/form-data required")

    boundary = params.get(b"boundary")

    if not boundary:
        raise VideoUploadError("Missing multipart boundary")

    try:
        remaining = int(environ.get("CONTENT_LENGTH", ""))
    except (TypeError, ValueError):
        raise VideoUploadError("Content-Length required")

    if remaining <= 0:
        raise VideoUploadError("Empty request")

    if remaining > MAX_VIDEO_SIZE + 1024 * 1024:
        raise VideoUploadError("Request too large")

    os.makedirs(VIDEO_DIR, exist_ok=True)

    state = {
        "headers": {},
        "header_count": 0,
        "header_field": bytearray(),
        "header_value": bytearray(),
        "field": None,
        "filename": None,
        "media_type": None,
        "size": 0,
        "parts": 0,
        "completed": False,
        "multipart_ended": False,
    }

    video_id = uuid.uuid4()
    temp_name = f".{video_id.hex}.upload"
    temp_path = os.path.join(VIDEO_DIR, temp_name)

    output = None

    def on_part_begin():
        state["parts"] += 1

        if state["parts"] > 1:
            raise VideoUploadError("Only one video field allowed")

        state["headers"] = {}
        state["header_count"] = 0
        state["header_field"].clear()
        state["header_value"].clear()

    def on_header_field(data, start, end):
        chunk = data[start:end]

        if len(state["header_field"]) + len(chunk) > 100:
            raise VideoUploadError("Multipart header name too large")

        state["header_field"].extend(chunk)

    def on_header_value(data, start, end):
        chunk = data[start:end]

        if len(state["header_value"]) + len(chunk) > 4096:
            raise VideoUploadError("Multipart header value too large")

        state["header_value"].extend(chunk)

    def on_header_end():
        state["header_count"] += 1

        if state["header_count"] > 16:
            raise VideoUploadError("Too many multipart headers")

        field = bytes(state["header_field"]).lower()
        value = bytes(state["header_value"])

        if len(field) > 100 or len(value) > 4096:
            raise VideoUploadError("Multipart header too large")

        state["headers"][field] = value

        state["header_field"].clear()
        state["header_value"].clear()

    def on_headers_finished():
        nonlocal output

        disposition = state["headers"].get(
            b"content-disposition", b""
        )

        _, options = parse_options_header(disposition)

        if options.get(b"name") != b"video":
            raise VideoUploadError("Expected video field")

        filename = options.get(b"filename", b"").decode(
            "utf-8", errors="replace"
        )

        filename = os.path.basename(
            filename.replace("\\", "/")
        )

        if not filename or len(filename) > 255:
            raise VideoUploadError("Invalid filename")

        extension = os.path.splitext(filename)[1].lower()

        if extension not in (".mp4", ".webm"):
            raise VideoUploadError("Only MP4/WebM allowed")

        expected_type = (
            "video/mp4" if extension == ".mp4"
            else "video/webm"
        )

        declared_type = state["headers"].get(
            b"content-type", b""
        ).decode("ascii", errors="replace").lower()

        if declared_type != expected_type:
            raise VideoUploadError("Invalid video MIME type")

        state["filename"] = filename
        state["media_type"] = expected_type

        output = open(temp_path, "xb")

    def on_part_data(data, start, end):
        chunk = data[start:end]

        if output is None:
            raise VideoUploadError("Video file not initialized")

        state["size"] += len(chunk)

        if state["size"] > MAX_VIDEO_SIZE:
            raise VideoUploadError("Video exceeds 500 MiB")

        output.write(chunk)

    def on_part_end():
        state["completed"] = True

    def on_end():
        state["multipart_ended"] = True

    callbacks = {
        "on_part_begin": on_part_begin,
        "on_header_field": on_header_field,
        "on_header_value": on_header_value,
        "on_header_end": on_header_end,
        "on_headers_finished": on_headers_finished,
        "on_part_data": on_part_data,
        "on_part_end": on_part_end,
        "on_end": on_end,
    }

    try:
        parser = MultipartParser(boundary, callbacks)

        stream = environ["wsgi.input"]

        while remaining:
            chunk = stream.read(min(CHUNK_SIZE, remaining))

            if not chunk:
                raise VideoUploadError("Incomplete request")

            remaining -= len(chunk)
            parser.write(chunk)

        parser.finalize()

        if not state["multipart_ended"]:
            raise VideoUploadError("Incomplete multipart request")

        if not state["completed"] or state["size"] == 0:
            raise VideoUploadError("No video uploaded")

        if output:
            output.close()
            output = None

        validate_video_signature(
            temp_path, state["media_type"]
        )

        return {
            "id": str(video_id),
            "temp_path": temp_path,
            "filename": state["filename"],
            "media_type": state["media_type"],
            "byte_size": state["size"],
        }

    except BaseException:
        if output:
            output.close()

        if os.path.exists(temp_path):
            os.unlink(temp_path)

        raise
