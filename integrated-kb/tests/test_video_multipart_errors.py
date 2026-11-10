"""Negative tests for multipart video uploads."""

import io
import os
import tempfile
from pathlib import Path

import app.video_multipart as video


BOUNDARY = b"kbkk-negative-test"
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32


def part(filename, payload):
    return (
        b"--" + BOUNDARY + b"\r\n"
        + b'Content-Disposition: form-data; name="video"; filename="'
        + filename.encode()
        + b'"\r\n'
        + b"Content-Type: video/mp4\r\n\r\n"
        + payload
        + b"\r\n"
    )


def request(body, declared_length=None):
    return {
        "CONTENT_TYPE": (
            "multipart/form-data; boundary=kbkk-negative-test"
        ),
        "CONTENT_LENGTH": str(
            len(body) if declared_length is None else declared_length
        ),
        "wsgi.input": io.BytesIO(body),
    }


def check_rejected(name, environ, directory):
    before = set(Path(directory).iterdir())

    try:
        video.parse_video_upload(environ)
    except video.VideoUploadError as error:
        print(f"PASS: {name}: {error}")
    except Exception as error:
        print(f"FAIL: {name}: unexpected {type(error).__name__}: {error}")
        raise
    else:
        raise AssertionError(f"FAIL: {name}: request accepted")

    after = set(Path(directory).iterdir())
    assert before == after, f"Temporary files leaked: {after - before}"


with tempfile.TemporaryDirectory() as directory:
    video.VIDEO_DIR = directory

    valid_body = part("test.mp4", MP4) + b"--" + BOUNDARY + b"--\r\n"

    check_rejected(
        "Truncated HTTP body",
        request(valid_body[:-10], len(valid_body)),
        directory,
    )

    check_rejected(
        "Two video files",
        request(
            part("first.mp4", MP4)
            + part("second.mp4", MP4)
            + b"--" + BOUNDARY + b"--\r\n"
        ),
        directory,
    )

    check_rejected(
        "Oversized request length",
        request(
            valid_body,
            video.MAX_VIDEO_SIZE + 1024 * 1024 + 1,
        ),
        directory,
    )

    check_rejected(
        "Invalid field name",
        request(
            valid_body.replace(
                b'name="video"',
                b'name="other"',
                1,
            )
        ),
        directory,
    )

    check_rejected(
        "Excessive header value",
        request(
            valid_body.replace(
                b'filename="test.mp4"',
                b'filename="' + b"a" * 5000 + b'.mp4"',
                1,
            )
        ),
        directory,
    )

    # A request ending without the final multipart boundary
    # must also be rejected.
    check_rejected(
        "Missing closing boundary",
        request(part("test.mp4", MP4)),
        directory,
    )

print("ALL NEGATIVE TESTS PASSED")
