"""Authenticated delivery of generated HLS playlists and segments."""

from ..router import Response
from ..utils import json_response
from ..video_access import get_accessible_video
from ..video_hls import (
    hls_file_available,
    validate_hls_filename,
    validate_storage_name,
)


def get_hls(req, vid, filename):
    """Authorize HLS access and delegate file delivery to Nginx."""
    if not req.user:
        raise PermissionError("Authentication required")

    # Reject invalid names before querying the database.
    if not validate_hls_filename(filename):
        return json_response(
            {"error": "Video not found"},
            "404 Not Found",
        )

    video = get_accessible_video(req.user, vid)

    if not video:
        return json_response(
            {"error": "Video not found"},
            "404 Not Found",
        )

    storage_name = video["storage_name"]

    if (
        video["media_type"] != "video/mp4"
        or not validate_storage_name(storage_name)
    ):
        return json_response(
            {"error": "Video not found"},
            "404 Not Found",
        )

    if not hls_file_available(storage_name, filename):
        return json_response(
            {"error": "HLS unavailable"},
            "404 Not Found",
        )

    content_type = (
        "application/vnd.apple.mpegurl"
        if filename == "index.m3u8"
        else "video/mp2t"
    )

    return Response(
        b"",
        "200 OK",
        content_type=content_type,
        headers=[
            (
                "X-Accel-Redirect",
                "/_protected_hls/"
                + storage_name[:32]
                + "/"
                + filename,
            ),
            ("Cache-Control", "private, no-store"),
            ("Content-Disposition", "inline"),
        ],
    )
