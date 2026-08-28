"""Media presentation metadata (BK-03) — focal, rights, license, captions.

Additive Media fields for the next-gen frontend: DB-level focal range
constraints, SET_NULL license linkage, and a leak check proving the public
media projection stays rights-free.
"""

from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction

from apps.media.models import Media, MediaLicense
from apps.media.public_urls import public_media_ref

PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)

PUBLIC_REF_KEYS = {"url", "alt", "mime", "title", "size"}


@pytest.fixture
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def uploaded(name, content, content_type="image/png"):
    return SimpleUploadedFile(name, content, content_type=content_type)


def make_media(**kwargs):
    defaults = {
        "file": uploaded("photo.png", PNG_1X1),
        "title": "Photo",
    }
    defaults.update(kwargs)
    return Media.objects.create(**defaults)


class TestMediaLicenseModel:
    def test_str_and_ordering(self, db):
        MediaLicense.objects.create(name="CC BY-SA 4.0")
        beta = MediaLicense.objects.create(name="CC BY 4.0")
        assert str(beta) == "CC BY 4.0"
        names = [item.name for item in MediaLicense.objects.all()]
        assert names == ["CC BY 4.0", "CC BY-SA 4.0"]

    def test_notes_blank_by_default(self, db):
        license = MediaLicense.objects.create(name="MIT")
        assert license.notes == ""


class TestMediaPresentationDefaults:
    def test_focal_license_caption_rights_default_empty(self, db, media_root):
        license = MediaLicense.objects.create(name="CC0")
        media = make_media(license=license)
        assert media.focal_x is None
        assert media.focal_y is None
        assert media.caption_fa == ""
        assert media.caption_en == ""
        assert media.rights_statement_fa == ""
        assert media.rights_statement_en == ""
        assert media.license_id == license.pk

    def test_rights_fields_persist_content(self, db, media_root):
        media = make_media(
            rights_statement_fa="Copyright FA",
            rights_statement_en="(c) Taha",
            caption_fa="caption-fa",
            caption_en="Caption",
        )
        media.refresh_from_db()
        assert media.rights_statement_fa == "Copyright FA"
        assert media.rights_statement_en == "(c) Taha"
        assert media.caption_fa == "caption-fa"
        assert media.caption_en == "Caption"


class TestFocalPercentConstraints:
    def test_rejects_out_of_range_focal_x(self, db, media_root):
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                make_media(focal_x=Decimal("150.00"))

    def test_rejects_out_of_range_focal_y(self, db, media_root):
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                make_media(focal_y=Decimal("-0.01"))

    @pytest.mark.parametrize("axis", ["focal_x", "focal_y"])
    def test_accepts_bounds_zero_and_hundred(self, db, media_root, axis):
        for value in (Decimal("0.00"), Decimal("100.00")):
            media = make_media(**{axis: value})
            media.refresh_from_db()
            assert getattr(media, axis) == value

    def test_negative_focal_x_rejected(self, db, media_root):
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                make_media(focal_x=Decimal("-1.00"))


class TestLicenseSetNull:
    def test_deleting_license_nulls_media_fk(self, db, media_root):
        license = MediaLicense.objects.create(name="CC BY-NC 4.0")
        media = make_media(license=license)
        license.delete()
        media.refresh_from_db()
        assert media.license_id is None

    def test_license_media_related_name(self, db, media_root):
        license = MediaLicense.objects.create(name="CC BY 4.0")
        media = make_media(license=license)
        assert list(license.media.all()) == [media]


class TestPublicProjectionStaysRightsFree:
    def test_inactive_media_with_rights_yields_no_public_ref(self, db, media_root):
        media = make_media(
            is_active=False,
            rights_statement_fa="internal-fa",
            rights_statement_en="internal note",
        )
        assert public_media_ref(media) is None

    def test_active_media_public_ref_excludes_new_fields(self, db, media_root):
        license = MediaLicense.objects.create(name="CC BY 4.0")
        media = make_media(
            is_active=True,
            license=license,
            focal_x=Decimal("30.00"),
            focal_y=Decimal("70.00"),
            rights_statement_fa="owner-fa",
            rights_statement_en="Owner: Taha",
            caption_fa="caption-fa",
            caption_en="Caption",
        )
        ref = public_media_ref(media)
        assert ref is not None
        assert set(ref) == PUBLIC_REF_KEYS | {"focal"}
        assert ref["focal"] == {"x": 30.0, "y": 70.0}
        for value in ref.values():
            assert "Owner" not in str(value)
            assert "owner" not in str(value)
            assert "caption" not in str(value)


class TestPublicRefFocal:
    def test_focal_present_when_both_axes_set(self, db, media_root):
        media = make_media(is_active=True, focal_x=Decimal("12.50"), focal_y=Decimal("88.00"))
        ref = public_media_ref(media)
        assert ref is not None
        assert ref["focal"] == {"x": 12.5, "y": 88.0}
        assert all(isinstance(v, float) for v in ref["focal"].values())

    def test_focal_absent_when_unset(self, db, media_root):
        media = make_media(is_active=True)
        ref = public_media_ref(media)
        assert ref is not None
        assert set(ref) == PUBLIC_REF_KEYS
        assert "focal" not in ref

    def test_focal_absent_when_partial(self, db, media_root):
        media = make_media(is_active=True, focal_x=Decimal("40.00"))
        ref = public_media_ref(media)
        assert ref is not None
        assert "focal" not in ref

    def test_inactive_media_with_focal_yields_no_public_ref(self, db, media_root):
        media = make_media(
            is_active=False,
            focal_x=Decimal("30.00"),
            focal_y=Decimal("70.00"),
        )
        assert public_media_ref(media) is None
