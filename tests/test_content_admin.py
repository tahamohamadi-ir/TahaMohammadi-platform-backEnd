"""Content admin surface after DEBT-0003 Wagtail uninstall."""

import pytest
from django_otp.plugins.otp_totp.models import TOTPDevice


@pytest.mark.django_db
def test_retired_articles_snippet_url_not_served(client, admin_user):
    """Former Wagtail snippet URLs are gone; SPA owns CRUD."""
    TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)
    client.force_login(admin_user)
    session = client.session
    session["otp_device_id"] = TOTPDevice.objects.get(user=admin_user).persistent_id
    session.save()
    response = client.get("/staff/snippets/content/article/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_wagtail_not_importable_as_runtime_dep():
    """Package removal gate for DEBT-0003 CLOSED."""
    import importlib.util

    assert importlib.util.find_spec("wagtail") is None
