import unittest
import uuid
from contextlib import contextmanager
from unittest.mock import patch

from app.handlers import pages


SPACE_ID = "fc33d94f-c742-4f47-b09b-01be0ea3e4c4"
PARENT_ID = "2e517de8-34e5-442f-aaab-79280499b0a2"
OTHER_SPACE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


class FakeRequest:
    user = {
        "user_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "roles": [],
    }


@contextmanager
def fake_transaction():
    yield object()


class PageParentUUIDTests(unittest.TestCase):

    def setUp(self):
        self.req = FakeRequest()

        self.payload = {
            "space_id": SPACE_ID,
            "parent_id": PARENT_ID,
            "title": "PostgreSQL",
            "content": "<p>Test content</p>",
            "labels": [],
        }

    def run_create(self, parent, allowed=True):
        with (
            patch.object(pages.access, "csrf", return_value=self.payload),
            patch.object(pages.access, "space", return_value=True),
            patch.object(pages.access, "page", return_value=allowed),
            patch.object(pages.db, "transaction", fake_transaction),
            patch.object(pages, "_page", return_value=parent),
            patch.object(pages.db, "query") as query,
            patch.object(pages.db, "execute"),
            patch.object(pages, "_labels"),
            patch.object(pages.video_links, "bind_page_videos"),
            patch.object(pages.audit, "log"),
        ):
            query.return_value = {"id": uuid.uuid4()}
            return pages.create_page(self.req)

    def test_valid_parent_same_space(self):
        parent = {
            "id": PARENT_ID,
            "space_id": uuid.UUID(SPACE_ID),
        }

        result = self.run_create(parent)

        self.assertIsNotNone(result)

    def test_parent_in_another_space(self):
        parent = {
            "id": PARENT_ID,
            "space_id": uuid.UUID(OTHER_SPACE_ID),
        }

        with self.assertRaisesRegex(
            PermissionError, "Invalid parent"
        ):
            self.run_create(parent)

    def test_missing_parent(self):
        with self.assertRaisesRegex(
            PermissionError, "Invalid parent"
        ):
            self.run_create(None)

    def test_parent_access_denied(self):
        parent = {
            "id": PARENT_ID,
            "space_id": uuid.UUID(SPACE_ID),
        }

        with self.assertRaisesRegex(
            PermissionError, "Invalid parent"
        ):
            self.run_create(parent, allowed=False)

    def test_invalid_parent_uuid(self):
        self.payload["parent_id"] = "not-a-uuid"

        with self.assertRaisesRegex(
            PermissionError, "Invalid parent"
        ):
            self.run_create(None)


if __name__ == "__main__":
    unittest.main()
