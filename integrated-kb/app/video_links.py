"""Bind uploaded videos to pages during page save."""

import uuid
from html.parser import HTMLParser

from . import db


class VideoReferenceParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.video_ids = set()

    def handle_starttag(self, tag, attrs):
        if tag != "video":
            return

        attributes = dict(attrs)
        src = attributes.get("src", "")

        if not src.startswith("/api/videos/"):
            return

        raw_id = src[len("/api/videos/"):]

        try:
            video_id = str(uuid.UUID(raw_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("Invalid video reference")

        if raw_id.lower() != video_id:
            raise ValueError("Invalid video reference")

        self.video_ids.add(video_id)


def extract_video_ids(html):
    parser = VideoReferenceParser()
    parser.feed(html)
    parser.close()
    return parser.video_ids


def bind_page_videos(conn, page_id, user_id, html):
    video_ids = extract_video_ids(html)

    if not video_ids:
        return

    # Lock all referenced videos in deterministic order.
    rows = db.query(
        """
        SELECT id, uploaded_by, page_id
        FROM video_uploads
        WHERE id = ANY(%s::uuid[])
        ORDER BY id
        FOR UPDATE
        """,
        (sorted(video_ids),),
        conn=conn,
    )

    if len(rows) != len(video_ids):
        raise PermissionError("Video reference not found")

    for row in rows:
        existing_page_id = row["page_id"]

        if existing_page_id is None:
            # A new upload must belong to the current user.
            if str(row["uploaded_by"]) != str(user_id):
                raise PermissionError("Video ownership mismatch")

        elif str(existing_page_id) != str(page_id):
            # A video cannot be moved between articles.
            raise PermissionError("Video belongs to another page")

        # Already attached to this page: allowed.
        # Page editing permission was checked by pages.py.

    db.execute(
        """
        UPDATE video_uploads
        SET page_id = %s
        WHERE id = ANY(%s::uuid[])
          AND page_id IS NULL
        """,
        (page_id, sorted(video_ids)),
        conn=conn,
    )
