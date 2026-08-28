"""Public preview share URLs (DEFER-0016) — no session."""

from django.urls import path

from apps.content.views_preview import public_share_preview

urlpatterns = [
    path(
        "share/<str:token>/",
        public_share_preview,
        name="content_public_share_preview",
    ),
]
