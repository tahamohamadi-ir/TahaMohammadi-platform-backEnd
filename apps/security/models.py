"""Audit log + MFA recovery code model (P3 admin-security)."""

from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=64)
    model_name = models.CharField(max_length=64, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    ip = models.CharField(max_length=45, blank=True)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Audit log"
        verbose_name_plural = "Audit logs"

    def __str__(self) -> str:
        return f"{self.action} ({self.created_at:%Y-%m-%d %H:%M:%S})"


class RecoveryCode(models.Model):
    """Hashed one-time MFA recovery token; plaintext exists only at issue time."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recovery_codes",
    )
    code_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "used_at"]),
        ]

    def __str__(self) -> str:
        state = "used" if self.used_at else "unused"
        return f"RecoveryCode({self.user_id}, {state})"
