"""Isolated tests for the streaming multipart parser."""

import io
import os
import tempfile
from pathlib import Path

import app.video_multipart as video


def make_request(filename, content_type, payload):
    boundary = b"kbkk-test-boundary"

    body = (
        b"--" + boundary + b"\r\n"
        + b'Content-Disposition: form-data; name="video"; filename="'
        + filename.encode("utf-8")
        + b'"\r\n'
        + b"Content-Type: "
        + content_type.encode("ascii")
        + b"\r\n\r\n"
        + payload
        + b"\r\n--" + boundary + b"--\r\n"
    )

    return {
        "CONTENT_TYPE": (
            "multipart/form-data; boundary=kbkk-test-boundary"
        ),
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
    }


with tempfile.TemporaryDirectory() as directory:
    video.VIDEO_DIR = directory

    print("=== TEST 1: VALID MP4 ===")

    payload = (b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32 + b"test-video-content" * 1000)

    result = video.parse_video_upload(
        make_request("sample.mp4", "video/mp4", payload)
    )

    assert result["byte_size"] == len(payload)
    assert result["filename"] == "sample.mp4"
    assert result["media_type"] == "video/mp4"
    assert Path(result["temp_path"]).read_bytes() == payload

    os.unlink(result["temp_path"])
    print("PASS: video saved correctly")


    print("=== TEST: VALID WEBM ===")

    webm_payload = (
        b"\x1a\x45\xdf\xa3"
        + b"\\x00" * 8
        + b"webm"
        + b"\\x00" * 100
    )

    result = video.parse_video_upload(
        make_request("sample.webm", "video/webm", webm_payload)
    )

    assert result["media_type"] == "video/webm"
    assert Path(result["temp_path"]).read_bytes() == webm_payload

    os.unlink(result["temp_path"])
    print("PASS: WebM saved correctly")

    print("=== TEST: FAKE MP4 ===")

    try:
        video.parse_video_upload(
            make_request("fake.mp4", "video/mp4", b"not a video")
        )
        raise AssertionError("Fake MP4 accepted")
    except video.VideoUploadError:
        print("PASS: fake MP4 rejected")

    print("=== TEST 2: INVALID EXTENSION ===")

    try:
        video.parse_video_upload(
            make_request("test.exe", "video/mp4", payload)
        )
        raise AssertionError("Invalid extension accepted")
    except video.VideoUploadError:
        print("PASS: invalid extension rejected")

    print("=== TEST 3: EMPTY FILE ===")

    try:
        video.parse_video_upload(
            make_request("empty.mp4", "video/mp4", b"")
        )
        raise AssertionError("Empty file accepted")
    except video.VideoUploadError:
        print("PASS: empty file rejected")

    print("=== TEST 4: TEMPORARY FILE CLEANUP ===")

    remaining_files = list(Path(directory).iterdir())

    assert not remaining_files, remaining_files

    print("PASS: no temporary files remain")

print("ALL TESTS PASSED")
