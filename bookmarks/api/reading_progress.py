import math

from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response

from bookmarks.models import ReadingProgress


class PositionSerializer(serializers.Serializer):
    anchor = serializers.CharField(max_length=100, allow_blank=True)
    offset = serializers.FloatField(min_value=0, max_value=1)
    fraction = serializers.FloatField(min_value=0, max_value=1)
    captureID = serializers.UUIDField(required=False, allow_null=True)
    eventDate = serializers.FloatField(min_value=0)
    sessionID = serializers.UUIDField()

    def validate(self, attrs):
        if not all(
            math.isfinite(attrs[key]) for key in ("offset", "fraction", "eventDate")
        ):
            raise serializers.ValidationError("Position must contain finite numbers.")
        return attrs

    def validate_eventDate(self, value):
        if value > timezone.now().timestamp() + 300:
            raise serializers.ValidationError("Reading event is in the future.")
        return value


class ProgressSerializer(serializers.Serializer):
    revision = serializers.IntegerField(min_value=0)
    url = serializers.URLField(max_length=2048)
    position = PositionSerializer()


def reading_progress(request, bookmark):
    # get_object() in the parent view restricts this endpoint to the owner.
    row, _ = ReadingProgress.objects.get_or_create(
        bookmark=bookmark, defaults={"url": bookmark.url}
    )
    if row.url != bookmark.url:
        ReadingProgress.objects.filter(pk=row.pk, url=row.url).update(
            url=bookmark.url, position={}, revision=row.revision + 1
        )
        row.refresh_from_db()

    def response(code=200):
        return Response(
            {
                "revision": row.revision,
                "url": row.url,
                "position": row.position or None,
            },
            status=code,
        )

    if request.method == "GET":
        return response()
    data = ProgressSerializer(data=request.data)
    data.is_valid(raise_exception=True)
    value = data.validated_data
    if value["url"] != bookmark.url or value["revision"] != row.revision:
        return response(409)
    position = dict(PositionSerializer(value["position"]).data)
    # Replaying a lost successful response is harmless. Old sessions cannot win
    # simply because their offline request reaches the server later.
    if position == row.position:
        return response()
    if row.position:
        old = PositionSerializer(data=row.position)
        old.is_valid(raise_exception=True)
        current = old.validated_data
        incoming_key = (
            value["position"]["eventDate"],
            str(value["position"]["sessionID"]),
        )
        current_key = (current["eventDate"], str(current["sessionID"]))
        if incoming_key <= current_key:
            return response()
    # Conditional UPDATE works on SQLite as well as PostgreSQL. A transaction
    # plus select_for_update alone would not protect SQLite deployments.
    changed = ReadingProgress.objects.filter(
        pk=row.pk, revision=value["revision"], url=bookmark.url
    ).update(position=position, revision=row.revision + 1)
    row.refresh_from_db()
    return response(200 if changed else 409)
