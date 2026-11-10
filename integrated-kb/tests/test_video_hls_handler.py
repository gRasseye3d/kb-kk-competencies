"""Isolated tests for protected HLS delivery."""

import uuid
from unittest.mock import patch

from app.handlers import video_hls


VIDEO_ID = str(uuid.uuid4())
STORAGE_NAME = uuid.uuid4().hex + ".mp4"

USER = {"user_id": str(uuid.uuid4())}

VIDEO = {
    "id": VIDEO_ID,
    "uploaded_by": USER["user_id"],
    "page_id": None,
    "storage_name": STORAGE_NAME,
    "media_type": "video/mp4",
    "byte_size": 1024,
}


class FakeRequest:
    def __init__(self, user=USER):
        self.user = user


def check_success(filename, content_type):
    with (
        patch.object(
            video_hls,
            "get_accessible_video",
            return_value=VIDEO,
        ) as access_mock,
        patch.object(
            video_hls,
            "hls_file_available",
            return_value=True,
        ) as file_mock,
    ):
        response = video_hls.get_hls(
            FakeRequest(), VIDEO_ID, filename
        )

        headers = dict(response.headers)

        assert response.status == "200 OK"
        assert response.body == b""

        assert headers["Content-Type"] == content_type
        assert headers["Cache-Control"] == "private, no-store"

        assert headers["X-Accel-Redirect"] == (
            "/_protected_hls/"
            + STORAGE_NAME[:32]
            + "/"
            + filename
        )

        access_mock.assert_called_once_with(USER, VIDEO_ID)
        file_mock.assert_called_once_with(
            STORAGE_NAME, filename
        )


check_success(
    "index.m3u8",
    "application/vnd.apple.mpegurl",
)
print("PASS: authorized playlist")

check_success(
    "segment_00000.ts",
    "video/mp2t",
)
print("PASS: authorized segment")


with patch.object(
    video_hls,
    "get_accessible_video",
) as access_mock:
    try:
        video_hls.get_hls(
            FakeRequest(None),
            VIDEO_ID,
            "index.m3u8",
        )
    except PermissionError:
        pass
    else:
        raise AssertionError(
            "Unauthenticated request was accepted"
        )

    access_mock.assert_not_called()

print("PASS: unauthenticated user rejected")


with patch.object(
    video_hls,
    "get_accessible_video",
    return_value=None,
):
    response = video_hls.get_hls(
        FakeRequest(),
        VIDEO_ID,
        "index.m3u8",
    )

    assert response.status == "404 Not Found"
    assert "X-Accel-Redirect" not in dict(response.headers)

print("PASS: inaccessible video rejected")


for filename in (
    "../index.m3u8",
    "../../secret",
    "segment_00000.ts/../index.m3u8",
    "segment_abcde.ts",
    "other.m3u8",
):
    with patch.object(
        video_hls,
        "get_accessible_video",
    ) as access_mock:
        response = video_hls.get_hls(
            FakeRequest(),
            VIDEO_ID,
            filename,
        )

        assert response.status == "404 Not Found"
        access_mock.assert_not_called()

print("PASS: unsafe filenames rejected")


for changed in (
    {"media_type": "video/webm"},
    {"storage_name": "../secret.mp4"},
    {"storage_name": "a" * 32 + ".webm"},
):
    invalid_video = dict(VIDEO, **changed)

    with (
        patch.object(
            video_hls,
            "get_accessible_video",
            return_value=invalid_video,
        ),
        patch.object(
            video_hls,
            "hls_file_available",
        ) as file_mock,
    ):
        response = video_hls.get_hls(
            FakeRequest(),
            VIDEO_ID,
            "index.m3u8",
        )

        assert response.status == "404 Not Found"
        file_mock.assert_not_called()

print("PASS: invalid video metadata rejected")


with (
    patch.object(
        video_hls,
        "get_accessible_video",
        return_value=VIDEO,
    ),
    patch.object(
        video_hls,
        "hls_file_available",
        return_value=False,
    ),
):
    response = video_hls.get_hls(
        FakeRequest(),
        VIDEO_ID,
        "index.m3u8",
    )

    assert response.status == "404 Not Found"
    assert "X-Accel-Redirect" not in dict(response.headers)

print("PASS: missing HLS file rejected")

print("ALL HLS HANDLER TESTS PASSED")
