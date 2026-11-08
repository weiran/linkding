import base64
import io
from unittest.mock import patch

from django.utils import timezone
from PIL import Image

from bookmarks.models import Bookmark
from bookmarks.services.reader_article import canonical_article, image_variant
from bookmarks.tests.helpers import BookmarkFactoryMixin, LinkdingApiTestCase


class SharedReaderTests(LinkdingApiTestCase, BookmarkFactoryMixin):
    def setUp(self):
        token = self.setup_api_token()
        self.client.credentials(HTTP_AUTHORIZATION="Token " + token.key)
        self.bookmark = Bookmark.objects.create(
            owner=token.user,
            url="https://example.com/article",
            title="Example",
            date_added=timezone.now(),
            date_modified=timezone.now(),
        )

    def test_shared_article_has_stable_anchors_and_no_publisher_code(self):
        text = (
            "A substantial paragraph about reading articles together across devices. "
            * 15
        )
        source = (
            "<html><head><title>Example article</title></head><body><article><h1>Example article</h1><p>"
            + text
            + "</p><p>"
            + text
            + '</p><img src="data:image/png;base64,'
        )
        image = io.BytesIO()
        Image.new("RGB", (1600, 2000), "red").save(image, "PNG")
        source += (
            base64.b64encode(image.getvalue()).decode()
            + '" onerror="alert(1)"><script>alert(1)</script></article></body></html>'
        )
        with patch(
            "bookmarks.services.reader_article.source_for",
            return_value=(source, "test-source"),
        ):
            article = canonical_article(self.bookmark)
            again = canonical_article(self.bookmark)
        self.assertEqual(article, again)
        self.assertIn("passage-", article["html"])
        self.assertNotIn("<script", article["html"])
        self.assertNotIn("onerror", article["html"])
        self.assertIn("reader-image:", article["html"])
        small = Image.open(
            io.BytesIO(base64.b64decode(article["assets"][0]["kindleData"]))
        )
        self.assertEqual(small.mode, "L")
        self.assertLessEqual(small.width, 600)
        self.assertLessEqual(small.height, 800)

    def test_endpoint_is_private(self):
        self.client.credentials()
        response = self.client.get(f"/api/bookmarks/{self.bookmark.pk}/reader-article/")
        self.assertIn(response.status_code, [401, 403])

    def test_another_user_cannot_read(self):
        other = self.setup_user()
        token = self.setup_api_token(other)
        self.client.credentials(HTTP_AUTHORIZATION="Token " + token.key)
        self.assertEqual(
            self.client.get(
                f"/api/bookmarks/{self.bookmark.pk}/reader-article/"
            ).status_code,
            404,
        )

    def test_image_orientation_and_limits(self):
        data = io.BytesIO()
        Image.new("RGB", (2000, 1000), "blue").save(data, "PNG")
        normal, small = image_variant(data.getvalue())
        self.assertEqual(Image.open(io.BytesIO(normal)).size, (1200, 600))
        self.assertEqual(Image.open(io.BytesIO(small)).size, (600, 300))
