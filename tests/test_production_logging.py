"""Production settings tests — the json formatter must emit real JSON (P3)."""

import importlib
import json
import logging
import logging.config
from io import StringIO


def _load_production_settings(monkeypatch):
    monkeypatch.setenv("ALLOWED_HOSTS", "cms.example.com")
    monkeypatch.setenv("DJANGO_SECRET_KEY", "x" * 60)
    monkeypatch.setenv("POSTGRES_USER", "cms")
    monkeypatch.setenv("POSTGRES_PASSWORD", "cms")
    return importlib.import_module("config.settings.production")


def test_production_logging_formatter_emits_json(monkeypatch):
    production = _load_production_settings(monkeypatch)
    stream = StringIO()
    logging.config.dictConfig(
        {
            "version": 1,
            "formatters": production.LOGGING["formatters"],
            "handlers": {
                "capture": {
                    "class": "logging.StreamHandler",
                    "stream": stream,
                    "formatter": "json",
                }
            },
            "loggers": {"test.production.json": {"handlers": ["capture"], "level": "INFO"}},
        }
    )
    logging.getLogger("test.production.json").info("hello %s", "world")

    payload = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert payload["message"] == "hello world"
    assert payload["levelname"] == "INFO"
    assert payload["timestamp"]


def test_production_json_formatter_uses_jsonlogger(monkeypatch):
    production = _load_production_settings(monkeypatch)
    assert production.LOGGING["formatters"]["json"]["()"] == "pythonjsonlogger.json.JsonFormatter"
