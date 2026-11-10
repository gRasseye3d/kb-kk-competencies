"""Tests for video ownership and page binding."""

import uuid
from unittest.mock import patch

from app import video_links


PAGE_ID = str(uuid.uuid4())
OTHER_PAGE_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())
OTHER_USER_ID = str(uuid.uuid4())
VIDEO_ID = str(uuid.uuid4())

HTML = (
    '<p>Example</p>'
    f'<video src="/api/videos/{VIDEO_ID}" controls></video>'
)


def check_binding(uploaded_by, attached_page, should_pass):
    rows = [{
        "id": uuid.UUID(VIDEO_ID),
        "uploaded_by": uuid.UUID(uploaded_by),
        "page_id": (
            uuid.UUID(attached_page)
            if attached_page else None
        ),
    }]

    with (
        patch.object(video_links.db, "query", return_value=rows),
        patch.object(video_links.db, "execute") as execute,
    ):
        if should_pass:
            video_links.bind_page_videos(
                object(), PAGE_ID, USER_ID, HTML
            )
            execute.assert_called_once()
        else:
            try:
                video_links.bind_page_videos(
                    object(), PAGE_ID, USER_ID, HTML
                )
            except PermissionError:
                pass
            else:
                raise AssertionError("Expected PermissionError")

            execute.assert_not_called()


assert video_links.extract_video_ids(HTML) == {VIDEO_ID}
print("PASS: video UUID extracted from HTML")

check_binding(USER_ID, None, True)
print("PASS: own new video accepted")

check_binding(OTHER_USER_ID, None, False)
print("PASS: foreign new video rejected")

check_binding(OTHER_USER_ID, PAGE_ID, True)
print("PASS: existing page video accepted")

check_binding(USER_ID, OTHER_PAGE_ID, False)
print("PASS: video from another page rejected")

with (
    patch.object(video_links.db, "query", return_value=[]),
    patch.object(video_links.db, "execute") as execute,
):
    try:
        video_links.bind_page_videos(
            object(), PAGE_ID, USER_ID, HTML
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("Missing video was accepted")

    execute.assert_not_called()

print("PASS: missing video rejected")
print("ALL VIDEO LINK TESTS PASSED")
