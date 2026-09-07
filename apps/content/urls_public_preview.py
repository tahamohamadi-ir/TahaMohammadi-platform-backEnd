from django.urls import path

from apps.content.views_preview import (
    public_share_preview,
    public_share_preview_attachment,
    public_share_preview_file,
)

urlpatterns = [
    path(
        "share/<str:token>/",
        public_share_preview,
        name="content_public_share_preview",
    ),
    path(
        "share/<str:token>/file/",
        public_share_preview_file,
        name="content_public_share_preview_file",
    ),
    path(
        "share/<str:token>/attachment/<int:media_id>/",
        public_share_preview_attachment,
        name="content_public_share_preview_attachment",
    ),
]
