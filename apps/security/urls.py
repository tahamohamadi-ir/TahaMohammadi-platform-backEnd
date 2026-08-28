"""Staff security URLs (HTML MFA fallback + Django login)."""

from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from apps.security.forms import OTPLoginForm
from apps.security.views_totp import (
    recovery_codes_reveal,
    totp_disable,
    totp_qrcode,
    totp_regenerate,
    totp_setup,
)

urlpatterns = [
    path(
        "login/",
        LoginView.as_view(
            template_name="security/login.html",
            authentication_form=OTPLoginForm,
            redirect_authenticated_user=True,
        ),
        name="staff_login",
    ),
    path(
        "logout/",
        LogoutView.as_view(next_page="/admin/login/"),
        name="staff_logout",
    ),
    path("account/two-factor/", totp_setup, name="security_totp_setup"),
    path("account/two-factor/qrcode/", totp_qrcode, name="security_totp_qrcode"),
    path(
        "account/two-factor/recovery-codes/",
        recovery_codes_reveal,
        name="security_recovery_codes_reveal",
    ),
    path(
        "account/two-factor/regenerate/",
        totp_regenerate,
        name="security_totp_regenerate",
    ),
    path(
        "account/two-factor/disable/",
        totp_disable,
        name="security_totp_disable",
    ),
]
