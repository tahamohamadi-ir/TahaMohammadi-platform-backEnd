"""Models for first-party aggregate analytics without visitor identifiers (§I07)."""

from __future__ import annotations

import datetime

from django.db import models, transaction
from django.db.models import F


class AggregateEvent(models.Model):
    """Daily aggregate event counters.

    Privacy boundary:
    Stores daily aggregates only (date, locale, path, event, target, count).
    No email, message, full referrer URL, cookie ID, IP address, or persistent
    visitor identifier is ever stored.
    """

    date = models.DateField(db_index=True, help_text="Calendar date in UTC.")
    locale = models.CharField(
        max_length=10, db_index=True, help_text="Canonical locale (fa/en)."
    )
    page_path = models.CharField(
        max_length=512, db_index=True, help_text="Canonical frontend path."
    )
    event = models.CharField(max_length=64, db_index=True, help_text="Registered event type.")
    target = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="Registered action ID (never arbitrary text or URL).",
    )
    count = models.PositiveIntegerField(default=1, help_text="Count of received events.")

    class Meta:
        ordering = ["-date", "page_path", "event"]
        constraints = [
            models.UniqueConstraint(
                fields=["date", "locale", "page_path", "event", "target"],
                name="unique_daily_aggregate_event",
            )
        ]

    def __str__(self) -> str:
        return (
            f"{self.date} [{self.locale}] {self.page_path} "
            f"({self.event}:{self.target}) = {self.count}"
        )

    @classmethod
    def record_event(
        cls,
        *,
        date: datetime.date,
        locale: str,
        page_path: str,
        event: str,
        target: str = "",
    ) -> AggregateEvent:
        """Increment daily aggregate counter atomically or create initial record."""
        target = (target or "").strip()
        with transaction.atomic():
            obj, created = cls.objects.select_for_update().get_or_create(
                date=date,
                locale=locale,
                page_path=page_path,
                event=event,
                target=target,
                defaults={"count": 1},
            )
            if not created:
                obj.count = F("count") + 1
                obj.save(update_fields=["count"])
                obj.refresh_from_db(fields=["count"])
            return obj
