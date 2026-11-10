"""Shared access checks for uploaded videos."""

import uuid

from . import access, db


def get_accessible_video(user, vid):
    """Return a video row only when the user may access it.

    None means that the video does not exist or access is denied.
    Authentication is enforced by the calling HTTP handler.
    """
    if not user:
        return None

    try:
        video_id = str(uuid.UUID(vid))
    except (ValueError, TypeError, AttributeError):
        return None

    video = db.query(
        """
        SELECT
            id,
            uploaded_by,
            page_id,
            storage_name,
            media_type,
            byte_size
        FROM video_uploads
        WHERE id = %s
        """,
        (video_id,),
        one=True,
    )

    if not video:
        return None

    if video["page_id"] is None:
        if str(video["uploaded_by"]) != str(user["user_id"]):
            return None
    else:
        page = db.query(
            "SELECT * FROM pages WHERE id = %s",
            (video["page_id"],),
            one=True,
        )

        if not page or not access.page(user, page):
            return None

    return video
