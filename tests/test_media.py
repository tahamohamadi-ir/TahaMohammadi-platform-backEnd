"""Media library tests — validators, storage naming, model, manager, admin form."""

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.media.admin import MediaAdminForm
from apps.media.models import Media
from apps.media.storage import media_upload_path
from apps.media.validators import (
    MAX_FILE_SIZE,
    validate_file_size,
    validate_file_type,
)

PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)

PDF_MIN = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"

SVG_MIN = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"></svg>'
SVG_SCRIPT = (
    b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
)
WAV_MIN = (
    b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
    b"D\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
)
MP3_MIN = b"ID3\x03\x00\x00\x00\x00\x00\x00\xff\xfb\x90\x00" + b"\x00" * 200


class FakeFile:
    """Minimal stand-in with the attributes validators read."""

    def __init__(self, size, name="big.png"):
        self.size = size
        self.name = name


@pytest.fixture
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def uploaded(name, content, content_type="application/octet-stream"):
    return SimpleUploadedFile(name, content, content_type=content_type)


class TestValidateFileType:
    def test_rejects_text_fake_named_png(self):
        fake = uploaded("x.png", b"hello")
        with pytest.raises(ValidationError):
            validate_file_type(fake)

    def test_rejects_extension_mime_mismatch(self):
        fake = uploaded("not-a-pdf.pdf", PNG_1X1)
        with pytest.raises(ValidationError):
            validate_file_type(fake)

    def test_accepts_real_png(self):
        validate_file_type(uploaded("photo.png", PNG_1X1))

    def test_accepts_real_pdf(self):
        validate_file_type(uploaded("cv.pdf", PDF_MIN))

    def test_accepts_svg(self):
        validate_file_type(uploaded("icon.svg", SVG_MIN))

    def test_rejects_svg_with_script(self):
        with pytest.raises(ValidationError):
            validate_file_type(uploaded("evil.svg", SVG_SCRIPT))

    def test_accepts_wav(self):
        validate_file_type(uploaded("clip.wav", WAV_MIN))

    def test_accepts_mp3(self):
        validate_file_type(uploaded("clip.mp3", MP3_MIN))

    def test_restores_file_position_after_check(self):
        fake = uploaded("photo.png", PNG_1X1)
        validate_file_type(fake)
        assert fake.tell() == 0
        rejected = uploaded("x.png", b"hello")
        with pytest.raises(ValidationError):
            validate_file_type(rejected)
        assert rejected.tell() == 0


class TestValidateFileSize:
    def test_rejects_over_5mb(self):
        with pytest.raises(ValidationError):
            validate_file_size(FakeFile(MAX_FILE_SIZE + 1))

    def test_accepts_exactly_5mb(self):
        validate_file_size(FakeFile(MAX_FILE_SIZE))

    def test_accepts_small_file(self):
        validate_file_size(FakeFile(1024))

    def test_av_cap_is_50mb(self):
        from apps.media.validators import MAX_AV_FILE_SIZE

        class WavFile:
            def __init__(self, size):
                self.size = size
                self.name = "clip.wav"
                self._pos = 0

            def seek(self, pos):
                self._pos = pos

            def tell(self):
                return self._pos

            def read(self, n=-1):
                data = WAV_MIN
                if n == -1:
                    return data
                return data[:n]

        validate_file_size(WavFile(MAX_AV_FILE_SIZE))
        with pytest.raises(ValidationError, match="50MB"):
            validate_file_size(WavFile(MAX_AV_FILE_SIZE + 1))


class TestMediaUploadPath:
    def test_sanitizes_unicode_and_spaces(self):
        media = Media(mime="image/png")
        path = media_upload_path(media, "فایل با فاصله و یونیکد.png")
        assert " " not in path
        assert path.isascii()
        assert path.endswith(".png")
        assert path.startswith("media/")
        assert path.count("/") == 1

    def test_extension_comes_from_detected_mime(self):
        media = Media(mime="image/jpeg")
        assert media_upload_path(media, "evil.exe").endswith(".jpeg")

    def test_ignores_user_directories(self):
        media = Media(mime="application/pdf")
        assert media_upload_path(media, "..\\..\\x\\evil.pdf").startswith("media/")
        assert ".." not in media_upload_path(media, "../../etc/passwd.pdf")

    def test_unique_per_call(self):
        media = Media(mime="image/gif")
        assert media_upload_path(media, "same.gif") != media_upload_path(
            media, "same.gif"
        )

    def test_refuses_unclassifiable_content(self):
        media = Media(mime="")
        with pytest.raises(ValidationError):
            media_upload_path(media, "blob.bin")


class TestMediaModel:
    def test_save_sets_mime_and_size_from_content(self, db, media_root):
        media = Media(
            file=uploaded("photo.png", PNG_1X1),
            title="Photo",
            is_active=False,
        )
        media.save()
        assert media.mime == "image/png"
        assert media.size == len(PNG_1X1)

    def test_save_ignores_client_content_type(self, db, media_root):
        media = Media(
            file=uploaded("photo.png", PNG_1X1, content_type="text/plain"),
            title="Photo",
        )
        media.save()
        assert media.mime == "image/png"


class TestMediaManager:
    def test_active_public_returns_only_active(self, db, media_root):
        active = Media.objects.create(
            file=uploaded("on.png", PNG_1X1),
            title="On",
            is_active=True,
        )
        Media.objects.create(
            file=uploaded("off.png", PNG_1X1),
            title="Off",
            is_active=False,
        )
        assert list(Media.objects.active_public()) == [active]
        assert list(Media.objects.all().active_public()) == [active]


class TestMediaAdminForm:
    def test_rejects_bad_mime(self):
        form = MediaAdminForm(
            data={"title": "Bad", "alt_text": "", "is_active": False},
            files={"file": uploaded("x.png", b"hello")},
        )
        assert not form.is_valid()
        assert "file" in form.errors

    def test_accepts_real_png(self):
        form = MediaAdminForm(
            data={"title": "Good", "alt_text": "", "is_active": False},
            files={"file": uploaded("photo.png", PNG_1X1)},
        )
        assert form.is_valid(), form.errors


class TestRenditionContract:
    def test_specs_exclude_original(self):
        from apps.media.renditions import RENDITION_NAMES, RENDITION_SPECS

        assert "original" not in RENDITION_NAMES
        assert {s.name for s in RENDITION_SPECS} == {"thumb", "card", "full"}

    def test_image_requires_rendition(self):
        from apps.media.renditions import select_public_rendition_name

        with pytest.raises(ValueError, match="rendition"):
            select_public_rendition_name("image/png", {"original"})

    def test_picks_largest_available(self):
        from apps.media.renditions import select_public_rendition_name

        assert (
            select_public_rendition_name("image/jpeg", {"thumb", "card"}) == "card"
        )

    def test_pdf_has_no_image_rendition(self):
        from apps.media.renditions import select_public_rendition_name

        assert select_public_rendition_name("application/pdf", set()) is None

    def test_original_forbidden_helper(self):
        from apps.media.renditions import original_forbidden_for_public

        assert original_forbidden_for_public("image/png") is True
        assert original_forbidden_for_public("application/pdf") is False
