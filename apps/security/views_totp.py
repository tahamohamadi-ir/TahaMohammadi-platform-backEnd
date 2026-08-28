"""TOTP enrollment, recovery codes, and MFA disable views (Django-level)."""

from base64 import b32encode
from io import BytesIO

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django_otp import login as otp_login
from django_otp import user_has_device
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_otp.qr import write_qrcode_image

from apps.security.forms import MFAConfirmForm, TOTPConfirmForm
from apps.security.recovery import (
    consume_recovery_code,
    issue_recovery_codes,
    pop_recovery_codes,
    purge_mfa_state,
    stash_recovery_codes,
    unused_recovery_code_count,
)

DEVICE_NAME = "default"


def _client_ip(request) -> str:
    return (request.META.get("REMOTE_ADDR") or "")[:45]


def _verify_second_factor(user, token: str, *, ip: str) -> bool:
    """Accept current TOTP or an unused recovery code."""
    cleaned = (token or "").strip()
    device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
    if device is not None and device.verify_token(cleaned):
        return True
    return consume_recovery_code(user, cleaned, ip=ip)


@staff_member_required
def totp_setup(request):
    """Show enrollment status, QR for an unconfirmed device, or create one."""
    if user_has_device(request.user, confirmed=True):
        return render(
            request,
            "security/totp_setup.html",
            {
                "enrolled": True,
                "form": None,
                "config_url": None,
                "manual_secret": None,
                "qrcode_url": None,
                "unused_recovery_codes": unused_recovery_code_count(request.user),
                "regenerate_url": reverse("security_totp_regenerate"),
                "disable_url": reverse("security_totp_disable"),
            },
        )

    device = (
        TOTPDevice.objects.filter(user=request.user, confirmed=False)
        .order_by("-id")
        .first()
    )
    if device is None:
        TOTPDevice.objects.filter(user=request.user).delete()
        device = TOTPDevice.objects.create(
            user=request.user,
            name=DEVICE_NAME,
            confirmed=False,
        )

    form = TOTPConfirmForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        token = form.cleaned_data["otp_token"]
        if device.verify_token(token):
            device.confirmed = True
            device.save(update_fields=["confirmed"])
            request.session.cycle_key()
            otp_login(request, device)
            codes = issue_recovery_codes(request.user, ip=_client_ip(request))
            stash_recovery_codes(request.session, codes)
            messages.success(
                request,
                "Two-factor authentication is enabled. "
                "Store your recovery codes before continuing.",
            )
            return redirect("security_recovery_codes_reveal")
        form.add_error("otp_token", "Invalid code. Wait for a new code and try again.")

    manual_secret = b32encode(device.bin_key).decode("ascii")
    return render(
        request,
        "security/totp_setup.html",
        {
            "enrolled": False,
            "form": form,
            "config_url": device.config_url,
            "manual_secret": manual_secret,
            "qrcode_url": reverse("security_totp_qrcode"),
            "unused_recovery_codes": 0,
            "regenerate_url": None,
            "disable_url": None,
        },
    )


@staff_member_required
def totp_qrcode(request):
    """SVG QR for the current user's pending (unconfirmed) TOTP device only."""
    device = (
        TOTPDevice.objects.filter(user=request.user, confirmed=False)
        .order_by("-id")
        .first()
    )
    if device is None:
        raise Http404
    try:
        buf = BytesIO()
        write_qrcode_image(device.config_url, buf)
    except ModuleNotFoundError:
        return HttpResponse(
            "QR library unavailable",
            status=503,
            content_type="text/plain",
        )
    return HttpResponse(buf.getvalue(), content_type="image/svg+xml")


@staff_member_required
def recovery_codes_reveal(request):
    """One-time display of plaintext recovery codes from the session."""
    codes = pop_recovery_codes(request.session)
    if not codes:
        messages.warning(
            request,
            "Recovery codes are only shown once. Regenerate them if you need a new set.",
        )
        return redirect("security_totp_setup")
    return render(
        request,
        "security/recovery_codes_reveal.html",
        {"codes": codes},
    )


@staff_member_required
def totp_regenerate(request):
    """Replace unused recovery codes after confirming a second factor."""
    if not user_has_device(request.user, confirmed=True):
        return redirect("security_totp_setup")

    form = MFAConfirmForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        token = form.cleaned_data["otp_token"]
        if _verify_second_factor(request.user, token, ip=_client_ip(request)):
            codes = issue_recovery_codes(request.user, ip=_client_ip(request))
            stash_recovery_codes(request.session, codes)
            messages.success(request, "New recovery codes generated.")
            return redirect("security_recovery_codes_reveal")
        form.add_error("otp_token", "Invalid authentication or recovery code.")

    return render(
        request,
        "security/totp_confirm_action.html",
        {
            "form": form,
            "title": "Regenerate recovery codes",
            "intro": (
                "Enter a current authenticator code or an unused recovery code. "
                "Unused previous recovery codes will be invalidated."
            ),
            "submit_label": "Regenerate codes",
        },
    )


@staff_member_required
def totp_disable(request):
    """Remove TOTP + recovery codes so the user can re-enroll."""
    if not user_has_device(request.user, confirmed=True):
        return redirect("security_totp_setup")

    form = MFAConfirmForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        token = form.cleaned_data["otp_token"]
        if _verify_second_factor(request.user, token, ip=_client_ip(request)):
            purge_mfa_state(request.user, ip=_client_ip(request))
            request.session.cycle_key()
            messages.success(
                request,
                "Two-factor authentication was disabled. Enroll again to continue.",
            )
            return redirect("security_totp_setup")
        form.add_error("otp_token", "Invalid authentication or recovery code.")

    return render(
        request,
        "security/totp_confirm_action.html",
        {
            "form": form,
            "title": "Disable two-factor authentication",
            "intro": (
                "Enter a current authenticator code or an unused recovery code. "
                "You will need to enroll TOTP again before using the admin."
            ),
            "submit_label": "Disable two-factor",
        },
    )
