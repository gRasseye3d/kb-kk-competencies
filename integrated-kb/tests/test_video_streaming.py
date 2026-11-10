"""Isolated tests for authenticated X-Accel-Redirect video delivery."""

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app.handlers import videos

if not hasattr(os, 'O_NOFOLLOW'):
    raise unittest.SkipTest('Secure no-follow file opens require POSIX O_NOFOLLOW')


VIDEO_ID = str(uuid.uuid4())
OWNER_ID = str(uuid.uuid4())
OTHER_ID = str(uuid.uuid4())
STORAGE_NAME = uuid.uuid4().hex + ".mp4"
CONTENT = bytes(range(256)) * 4


class FakeRequest:
    def __init__(self, user_id, range_header=None):
        self.user = (
            {"user_id": user_id}
            if user_id is not None
            else None
        )
        self.headers = {}

        if range_header is not None:
            self.headers["range"] = range_header


def headers_dict(response):
    return dict(response.headers)


def check_accel_response(response):
    headers = headers_dict(response)

    assert response.status == "200 OK", response.status
    assert response.body == b""

    assert headers["X-Accel-Redirect"] == (
        "/_protected_videos/" + STORAGE_NAME
    )

    assert headers["Content-Type"] == "video/mp4"
    assert headers["Content-Disposition"] == "inline"
    assert headers["Cache-Control"] == "private, no-store"

    assert "Content-Range" not in headers
    assert "Content-Length" not in headers


def run_tests(directory):
    folder = Path(directory)
    video_path = folder / STORAGE_NAME
    video_path.write_bytes(CONTENT)

    video_row = {
        "id": VIDEO_ID,
        "uploaded_by": OWNER_ID,
        "page_id": None,
        "storage_name": STORAGE_NAME,
        "media_type": "video/mp4",
        "byte_size": len(CONTENT),
    }

    with (
        patch.object(videos, "VIDEO_DIR", str(folder)),
        patch.object(videos.db, "query", return_value=video_row),
    ):
        response = videos.get_video(
            FakeRequest(OWNER_ID), VIDEO_ID
        )
        check_accel_response(response)
        print("PASS: authorized video uses X-Accel-Redirect")

        for range_header in (
            "bytes=100-199",
            "bytes=900-",
            "bytes=-50",
            "bytes=999999-",
        ):
            response = videos.get_video(
                FakeRequest(OWNER_ID, range_header),
                VIDEO_ID,
            )
            check_accel_response(response)

        print("PASS: Range processing delegated to Nginx")

        response = videos.get_video(
            FakeRequest(OTHER_ID), VIDEO_ID
        )
        assert response.status == "404 Not Found"
        assert "X-Accel-Redirect" not in headers_dict(response)
        print("PASS: other user rejected")

        try:
            videos.get_video(FakeRequest(None), VIDEO_ID)
        except PermissionError:
            print("PASS: unauthenticated user rejected")
        else:
            raise AssertionError(
                "Unauthenticated request was not rejected"
            )

        video_path.write_bytes(CONTENT[:-1])

        response = videos.get_video(
            FakeRequest(OWNER_ID), VIDEO_ID
        )
        assert response.status == "404 Not Found"
        assert "X-Accel-Redirect" not in headers_dict(response)
        print("PASS: incorrect file size rejected")


with tempfile.TemporaryDirectory() as directory:
    run_tests(directory)

print("ALL VIDEO ACCEL TESTS PASSED")
