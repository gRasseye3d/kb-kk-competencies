"""Isolated tests for video upload database integration."""

import io
import json
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from app.handlers import videos


class FakeRequest:
    user = {
        "user_id": "00000000-0000-4000-8000-000000000001",
        "csrf_token": "test-csrf-token",
    }

    headers = {
        "x-csrf-token": "test-csrf-token",
    }

    environ = {
        "CONTENT_TYPE": "multipart/form-data; boundary=test",
        "CONTENT_LENGTH": "100",
        "wsgi.input": io.BytesIO(b""),
    }


@contextmanager
def fake_transaction():
    yield object()


def run_case(directory, database_fails):
    folder = Path(directory)
    temp_path = folder / "temporary.upload"
    temp_path.write_bytes(b"test-video-content")

    upload_data = {
        "id": "00000000-0000-4000-8000-000000000002",
        "temp_path": str(temp_path),
        "filename": "test.mp4",
        "media_type": "video/mp4",
        "byte_size": 18,
    }

    calls = []

    def fake_execute(sql, params, conn=None):
        calls.append((sql, params))

        if database_fails:
            raise RuntimeError("Simulated database failure")

        return 1

    with (
        patch.object(videos, "VIDEO_DIR", str(folder)),
        patch.object(videos, "parse_video_upload", return_value=upload_data),
        patch.object(videos.db, "transaction", fake_transaction),
        patch.object(videos.db, "execute", fake_execute),
    ):
        if database_fails:
            try:
                videos.upload_video(FakeRequest())
            except RuntimeError as error:
                assert str(error) == "Simulated database failure"
            else:
                raise AssertionError("Database error was not propagated")

            assert not list(folder.iterdir()), (
                "Files remain after database failure"
            )

            print("PASS: database failure removes uploaded files")

        else:
            response = videos.upload_video(FakeRequest())

            assert response.status == "201 Created"
            data = json.loads(response.body)

            assert data["id"] == upload_data["id"]
            assert data["url"] == "/api/videos/" + upload_data["id"]
            assert len(calls) == 1

            files = list(folder.iterdir())
            assert len(files) == 1
            assert files[0].suffix == ".mp4"
            assert files[0].read_bytes() == b"test-video-content"

            print("PASS: successful upload creates file and DB record")


with tempfile.TemporaryDirectory() as directory:
    run_case(directory, database_fails=False)

with tempfile.TemporaryDirectory() as directory:
    run_case(directory, database_fails=True)

print("VIDEO DATABASE TESTS PASSED")
