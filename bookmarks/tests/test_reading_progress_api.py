import uuid

from django.utils import timezone

from bookmarks.models import Bookmark, User
from bookmarks.tests.helpers import BookmarkFactoryMixin, LinkdingApiTestCase


class ReadingProgressApiTestCase(LinkdingApiTestCase, BookmarkFactoryMixin):
    def setUp(self):
        token = self.setup_api_token()
        self.client.credentials(HTTP_AUTHORIZATION="Token " + token.key)
        self.bookmark = Bookmark.objects.create(
            owner=token.user,
            url="https://example.com",
            date_added=timezone.now(),
            date_modified=timezone.now(),
        )
        self.path = f"/api/bookmarks/{self.bookmark.pk}/reading-progress/"
        self.position = dict(
            anchor="passage-abc-0",
            offset=0.2,
            fraction=0.4,
            eventDate=timezone.now().timestamp(),
            sessionID=str(uuid.uuid4()),
            captureID=str(uuid.uuid4()),
        )

    def put(self, revision=0, position=None):
        return self.client.put(
            self.path,
            dict(
                revision=revision,
                url=self.bookmark.url,
                position=position or self.position,
            ),
            format="json",
        )

    def test_roundtrip_and_idempotent_retry(self):
        self.assertIsNone(self.client.get(self.path).data["position"])
        first = self.put()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data["revision"], 1)
        self.assertEqual(self.put().status_code, 409)
        self.assertEqual(self.put(1).data["revision"], 1)
        self.assertEqual(self.client.get(self.path).data, first.data)

    def test_old_event_does_not_overwrite_newer_and_start_over_works(self):
        self.put()
        old = self.position | dict(
            eventDate=self.position["eventDate"] - 10, fraction=0.9
        )
        self.assertEqual(self.put(1, old).data["position"]["fraction"], 0.4)
        reset = self.position | dict(
            eventDate=self.position["eventDate"] + 1, fraction=0
        )
        self.assertEqual(self.put(1, reset).data["position"]["fraction"], 0)

    def test_owner_only(self):
        other = User.objects.create_user("other")
        token = self.setup_api_token(other)
        self.client.credentials(HTTP_AUTHORIZATION="Token " + token.key)
        self.assertEqual(self.client.get(self.path).status_code, 404)
        self.assertEqual(self.put().status_code, 404)

    def test_unauthenticated(self):
        self.client.credentials()
        self.assertIn(self.client.get(self.path).status_code, [401, 403])

    def test_invalid_values_and_future_time(self):
        for patch in [
            dict(fraction=2),
            dict(offset=-1),
            dict(eventDate=-1),
            dict(eventDate=timezone.now().timestamp() + 600),
            dict(anchor="a" * 101),
        ]:
            self.assertEqual(self.put(position=self.position | patch).status_code, 400)

    def test_bookmark_change_invalidates_position(self):
        self.put()
        self.bookmark.url = "https://example.org/new"
        self.bookmark.save()
        result = self.client.get(self.path)
        self.assertIsNone(result.data["position"])
        self.assertEqual(result.data["revision"], 2)

    def test_progress_does_not_edit_bookmark(self):
        before = self.bookmark.date_modified
        self.put()
        self.bookmark.refresh_from_db()
        self.assertEqual(self.bookmark.date_modified, before)
