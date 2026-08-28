"""Rebuild trigger tests (P3-08) — HMAC signature, freshness, gating, script hook."""

import hashlib
import hmac
import time
from unittest.mock import MagicMock, patch

from django.test import Client, SimpleTestCase, override_settings
from django.urls import reverse

from apps.rebuild.services import (
    MAX_TRIGGER_AGE_SECONDS,
    build_signed_rebuild_url,
    invoke_static_rebuild,
    rebuild_script_path,
)

SECRET = "test-rebuild-secret"


def _token(secret, timestamp):
    message = f"taha-rebuild:{timestamp}"
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def _signed_post(client, timestamp=None, token=None, secret=SECRET):
    timestamp = int(time.time()) if timestamp is None else timestamp
    token = _token(secret, timestamp) if token is None else token
    return client.post(
        reverse("rebuild_trigger"), {"token": token, "timestamp": str(timestamp)}
    )


@override_settings(REBUILD_TRIGGER_ENABLED=True, REBUILD_TRIGGER_SECRET=SECRET)
class TestRebuildTrigger(SimpleTestCase):
    @patch("apps.rebuild.views.invoke_static_rebuild", return_value=True)
    def test_valid_signature_recent_timestamp_returns_200(self, mocked):
        response = _signed_post(Client())
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "triggered": True}
        mocked.assert_called_once_with(enabled=True)

    def test_invalid_token_returns_403(self):
        response = _signed_post(Client(), token="deadbeef")
        assert response.status_code == 403
        assert response.json() == {"status": "denied"}

    def test_expired_timestamp_returns_403(self):
        expired = int(time.time()) - MAX_TRIGGER_AGE_SECONDS - 60
        response = _signed_post(Client(), timestamp=expired)
        assert response.status_code == 403
        assert response.json() == {"status": "denied"}

    def test_get_returns_405(self):
        response = Client().get(reverse("rebuild_trigger"))
        assert response.status_code == 405

    @override_settings(REBUILD_TRIGGER_ENABLED=False, REBUILD_TRIGGER_SECRET=SECRET)
    def test_disabled_setting_returns_403(self):
        response = _signed_post(Client())
        assert response.status_code == 403
        assert response.json() == {"status": "denied"}


def test_build_signed_rebuild_url_shape():
    url = build_signed_rebuild_url(SECRET, "https://cms.example.com/")
    assert url.startswith("https://cms.example.com/rebuild-trigger/?token=")
    assert "timestamp=" in url


def test_invoke_static_rebuild_disabled_by_default(settings):
    settings.REBUILD_TRIGGER_ENABLED = False
    with patch("apps.rebuild.services.subprocess.Popen") as popen:
        assert invoke_static_rebuild() is False
        popen.assert_not_called()


def test_invoke_static_rebuild_starts_script(tmp_path, settings):
    script = tmp_path / "rebuild-web.sh"
    script.write_text("#!/bin/bash\n")
    settings.REBUILD_TRIGGER_ENABLED = True
    settings.REBUILD_SCRIPT_PATH = str(script)
    with patch("apps.rebuild.services.subprocess.Popen", return_value=MagicMock()) as popen:
        assert invoke_static_rebuild() is True
        popen.assert_called_once()
        command = popen.call_args[0][0]
        assert command[0] == "bash"
        assert command[1] == str(script)


def test_invoke_static_rebuild_missing_script(tmp_path, settings):
    settings.REBUILD_TRIGGER_ENABLED = True
    settings.REBUILD_SCRIPT_PATH = str(tmp_path / "missing.sh")
    with patch("apps.rebuild.services.subprocess.Popen") as popen:
        assert invoke_static_rebuild() is False
        popen.assert_not_called()


def test_rebuild_script_path_does_not_require_repo_depth(settings):
    settings.REBUILD_SCRIPT_PATH = ""
    path = rebuild_script_path()
    assert path.name.endswith("rebuild-web.sh") or path.name == "nonexistent-rebuild-web.sh"
