"""Shared pytest fixtures (P3). Factories live per-app in their test modules."""

import pytest
from django.contrib.auth import get_user_model


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(
        username="tester",
        email="tester@example.com",
        password="test-pass-123",
    )


@pytest.fixture
def admin_user(db):
    return get_user_model().objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="test-pass-123",
    )


@pytest.fixture
def client(user):
    from django.test import Client

    c = Client()
    c.force_login(user)
    return c
