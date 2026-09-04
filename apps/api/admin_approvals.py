"""Owner approval queue surface (ADMIN-270 / BACKEND-210).

Read-only projection of ``ContentSeedRecord`` — the per-``content_id`` rows
imported from the owner seed package (BACKEND-070). The queue is the seed
package's publication gate made inspectable: every record carries the owner
set ``approval_state`` / ``publication_state`` / ``visibility`` triple and the
derived three-part ``isPublicationAllowed`` gate (never auto-satisfied).

No mutation endpoint exists here by design: approving content is an owner
decision recorded in the seed package (or a future owner-facing workflow),
not a staff UI toggle.
"""

from __future__ import annotations

from ninja import Router, Schema

from apps.api.admin_common import _require_admin_otp
from apps.content.models import ContentSeedRecord

approvals_router = Router()

VALID_STATE_FILTERS = ("all", "approved", "not-approved")


class ApprovalQueueItemOut(Schema):
    """One seed row as the admin approval queue shows it."""

    contentId: str
    contentType: str
    locale: str
    slug: str
    title: str
    approvalState: str
    publicationState: str
    visibility: str
    isPublicationAllowed: bool


class ApprovalQueueCountsOut(Schema):
    """Counts over every seed record, independent of the ``state`` filter."""

    total: int
    approved: int
    notApproved: int


class ApprovalQueueOut(Schema):
    items: list[ApprovalQueueItemOut]
    counts: ApprovalQueueCountsOut


def _item_out(record: ContentSeedRecord) -> ApprovalQueueItemOut:
    return ApprovalQueueItemOut(
        contentId=record.content_id,
        contentType=record.content_type,
        locale=record.locale,
        slug=record.slug,
        title=record.title,
        approvalState=record.approval_state,
        publicationState=record.publication_state,
        visibility=record.visibility,
        isPublicationAllowed=record.is_publication_allowed,
    )


@approvals_router.get(
    "",
    response=ApprovalQueueOut,
    summary="Owner approval queue from the imported seed records.",
)
def approval_queue(request, state: str = "not-approved"):
    _require_admin_otp(request)
    if state not in VALID_STATE_FILTERS:
        from apps.api.admin_common import AdminError

        raise AdminError(
            400,
            "VALIDATION",
            "Invalid state. Expected one of: all, approved, not-approved.",
        )

    rows = ContentSeedRecord.objects.all().order_by("content_id")
    total = rows.count()
    approved = rows.filter(approval_state="approved").count()
    if state == "approved":
        rows = rows.filter(approval_state="approved")
    elif state == "not-approved":
        rows = rows.exclude(approval_state="approved")

    return ApprovalQueueOut(
        items=[_item_out(record) for record in rows],
        counts=ApprovalQueueCountsOut(
            total=total, approved=approved, notApproved=total - approved
        ),
    )
