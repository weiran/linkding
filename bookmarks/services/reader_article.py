"""Canonical, inert article documents shared by native and Kindle readers."""

import base64
import hashlib
import io
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urljoin, urlparse

import bleach
from bs4 import BeautifulSoup
from django.conf import settings
from PIL import Image, ImageOps

from bookmarks.models import BookmarkAsset
from bookmarks.services import assets, http_client

VERSION = "readability-shared-1"
MAX_SOURCE = 5 * 1024 * 1024
MAX_IMAGE = 8 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 20000000
TAGS = {
    "article",
    "div",
    "section",
    "span",
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ul",
    "ol",
    "li",
    "blockquote",
    "pre",
    "code",
    "strong",
    "em",
    "b",
    "i",
    "a",
    "img",
    "figure",
    "figcaption",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "br",
    "hr",
    "sup",
    "sub",
}


def bounded_response(url, limit):
    started = time.monotonic()
    with http_client.get(url, stream=True, timeout=(3, 3)) as response:
        response.raise_for_status()
        output = bytearray()
        for chunk in response.iter_content(16384):
            output.extend(chunk)
            if len(output) > limit or time.monotonic() - started > 10:
                raise ValueError("Download exceeds reader limit")
        return bytes(output)


def image_variant(data):
    with Image.open(io.BytesIO(data)) as source:
        source.load()
        color = ImageOps.exif_transpose(source).convert("RGB")
        color.thumbnail((1200, 1600))
        normal = io.BytesIO()
        color.save(normal, "JPEG", quality=82)
        eink = color.convert("L")
        eink.thumbnail((600, 800))
        small = io.BytesIO()
        eink.save(small, "JPEG", quality=85)
        return normal.getvalue(), small.getvalue()


def source_for(bookmark):
    candidates = BookmarkAsset.objects.filter(
        bookmark=bookmark, status="complete", content_type="text/html"
    ).order_by("-date_created")
    # Safari/app captures take precedence over anonymous server snapshots.
    candidate = (
        candidates.filter(display_name__startswith="cuttings-article-v1-").first()
        or candidates.filter(asset_type="snapshot").first()
    )
    if candidate:
        with assets.open_asset_file(candidate) as stream:
            raw = stream.read(MAX_SOURCE + 1)
        identity = f"{candidate.id}:{candidate.date_created.isoformat()}"
    else:
        raw = bounded_response(bookmark.url, MAX_SOURCE)
        identity = hashlib.sha256(raw).hexdigest()
    if len(raw) > MAX_SOURCE:
        raise ValueError("Article source exceeds reader limit")
    return raw.decode("utf-8"), identity


def canonical_article(bookmark):
    # Content-addressed cache is private on disk; access always comes through
    # the owner-scoped API. Never expose it as static content.
    source, identity = source_for(bookmark)
    key = hashlib.sha256((VERSION + bookmark.url + identity).encode()).hexdigest()
    directory = Path(settings.BASE_DIR) / "data" / "reader"
    directory.mkdir(mode=0o700, exist_ok=True)
    cache = directory / f"{bookmark.id}-{key}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    runtime = Path(settings.BASE_DIR) / "reader-runtime" / "extract.cjs"
    result = subprocess.run(
        ["node", str(runtime)],
        input=json.dumps({"html": source, "url": bookmark.url}),
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=15,
        check=True,
    )
    extracted = json.loads(result.stdout)
    clean = bleach.clean(
        extracted["html"],
        tags=TAGS,
        attributes={
            "a": ["href"],
            "img": ["src", "alt"],
            "td": ["colspan", "rowspan"],
            "th": ["colspan", "rowspan"],
        },
        protocols={"http", "https", "data"},
        strip=True,
    )
    document = BeautifulSoup(clean, "html.parser")
    pictures = []
    image_deadline = time.monotonic() + 10
    image_bytes = 0
    for image in document.find_all("img"):
        url = urljoin(bookmark.url, image.get("src", ""))
        try:
            if (
                len(pictures) >= 30
                or time.monotonic() > image_deadline
                or image_bytes > 10 * 1024 * 1024
            ):
                raise ValueError("Image count limit")
            if url.startswith("data:image/"):
                raw = base64.b64decode(url.split(",", 1)[1], validate=True)
            elif urlparse(url).scheme in {"http", "https"}:
                raw = bounded_response(url, MAX_IMAGE)
            else:
                raise ValueError("Unsupported image")
            if len(raw) > MAX_IMAGE:
                raise ValueError("Image limit")
            normal, eink = image_variant(raw)
            identifier = hashlib.sha256(normal).hexdigest()
            if not any(x["id"] == identifier for x in pictures):
                image_bytes += len(normal) + len(eink)
                pictures.append(
                    {
                        "id": identifier,
                        "mimeType": "image/jpeg",
                        "data": base64.b64encode(normal).decode(),
                        "kindleData": base64.b64encode(eink).decode(),
                    }
                )
            image["src"] = "reader-image:" + identifier
        except Exception:
            # Text remains readable if a publisher image is inaccessible.
            image.replace_with(document.new_string(image.get("alt", "")))
    counts = {}
    for block in document.select("p,h1,h2,h3,h4,h5,h6,li,pre,blockquote,figure,table"):
        text = " ".join(block.get_text(" ", strip=True).split())
        key_text = hashlib.sha256(text.encode()).hexdigest()[:20]
        occurrence = counts.get(key_text, 0)
        counts[key_text] = occurrence + 1
        block["id"] = f"passage-{key_text}-{occurrence}"
    for link in document.find_all("a", href=True):
        link["href"] = urljoin(bookmark.url, link["href"])
    html = "<article>" + str(document) + "</article>"
    digest = hashlib.sha256(html.encode()).hexdigest()
    payload = {
        "version": VERSION,
        "captureID": str(uuid.uuid5(uuid.NAMESPACE_URL, bookmark.url + digest)),
        "url": bookmark.url,
        "title": extracted["title"] or bookmark.resolved_title,
        "html": html,
        "assets": pictures,
    }
    # Unique temporary files avoid partial publication under concurrent reads.
    temporary = cache.with_suffix("." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(payload))
    os.replace(temporary, cache)
    for old in directory.glob(f"{bookmark.id}-*.json"):
        if old != cache:
            old.unlink(missing_ok=True)
    return payload
