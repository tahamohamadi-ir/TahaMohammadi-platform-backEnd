"""Admin login form with optional TOTP / recovery-code second factor."""

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.utils.translation import gettext_lazy as _
from django_otp import user_has_device
from django_otp.forms import OTPAuthenticationFormMixin
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.security.recovery import consume_recovery_code


class OTPLoginForm(OTPAuthenticationFormMixin, AuthenticationForm):
    """Password login; require OTP or recovery code when a TOTP device exists."""

    otp_device = forms.CharField(required=False, widget=forms.Select)
    otp_token = forms.CharField(
        label=_("Authentication code"),
        required=False,
        max_length=40,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "text",
                "placeholder": _("Authenticator or recovery code"),
            }
        ),
    )
    otp_challenge = forms.CharField(required=False)

    def clean(self):
        cleaned_data = super().clean()
        user = self.get_user()
        if user is not None and user_has_device(user, confirmed=True):
            token = (cleaned_data.get("otp_token") or "").strip()
            try:
                self.clean_otp(user)
            except forms.ValidationError as exc:
                codes = {getattr(err, "code", None) for err in getattr(exc, "error_list", [])}
                if getattr(exc, "code", None):
                    codes.add(exc.code)
                if "token_required" in codes or not token:
                    raise
                ip = ""
                request = getattr(self, "request", None)
                if request is not None:
                    ip = (request.META.get("REMOTE_ADDR") or "")[:45]
                if not consume_recovery_code(user, token, ip=ip):
                    raise
                device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
                if device is None:
                    raise forms.ValidationError(
                        self.otp_error_messages["invalid_token"],
                        code="invalid_token",
                    ) from None
                user.otp_device = device
        return cleaned_data


class TOTPConfirmForm(forms.Form):
    """Confirm an unconfirmed TOTP device with a current authenticator code."""

    otp_token = forms.CharField(
        label=_("Authentication code"),
        min_length=6,
        max_length=8,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "autofocus": True,
            }
        ),
    )


class MFAConfirmForm(forms.Form):
    """Confirm regenerate/disable with authenticator or recovery code."""

    otp_token = forms.CharField(
        label=_("Authentication or recovery code"),
        min_length=6,
        max_length=40,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "text",
                "autofocus": True,
            }
        ),
    )
