"""Admin API endpoints for project case-study, evidence, collaborators, and funding.

Contract: Docs/03-contracts/PRODUCT-INTERFACES-V2.md §I03
Packet: Docs/05-delivery/concept-alignment-v2/product-packets/PU-04-project-evidence.md
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from ninja import Field, Router, Schema
from pydantic import model_validator

from apps.api.admin_common import (
    AdminError,
    _check_csrf,
    _client_ip,
    _require_admin_otp,
)
from apps.content.models import (
    CaseStudyDepth,
    EvidenceVisibility,
    Project,
    ProjectCaseStudyDetails,
    ProjectCollaborator,
    ProjectEvidence,
    ProjectFunding,
)
from apps.content.revisions import create_revision
from apps.security.models import AuditLog


class CaseStudyDetailsIn(Schema):
    """Case study narrative extension fields."""

    depth: str = "standard"
    problem: str = ""
    constraints: str = ""
    technical_decisions: str = ""
    trade_offs: str = ""
    outcomes_summary: str = ""
    lessons_learned: str = ""
    testing_summary: str = ""

    @model_validator(mode="before")
    @classmethod
    def accept_camel_case(cls, data: Any) -> Any:
        if isinstance(data, dict):
            mapping = {
                "technicalDecisions": "technical_decisions",
                "tradeOffs": "trade_offs",
                "outcomesSummary": "outcomes_summary",
                "lessonsLearned": "lessons_learned",
                "testingSummary": "testing_summary",
            }
            for camel, snake in mapping.items():
                if camel in data and snake not in data:
                    data[snake] = data[camel]
        return data


class CaseStudyEvidenceIn(Schema):
    """Evidence update row; id is optional for new rows."""

    id: int | None = None
    label: str
    value: str = ""
    source: str = ""
    last_verified: date | None = None
    visibility: str = "internal"

    @model_validator(mode="before")
    @classmethod
    def accept_camel_case(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "lastVerified" in data and "last_verified" not in data:
                data["last_verified"] = data["lastVerified"]
        return data


class CaseStudyCollaboratorIn(Schema):
    """Collaborator credit update row; id is optional for new rows."""

    id: int | None = None
    name: str
    role: str = ""
    publication_approved: bool = False

    @model_validator(mode="before")
    @classmethod
    def accept_camel_case(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "publicationApproved" in data and "publication_approved" not in data:
                data["publication_approved"] = data["publicationApproved"]
        return data


class CaseStudyFundingIn(Schema):
    """Funding disclosure update row; id is optional for new rows."""

    id: int | None = None
    funder: str
    grant_id: str = ""
    publication_approved: bool = False

    @model_validator(mode="before")
    @classmethod
    def accept_camel_case(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "grantId" in data and "grant_id" not in data:
                data["grant_id"] = data["grantId"]
            if "publicationApproved" in data and "publication_approved" not in data:
                data["publication_approved"] = data["publicationApproved"]
        return data


class ProjectCaseStudyIn(Schema):
    """Full-aggregate case-study payload for atomic PUT."""

    details: CaseStudyDetailsIn | None = None
    evidence: list[CaseStudyEvidenceIn] = Field(default_factory=list)
    collaborators: list[CaseStudyCollaboratorIn] = Field(default_factory=list)
    funding: list[CaseStudyFundingIn] = Field(default_factory=list)


class CaseStudyDetailsOut(Schema):
    depth: str = "standard"
    problem: str = ""
    constraints: str = ""
    technical_decisions: str = ""
    trade_offs: str = ""
    outcomes_summary: str = ""
    lessons_learned: str = ""
    testing_summary: str = ""


class CaseStudyEvidenceOut(Schema):
    id: int
    label: str
    value: str = ""
    source: str = ""
    last_verified: date | None = None
    visibility: str = "internal"


class CaseStudyCollaboratorOut(Schema):
    id: int
    name: str
    role: str = ""
    publication_approved: bool = False


class CaseStudyFundingOut(Schema):
    id: int
    funder: str
    grant_id: str = ""
    publication_approved: bool = False


class ProjectCaseStudyOut(Schema):
    projectId: int
    details: CaseStudyDetailsOut | None = None
    evidence: list[CaseStudyEvidenceOut] = Field(default_factory=list)
    collaborators: list[CaseStudyCollaboratorOut] = Field(default_factory=list)
    funding: list[CaseStudyFundingOut] = Field(default_factory=list)
    updatedAt: str


def _serialize_project_case_study(project: Project) -> ProjectCaseStudyOut:
    from apps.api.admin_content import _serialize_updated_at

    details: CaseStudyDetailsOut | None = None
    if hasattr(project, "case_study") and project.case_study:
        cs = project.case_study
        details = CaseStudyDetailsOut(
            depth=cs.depth,
            problem=cs.problem,
            constraints=cs.constraints,
            technical_decisions=cs.technical_decisions,
            trade_offs=cs.trade_offs,
            outcomes_summary=cs.outcomes_summary,
            lessons_learned=cs.lessons_learned,
            testing_summary=cs.testing_summary,
        )

    evidence = [
        CaseStudyEvidenceOut(
            id=row.pk,
            label=row.label,
            value=row.value,
            source=row.source,
            last_verified=row.last_verified,
            visibility=row.visibility,
        )
        for row in project.evidence_items.all().order_by("id")
    ]

    collaborators = [
        CaseStudyCollaboratorOut(
            id=row.pk,
            name=row.name,
            role=row.role,
            publication_approved=row.publication_approved,
        )
        for row in project.collaborators.all().order_by("id")
    ]

    funding = [
        CaseStudyFundingOut(
            id=row.pk,
            funder=row.funder,
            grant_id=row.grant_id,
            publication_approved=row.publication_approved,
        )
        for row in project.funding_items.all().order_by("id")
    ]

    return ProjectCaseStudyOut(
        projectId=project.pk,
        details=details,
        evidence=evidence,
        collaborators=collaborators,
        funding=funding,
        updatedAt=_serialize_updated_at(project.updated_at),
    )


def register_project_case_study_endpoints(router: Router) -> None:
    """Register GET/PUT /project/{id}/case-study on the given content router."""
    from apps.api.admin_content import (
        DETAIL_FIELD_MAPS,
        AdminConflictError,
        _if_match_matches,
        _serialize_updated_at,
    )

    @router.get(
        "/project/{id}/case-study",
        response=ProjectCaseStudyOut,
        summary="Get project case study, evidence, collaborators, and funding.",
    )
    def get_project_case_study(request, id: int) -> ProjectCaseStudyOut:
        _require_admin_otp(request)
        project = Project.objects.filter(pk=id).first()
        if project is None:
            raise AdminError(404, "NOT_FOUND", "Project not found.")
        return _serialize_project_case_study(project)

    @router.put(
        "/project/{id}/case-study",
        response=ProjectCaseStudyOut,
        summary="Atomically update project case study, evidence, collaborators, and funding.",
    )
    def put_project_case_study(
        request, id: int, payload: ProjectCaseStudyIn
    ) -> ProjectCaseStudyOut:
        _require_admin_otp(request)
        _check_csrf(request)

        with transaction.atomic():
            project = Project.objects.select_for_update().filter(pk=id).first()
            if project is None:
                raise AdminError(404, "NOT_FOUND", "Project not found.")

            if not _if_match_matches(request.headers.get("If-Match"), project):
                raise AdminConflictError(_serialize_updated_at(project.updated_at))

            # 1. Validation phase
            if payload.details is not None:
                if payload.details.depth not in CaseStudyDepth.values:
                    raise AdminError(
                        400,
                        "VALIDATION",
                        f"Invalid depth '{payload.details.depth}'.",
                        fields={"fields": ["details.depth"]},
                    )

            for ev in payload.evidence:
                if ev.visibility not in EvidenceVisibility.values:
                    raise AdminError(
                        400,
                        "VALIDATION",
                        f"Invalid visibility '{ev.visibility}'.",
                        fields={"fields": ["evidence"]},
                    )
                if not (ev.label or "").strip():
                    raise AdminError(
                        400,
                        "VALIDATION",
                        "Evidence label must not be empty.",
                        fields={"fields": ["evidence"]},
                    )
                if ev.id is not None:
                    row_ev = ProjectEvidence.objects.filter(pk=ev.id).first()
                    if row_ev is None:
                        raise AdminError(
                            400,
                            "VALIDATION",
                            f"Evidence {ev.id} does not exist.",
                            fields={"fields": ["evidence"]},
                        )
                    if row_ev.project_id != project.pk:
                        raise AdminError(
                            400,
                            "VALIDATION",
                            f"Evidence {ev.id} belongs to another project.",
                            fields={"fields": ["evidence"]},
                        )

            for c in payload.collaborators:
                if not (c.name or "").strip():
                    raise AdminError(
                        400,
                        "VALIDATION",
                        "Collaborator name must not be empty.",
                        fields={"fields": ["collaborators"]},
                    )
                if c.id is not None:
                    row_c = ProjectCollaborator.objects.filter(pk=c.id).first()
                    if row_c is None:
                        raise AdminError(
                            400,
                            "VALIDATION",
                            f"Collaborator {c.id} does not exist.",
                            fields={"fields": ["collaborators"]},
                        )
                    if row_c.project_id != project.pk:
                        raise AdminError(
                            400,
                            "VALIDATION",
                            f"Collaborator {c.id} belongs to another project.",
                            fields={"fields": ["collaborators"]},
                        )

            for f in payload.funding:
                if not (f.funder or "").strip():
                    raise AdminError(
                        400,
                        "VALIDATION",
                        "Funding funder must not be empty.",
                        fields={"fields": ["funding"]},
                    )
                if f.id is not None:
                    row_f = ProjectFunding.objects.filter(pk=f.id).first()
                    if row_f is None:
                        raise AdminError(
                            400,
                            "VALIDATION",
                            f"Funding {f.id} does not exist.",
                            fields={"fields": ["funding"]},
                        )
                    if row_f.project_id != project.pk:
                        raise AdminError(
                            400,
                            "VALIDATION",
                            f"Funding {f.id} belongs to another project.",
                            fields={"fields": ["funding"]},
                        )

            # 2. Mutate case-study details
            cs_row: ProjectCaseStudyDetails | None = None
            if payload.details is not None:
                cs_row, _ = ProjectCaseStudyDetails.objects.get_or_create(project=project)
                cs_row.depth = payload.details.depth
                cs_row.problem = payload.details.problem
                cs_row.constraints = payload.details.constraints
                cs_row.technical_decisions = payload.details.technical_decisions
                cs_row.trade_offs = payload.details.trade_offs
                cs_row.outcomes_summary = payload.details.outcomes_summary
                cs_row.lessons_learned = payload.details.lessons_learned
                cs_row.testing_summary = payload.details.testing_summary
                try:
                    cs_row.save()
                except ValidationError as err:
                    msg = (
                        str(err.message_dict)
                        if hasattr(err, "message_dict")
                        else str(err.messages)
                    )
                    raise AdminError(400, "VALIDATION", msg) from None

            # 3. Mutate evidence rows
            kept_ev_ids: set[int] = set()
            for ev in payload.evidence:
                if ev.id is not None:
                    row_ev = ProjectEvidence.objects.get(pk=ev.id, project=project)
                    row_ev.label = ev.label.strip()
                    row_ev.value = ev.value
                    row_ev.source = ev.source
                    row_ev.last_verified = ev.last_verified
                    row_ev.visibility = ev.visibility
                    row_ev.save()
                    kept_ev_ids.add(row_ev.pk)
                else:
                    new_ev = ProjectEvidence.objects.create(
                        project=project,
                        label=ev.label.strip(),
                        value=ev.value,
                        source=ev.source,
                        last_verified=ev.last_verified,
                        visibility=ev.visibility,
                    )
                    kept_ev_ids.add(new_ev.pk)
            project.evidence_items.exclude(pk__in=kept_ev_ids).delete()

            # 4. Mutate collaborator rows
            kept_collab_ids: set[int] = set()
            for c in payload.collaborators:
                if c.id is not None:
                    row_c = ProjectCollaborator.objects.get(pk=c.id, project=project)
                    row_c.name = c.name.strip()
                    row_c.role = c.role.strip()
                    row_c.publication_approved = c.publication_approved
                    row_c.save()
                    kept_collab_ids.add(row_c.pk)
                else:
                    new_c = ProjectCollaborator.objects.create(
                        project=project,
                        name=c.name.strip(),
                        role=c.role.strip(),
                        publication_approved=c.publication_approved,
                    )
                    kept_collab_ids.add(new_c.pk)
            project.collaborators.exclude(pk__in=kept_collab_ids).delete()

            # 5. Mutate funding rows
            kept_funding_ids: set[int] = set()
            for f in payload.funding:
                if f.id is not None:
                    row_f = ProjectFunding.objects.get(pk=f.id, project=project)
                    row_f.funder = f.funder.strip()
                    row_f.grant_id = f.grant_id.strip()
                    row_f.publication_approved = f.publication_approved
                    row_f.save()
                    kept_funding_ids.add(row_f.pk)
                else:
                    new_f = ProjectFunding.objects.create(
                        project=project,
                        funder=f.funder.strip(),
                        grant_id=f.grant_id.strip(),
                        publication_approved=f.publication_approved,
                    )
                    kept_funding_ids.add(new_f.pk)
            project.funding_items.exclude(pk__in=kept_funding_ids).delete()

            # 6. Update project timestamp
            project.updated_at = timezone.now()
            project.save(update_fields=["updated_at"])

            # 7. Audit and revision snapshot
            rev = create_revision(
                entity_key="project",
                item=project,
                field_attrs=DETAIL_FIELD_MAPS["project"],
                user=request.user,
                note="Update case study, evidence, collaborators, and funding",
            )
            rev.snapshot["case_study"] = {
                "details": (
                    {
                        "depth": cs_row.depth,
                        "problem": cs_row.problem,
                        "constraints": cs_row.constraints,
                        "technical_decisions": cs_row.technical_decisions,
                        "trade_offs": cs_row.trade_offs,
                        "outcomes_summary": cs_row.outcomes_summary,
                        "lessons_learned": cs_row.lessons_learned,
                        "testing_summary": cs_row.testing_summary,
                    }
                    if cs_row
                    else None
                ),
                "evidence": list(
                    project.evidence_items.values(
                        "id", "label", "value", "source", "last_verified", "visibility"
                    )
                ),
                "collaborators": list(
                    project.collaborators.values("id", "name", "role", "publication_approved")
                ),
                "funding": list(
                    project.funding_items.values(
                        "id", "funder", "grant_id", "publication_approved"
                    )
                ),
            }
            rev.save(update_fields=["snapshot"])

            AuditLog.objects.create(
                user=request.user,
                action="project.case_study.update",
                model_name="project",
                object_id=str(project.pk),
                ip=_client_ip(request),
                detail=(
                    f"revision_id={rev.pk}; evidence={len(kept_ev_ids)}; "
                    f"collaborators={len(kept_collab_ids)}; funding={len(kept_funding_ids)}"
                ),
            )

            return _serialize_project_case_study(project)
