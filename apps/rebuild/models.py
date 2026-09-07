"""Publication job and machine runner models (PU-07-jobs, PRODUCT-INTERFACES-V2 §I06).

Persists idempotent publication jobs and prevents machine authentication nonce reuse.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db import models


class PublicationJobState(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"


class RemovalState(models.TextChoices):
    NOT_REQUESTED = "not_requested", "Not Requested"
    PENDING = "pending", "Pending"
    EFFECTIVE = "effective", "Effective"
    FAILED = "failed", "Failed"


class PublicationJob(models.Model):
    """Persisted publication job record representing a build/deploy operation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    idempotency_key = models.CharField(
        max_length=255, unique=True, null=True, blank=True, db_index=True
    )
    state = models.CharField(
        max_length=20,
        choices=PublicationJobState.choices,
        default=PublicationJobState.QUEUED,
        db_index=True,
    )
    locale = models.CharField(max_length=10, null=True, blank=True)
    requested_revision = models.CharField(max_length=255, blank=True, default="")
    deployed_revision = models.CharField(max_length=255, null=True, blank=True)
    affected_paths = models.JSONField(default=list, blank=True)
    revoked_paths = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "A01: detail/file URLs actually denied at the edge on archive. "
            "Never includes shared pages (home, list indexes); those are only "
            "rebuilt via affected_paths."
        ),
    )
    removal_state = models.CharField(
        max_length=20,
        choices=RemovalState.choices,
        default=RemovalState.NOT_REQUESTED,
    )
    error_code = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "publication job"
        verbose_name_plural = "publication jobs"

    def __str__(self) -> str:
        return f"PublicationJob({self.id}, state={self.state})"

    def to_dict(self) -> dict[str, Any]:
        """Format wire dictionary strictly conforming to PRODUCT-INTERFACES-V2 §I06."""
        return {
            "id": str(self.id),
            "state": self.state,
            "locale": self.locale,
            "requestedRevision": self.requested_revision,
            "deployedRevision": self.deployed_revision,
            "affectedPaths": self.affected_paths if isinstance(self.affected_paths, list) else [],
            "revokedPaths": self.revoked_paths if isinstance(self.revoked_paths, list) else [],
            "removalState": self.removal_state,
            "createdAt": self._format_dt(self.created_at),
            "startedAt": self._format_dt(self.started_at),
            "finishedAt": self._format_dt(self.finished_at),
            "errorCode": self.error_code,
            "updatedAt": self._format_dt(self.updated_at),
        }

    @staticmethod
    def _format_dt(dt) -> str | None:
        if dt is None:
            return None
        r = dt.isoformat()
        if r.endswith("+00:00"):
            r = r.removesuffix("+00:00") + "Z"
        return r


class PublicationMachineNonce(models.Model):
    """Machine authentication nonce storage for replay attack prevention."""

    nonce = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "publication machine nonce"
        verbose_name_plural = "publication machine nonces"

    def __str__(self) -> str:
        return f"Nonce({self.nonce})"
