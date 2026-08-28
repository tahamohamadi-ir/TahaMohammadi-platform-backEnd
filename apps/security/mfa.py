"""MFA enforcement middleware for Django staff HTML under ``/staff/``.

Policy for authenticated staff on ``/staff/`` paths:

- No confirmed TOTP device: allow login/logout and TOTP enrollment; redirect
  other ``/staff/`` paths to setup.
- Confirmed device but session not OTP-verified: allow only login/logout;
  enrollment/QR paths are NOT exempt (prevents secret leakage via stale sessions).
- Confirmed device and ``request.user.otp_device`` set: allow.
- Non-``/staff/`` paths are never affected (SPA ``/admin/`` uses Ninja OTP).

SPA enrollment remains ``/admin/security`` + ``/api/v1/admin/auth/mfa/*``.
"""

from django.http import HttpResponseRedirect
from django.urls import reverse

STAFF_PREFIX = "/staff/"
LOGIN_PATH = "/staff/login/"
LOGOUT_PATH = "/staff/logout/"
MFA_SETUP_PATH = "/staff/account/two-factor/"


def _mfa_setup_url() -> str:
    try:
        return reverse("security_totp_setup")
    except Exception:
        return MFA_SETUP_PATH


class MFAEnforcementMiddleware:
    """Require TOTP enrollment and verified OTP session for ``/staff/`` HTML."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith(STAFF_PREFIX):
            return self.get_response(request)

        if not request.user.is_authenticated or not request.user.is_staff:
            return self.get_response(request)

        path = request.path
        if path in (LOGIN_PATH, LOGOUT_PATH):
            return self.get_response(request)

        from django_otp import user_has_device

        has_device = user_has_device(request.user, confirmed=True)
        otp_ok = getattr(request.user, "otp_device", None) is not None

        if not has_device:
            if path.startswith(MFA_SETUP_PATH):
                return self.get_response(request)
            return HttpResponseRedirect(_mfa_setup_url())

        if otp_ok:
            return self.get_response(request)

        return HttpResponseRedirect(LOGIN_PATH + "?next=" + path)
