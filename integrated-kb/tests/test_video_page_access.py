"""Security tests for videos attached to knowledge-base pages."""

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.handlers import videos

if not hasattr(os, 'O_NOFOLLOW'):
    raise unittest.SkipTest('Secure no-follow file opens require POSIX O_NOFOLLOW')


VIDEO_ID = str(uuid.uuid4())
OWNER_ID = str(uuid.uuid4())
OTHER_ID = str(uuid.uuid4())
PAGE_ID = str(uuid.uuid4())
STORAGE_NAME = uuid.uuid4().hex + ".mp4"

CONTENT = b"test video content"


def request_for(user_id):
    return SimpleNamespace(
        user={"user_id": user_id},
        headers={},
    )


def headers(response):
    return dict(response.headers)


with tempfile.TemporaryDirectory() as directory:
    folder = Path(directory)
    (folder / STORAGE_NAME).write_bytes(CONTENT)

    video = {
        "id": VIDEO_ID,
        "uploaded_by": OWNER_ID,
        "page_id": PAGE_ID,
        "storage_name": STORAGE_NAME,
        "media_type": "video/mp4",
        "byte_size": len(CONTENT),
    }

    page = {
        "id": PAGE_ID,
        "title": "Restricted test page",
    }

    def fake_query(sql, *args, **kwargs):
        if "FROM video_uploads" in sql:
            return video
        if "FROM pages" in sql:
            return page
        raise AssertionError("Unexpected database query")

    with (
        patch.object(videos, "VIDEO_DIR", folder),
        patch.object(videos.db, "query", side_effect=fake_query),
    ):
        with patch.object(
            videos.access, "page", return_value=False
        ) as access_check:
            response = videos.get_video(
                request_for(OTHER_ID), VIDEO_ID
            )

            assert response.status == "404 Not Found"
            assert "X-Accel-Redirect" not in headers(response)
            access_check.assert_called_once()

            print("PASS: restricted page video denied")

        with patch.object(
            videos.access, "page", return_value=True
        ) as access_check:
            response = videos.get_video(
                request_for(OTHER_ID), VIDEO_ID
            )

            assert response.status == "200 OK"
            assert response.body == b""

            assert headers(response)["X-Accel-Redirect"] == (
                "/_protected_videos/" + STORAGE_NAME
            )

            access_check.assert_called_once()

            print("PASS: permitted page video accessible")

        with patch.object(
            videos.access, "page", return_value=False
        ):
            response = videos.get_video(
                request_for(OWNER_ID), VIDEO_ID
            )

            assert response.status == "404 Not Found"
            assert "X-Accel-Redirect" not in headers(response)

            print("PASS: uploader cannot bypass page restrictions")

print("ALL VIDEO PAGE ACCESS TESTS PASSED")
