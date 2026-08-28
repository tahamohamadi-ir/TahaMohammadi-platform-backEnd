"""Staff + OTP gate for non-SPA Django views (preview, HTML MFA fallback)."""

from __future__ import annotations

from functools import wraps

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django_otp import user_has_device


def staff_otp_required(view_func):
    """Require authenticated staff; when TOTP is enrolled, require OTP session.

    Unauthenticated users redirect to ``LOGIN_URL`` (SPA or Django accounts login).
    Staff without a confirmed device may reach enrollment HTML under ``/staff/``.
    Staff with a device but no OTP session redirect to login (re-auth with OTP).
    """

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect_to_login(
                request.get_full_path(), login_url=settings.LOGIN_URL
            )
        if not user.is_staff or not user.is_active:
            return HttpResponseForbidden("Staff access required.")

        has_device = user_has_device(user, confirmed=True)
        otp_ok = getattr(user, "otp_device", None) is not None
        if has_device and not otp_ok:
            return redirect_to_login(
                request.get_full_path(), login_url=settings.LOGIN_URL
            )
        return view_func(request, *args, **kwargs)

    return _wrapped


def redirect_unauthenticated_to_login(request):
    """Helper for views that need an explicit redirect response."""
    return HttpResponseRedirect(
        f"{settings.LOGIN_URL}?next={request.get_full_path()}"
    )
