# Reading progress API (fork)

Based on upstream v1.47.0. This fork adds private reader state, not a second bookmark library.

`GET /api/bookmarks/<id>/reading-progress/` returns `{revision, url, position}`.
A missing position is null. `PUT` accepts the same envelope with a non-null position:

```json
{"revision":0,"url":"https://example.com/article","position":{"anchor":"passage-example-0","offset":0.2,"fraction":0.6,"captureID":null,"eventDate":1794050000,"sessionID":"00000000-0000-0000-0000-000000000001"}}
```

Use the normal token authorization header. Only the bookmark owner can access progress, even for shared bookmarks. Numbers must be finite; offsets/fractions are 0–1; eventDate is Unix seconds and may not exceed server time by more than five minutes. captureID is optional; sessionID identifies the reading session.

The revision is a compare-and-swap precondition. A stale revision or URL returns 409 with current state. Refetch/reconcile rather than blindly overwriting. Repeated identical state is harmless. State order is `(eventDate, lowercase session UUID)`; an older interaction cannot replace a newer one just by uploading later. Clients must use actual interaction time and reasonably synchronized clocks. Backwards reading and start-over are valid newer events. Equal timestamps within the same session retain the first accepted value.

Progress updates do not change bookmark modification dates, tags, unread status or snapshots. Bookmark deletion cascades to progress. Changing a URL invalidates progress on next access and increments revision. Clients should fetch at article open, retain local offline state, and retry on foreground/reconnect. There is no bulk progress feed yet.

Dingbat uses content-derived passage IDs, a normalized within-block offset and a layout-dependent fallback fraction. Sharing the same captured source and extractor/anchor version improves restoration; differing captures cannot guarantee identical passage positions. The stock Linkding web reader does not yet publish or restore this progress. The future Kindle app must implement this contract; it has not been created.

## Deployment

`Dockerfile.progress` overlays only the changed Python application files and migration onto the digest-pinned official Plus image. Build using `docker build -f Dockerfile.progress -t homelab/linkding:progress-<commit> .`. This retains the upstream Chromium/SingleFile dependencies. Back up SQLite before deploying. The additive 0055 migration runs through the image's normal startup. Rolling back to upstream leaves the unused progress table intact; avoid restoring an older whole database unless deliberately discarding subsequent writes.

Tests: `uv run --python 3.13 python manage.py test bookmarks.tests.test_reading_progress_api bookmarks.tests.test_bookmarks_api bookmarks.tests.test_bookmark_assets_api --noinput`.
