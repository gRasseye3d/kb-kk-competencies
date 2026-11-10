"""Verify multipart header count limit."""

import io
import tempfile
from pathlib import Path

import app.video_multipart as video


boundary = b"kbkk-header-count-test"

headers = (
    b'Content-Disposition: form-data; name="video"; filename="test.mp4"\r\n'
    b"Content-Type: video/mp4\r\n"
)

# Add 15 more headers: 17 in total.
for number in range(15):
    headers += f"X-Test-{number}: value\r\n".encode("ascii")

payload = (
    b"\x00\x00\x00\x18ftypisom"
    + b"\x00" * 32
)

body = (
    b"--" + boundary + b"\r\n"
    + headers
    + b"\r\n"
    + payload
    + b"\r\n--" + boundary + b"--\r\n"
)

environ = {
    "CONTENT_TYPE": (
        "multipart/form-data; boundary=kbkk-header-count-test"
    ),
    "CONTENT_LENGTH": str(len(body)),
    "wsgi.input": io.BytesIO(body),
}

with tempfile.TemporaryDirectory() as directory:
    video.VIDEO_DIR = directory

    try:
        video.parse_video_upload(environ)
    except video.VideoUploadError as error:
        assert str(error) == "Too many multipart headers", str(error)
        print("PASS: 17 multipart headers rejected")
    else:
        raise AssertionError("FAIL: 17 headers were accepted")

    assert not list(Path(directory).iterdir()), (
        "FAIL: temporary files remain"
    )

    print("PASS: no temporary files remain")

print("HEADER COUNT TEST PASSED")
