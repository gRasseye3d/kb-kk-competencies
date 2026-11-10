"""Reject symbolic links when streaming video."""

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.handlers import videos

if not hasattr(os, 'O_NOFOLLOW'):
    raise unittest.SkipTest('Secure symlink rejection requires POSIX O_NOFOLLOW')


video_id = str(uuid.uuid4())
user_id = str(uuid.uuid4())
storage_name = uuid.uuid4().hex + ".mp4"

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)

    target = root / "secret.txt"
    target.write_bytes(b"THIS MUST NEVER BE STREAMED")

    link = root / storage_name
    os.symlink(target, link)

    metadata = {
        "id": uuid.UUID(video_id),
        "uploaded_by": uuid.UUID(user_id),
        "page_id": None,
        "storage_name": storage_name,
        "media_type": "video/mp4",
        "byte_size": target.stat().st_size,
    }

    request = SimpleNamespace(
        user={"user_id": user_id},
        headers={},
    )

    with (
        patch.object(videos, "VIDEO_DIR", root),
        patch.object(videos.db, "query", return_value=metadata),
    ):
        response = videos.get_video(request, video_id)

    assert response.status == "404 Not Found", response.status

print("PASS: symbolic link rejected")
print("ALL VIDEO SYMLINK TESTS PASSED")
