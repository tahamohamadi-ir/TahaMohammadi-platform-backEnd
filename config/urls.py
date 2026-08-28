"""URL configuration — /admin/ React SPA, /staff/ Django staff HTML, /health/."""

from django.urls import include, path

from apps.api.admin_api import admin_api
from apps.api.admin_spa import serve_admin_ui
from apps.api.api import api
from apps.content.admin_api import admin_profile_create_sibling, admin_profile_detail
from apps.content.public_api import public_profile_detail, public_profile_list
from apps.health.views import health
from apps.media.views import serve_public_media
from apps.rebuild.views import rebuild_trigger

urlpatterns = [
    path("preview/", include("apps.content.urls_public_preview")),
    path("health/", health, name="health"),
    path("media/<path:name>", serve_public_media, name="public_media"),
    # Custom React admin SPA (ADR-0026, ADM-1 cutover). More specific staff
    # paths under /staff/ — not under /admin/ — so the SPA catch-all stays simple.
    path("admin/", serve_admin_ui, name="admin_spa"),
    path("admin/<path:spa_path>", serve_admin_ui, name="admin_spa_path"),
    # Django staff HTML: login/MFA fallback + preview + legacy profile editor.
    path("staff/", include("apps.security.urls")),
    path("staff/", include("apps.content.urls_staff")),
    path("api/v1/admin/", admin_api.urls),
    path("api/profiles/<str:locale>", public_profile_list, name="public_profile_list"),
    path(
        "api/profiles/<str:locale>/<slug:slug>",
        public_profile_detail,
        name="public_profile_detail",
    ),
    path(
        "api/admin/profiles/<str:locale>/<slug:slug>",
        admin_profile_detail,
        name="admin_profile_detail",
    ),
    path(
        "api/admin/profiles/<str:locale>/<slug:slug>/siblings/<str:target_locale>",
        admin_profile_create_sibling,
        name="admin_profile_create_sibling",
    ),
    path("api/", api.urls),
    path("rebuild-trigger/", rebuild_trigger, name="rebuild_trigger"),
]
