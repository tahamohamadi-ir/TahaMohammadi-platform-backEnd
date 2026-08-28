"""Public /media/ serves only active library files (staff may preview inactive)."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from apps.media.models import Media

PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def test_anonymous_gets_active_media(db, media_root):
    media = Media.objects.create(
        file=SimpleUploadedFile("photo.png", PNG_1X1, content_type="image/png"),
        title="Photo",
        is_active=True,
    )
    response = Client().get(f"/media/{media.file.name}")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("image/png")


def test_anonymous_cannot_fetch_inactive_media(db, media_root):
    media = Media.objects.create(
        file=SimpleUploadedFile("secret.png", PNG_1X1, content_type="image/png"),
        title="Secret",
        is_active=False,
    )
    response = Client().get(f"/media/{media.file.name}")
    assert response.status_code == 404


def test_unknown_media_path_404(db):
    response = Client().get("/media/media/does-not-exist.png")
    assert response.status_code == 404
