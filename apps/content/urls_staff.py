"""Staff-only Django URLs (HTML profile pages + MFA fallback)."""

from django.shortcuts import redirect
from django.urls import path

from apps.admin.views import profile_detail_page, profile_index
from apps.content.views_preview import staff_content_preview


def staff_home(request):
    """``/staff/`` entry — send OTP-verified staff to the profile index."""
    return redirect("admin_profile_index")


urlpatterns = [
    path("", staff_home, name="staff_home"),
    path(
        "preview/<str:kind>/<int:pk>/",
        staff_content_preview,
        name="content_staff_preview",
    ),
    path("profiles/", profile_index, name="admin_profile_index"),
    path(
        "profiles/<str:locale>/<slug:slug>/",
        profile_detail_page,
        name="admin_profile_detail_page",
    ),
]
