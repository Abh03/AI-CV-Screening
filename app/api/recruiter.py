"""Authenticated recruiter workspace, document access and human decision audit."""
import csv
import hashlib
import io
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select, func, literal
from sqlalchemy.orm import defer, undefer
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.campaigns import _owned, _jd
from app.campaigns.recruiter import PoolFilters, analytics, candidate_view, matches, review_view, sorted_pool
from app.config import settings
from app.core.auth import Principal, current_principal
from app.core.security import decrypt_bytes, encrypt_payload
from app.stage1_rules.contracts import Years
from app.models.database import (CampaignModel, CampaignCVModel, CampaignJDModel, CampaignPairModel,
    CandidateReviewModel, CandidateReviewEventModel, RecruiterViewModel, CampaignMemberModel, RecruiterUserModel, get_db)

router = APIRouter(prefix="/api/v1/recruiter", tags=["Recruiter workspace"])


def parse_filters(raw):
    try:
        return PoolFilters.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(422, "Invalid candidate filters") from exc


def validate_requirements(filters, jd):
    ids = {target["target_id"] for target in jd.job_snapshot.get("relevance_contract", {}).get("targets", [])}
    if not set(filters.requirement_ids).issubset(ids):
        raise HTTPException(422, "These requirement filters belong to a different role")


def access_scope(principal):
    shared = select(CampaignMemberModel.campaign_id).where(CampaignMemberModel.user_id == principal.id)
    return (CampaignModel.owner_id == principal.id) | CampaignModel.id.in_(shared)


async def pool_rows(db, campaign_id, jd_key=None, search="", cv_ids=None):
    term = search.strip()
    text_matches = (CampaignCVModel.candidate_id.icontains(term, autoescape=True) |
                    CampaignCVModel.source_filename.icontains(term, autoescape=True) |
                    CampaignCVModel.redacted_text.icontains(term, autoescape=True)) if term else literal(True)
    query = (select(CampaignPairModel, CampaignCVModel, CampaignJDModel, CandidateReviewModel,
                    CampaignCVModel.encrypted_original_pdf.is_not(None), text_matches)
        .join(CampaignCVModel, CampaignCVModel.id == CampaignPairModel.cv_id)
        .join(CampaignJDModel, CampaignJDModel.id == CampaignPairModel.jd_id)
        .outerjoin(CandidateReviewModel, CandidateReviewModel.pair_id == CampaignPairModel.id)
        .options(defer(CampaignCVModel.encrypted_pdf), defer(CampaignCVModel.encrypted_original_pdf),
                 defer(CampaignCVModel.source_locations), defer(CampaignCVModel.redacted_text))
        .where(CampaignPairModel.campaign_id == campaign_id))
    if jd_key is not None:
        query = query.where(CampaignJDModel.jd_key == jd_key)
    if cv_ids is not None:
        query = query.where(CampaignCVModel.id.in_(cv_ids))
    result = []
    for pair, cv, jd, review, available, matched in (await db.execute(query)).all():
        cv.search_matches = bool(matched)
        result.append((pair, cv, jd, review, available))
    return result


def project(rows, filters):
    visible, full = [], []
    for pair, cv, jd, review, available in rows:
        cv.document_available = available
        row = candidate_view(pair, cv, jd, review)
        full.append(row)
        searchable = filters.search if cv.search_matches else ""
        if matches(row, filters, searchable):
            visible.append(row)
    return sorted_pool(visible, filters), full


@router.get("/campaigns/{campaign_id}/roles/{jd_key}/pool")
async def candidate_pool(campaign_id: str, jd_key: str, filters: str = Query("{}", max_length=12000),
                         limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0),
                         db: AsyncSession = Depends(get_db), principal: Principal = Depends(current_principal)):
    campaign = await _owned(db, campaign_id, principal)
    jd = await _jd(db, campaign.id, jd_key)
    parsed = parse_filters(filters)
    validate_requirements(parsed, jd)
    selected, full = project(await pool_rows(db, campaign.id, jd_key, parsed.search), parsed)
    page = selected[offset:offset + limit]
    if parsed.search.strip() and page:
        texts = (await db.execute(select(CampaignCVModel.candidate_id, CampaignCVModel.redacted_text).where(
            CampaignCVModel.campaign_id == campaign.id,
            CampaignCVModel.candidate_id.in_([row["candidate_id"] for row in page])))).all()
        excerpts = {}
        for identifier, text in texts:
            text = text or ""
            index = text.casefold().find(parsed.search.strip().casefold())
            excerpts[identifier] = text[max(0, index - 80):index + 180] if index >= 0 else ""
        for row in page:
            row["search_excerpt"] = excerpts.get(row["candidate_id"], "")
    return {"campaign_id": campaign.id, "campaign_name": campaign.name, "jd_key": jd_key,
            "role_title": jd.job_snapshot.get("title", jd_key), "jd_status": jd.status,
            "stage3_cap": jd.stage3_cap, "requirements": jd.job_snapshot.get("relevance_contract", {}).get("targets", []),
            "total": len(selected), "limit": limit, "offset": offset, "results": page,
            "analytics": analytics(full, jd), "filtered_analytics": analytics(selected, jd),
            "scoring_policy": jd.policy_snapshot.get("scoring", jd.policy_snapshot)}


@router.get("/candidates")
async def find_candidates(search: str = Query("", max_length=255), campaign_id: str = Query("", max_length=64),
                          limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0),
                          db: AsyncSession = Depends(get_db), principal: Principal = Depends(current_principal)):
    # Narrow by identity first; this search includes every processing stage and all accessible campaigns.
    query = select(CampaignCVModel.id, CampaignCVModel.campaign_id).join(CampaignModel).where(access_scope(principal))
    if campaign_id:
        query = query.where(CampaignModel.id == campaign_id)
    if search.strip():
        query = query.where(CampaignCVModel.source_filename.icontains(search.strip(), autoescape=True) |
                            CampaignCVModel.candidate_id.icontains(search.strip(), autoescape=True) |
                            CampaignCVModel.redacted_text.icontains(search.strip(), autoescape=True))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    chosen = (await db.execute(query.order_by(CampaignCVModel.source_filename, CampaignCVModel.id).limit(limit).offset(offset))).all()
    found = []
    for scope in dict.fromkeys(campaign for _, campaign in chosen):
        ids = {identifier for identifier, campaign in chosen if campaign == scope}
        campaign = await db.get(CampaignModel, scope)
        rows = await pool_rows(db, scope, cv_ids=ids)
        for pair, cv, jd, review, available in rows:
            if cv.id not in ids:
                continue
            cv.document_available = available
            row = candidate_view(pair, cv, jd, review)
            row["campaign_name"] = campaign.name
            found.append(row)
    # Intake-rejected files have no candidate record, but recruiters still need their outcome.
    scope_query = select(CampaignModel).where(access_scope(principal))
    if campaign_id:
        scope_query = scope_query.where(CampaignModel.id == campaign_id)
    rejected = []
    for campaign in (await db.execute(scope_query.order_by(CampaignModel.created_at.desc(), CampaignModel.id))).scalars():
        for entry in (campaign.intake_report or {}).get("rejected", []):
            if search.strip().casefold() in entry.get("name", "").casefold():
                rejected.append({"campaign_id": campaign.id, "campaign_name": campaign.name,
                                 "filename": entry.get("name", ""), "reason": entry.get("code", "INTAKE_REJECTED")})
    return {"total": total, "limit": limit, "offset": offset, "results": found,
            "intake_rejection_total": len(rejected), "intake_rejections": rejected[offset:offset + limit]}


async def owned_candidate(db, campaign_id, candidate_id, principal, load_pdf=False):
    await _owned(db, campaign_id, principal)
    options = [defer(CampaignCVModel.encrypted_pdf)]
    if load_pdf:
        options.append(undefer(CampaignCVModel.encrypted_original_pdf))
    cv = (await db.execute(select(CampaignCVModel).options(*options).where(CampaignCVModel.campaign_id == campaign_id,
        CampaignCVModel.candidate_id == candidate_id))).scalar_one_or_none()
    if cv is None:
        raise HTTPException(404, "Candidate not found")
    return cv


@router.get("/campaigns/{campaign_id}/candidates/{candidate_id}/document")
async def candidate_document(campaign_id: str, candidate_id: str, db: AsyncSession = Depends(get_db),
                             principal: Principal = Depends(current_principal)):
    cv = await owned_candidate(db, campaign_id, candidate_id, principal, load_pdf=True)
    if cv.encrypted_original_pdf is None:
        raise HTTPException(404, "Original PDF is unavailable; use the extracted CV or restore the matching original")
    try:
        pdf = decrypt_bytes(cv.encrypted_original_pdf)
    except Exception as exc:
        raise HTTPException(503, "CV document cannot be decrypted") from exc
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": 'inline; filename="candidate-cv.pdf"',
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.put("/campaigns/{campaign_id}/candidates/{candidate_id}/document")
async def restore_document(campaign_id: str, candidate_id: str, request: Request,
                           db: AsyncSession = Depends(get_db), principal: Principal = Depends(current_principal)):
    cv = await owned_candidate(db, campaign_id, candidate_id, principal)
    if request.headers.get("content-type", "").split(";")[0] != "application/pdf":
        raise HTTPException(415, "Expected application/pdf")
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > settings.PDF_MAX_BYTES:
            raise HTTPException(413, "PDF exceeds size limit")
        payload.extend(chunk)
    if not payload.startswith(b"%PDF-") or hashlib.sha256(payload).hexdigest() != cv.content_hash:
        raise HTTPException(422, "This file does not match the candidate's original CV")
    cv.encrypted_original_pdf = encrypt_payload(bytes(payload))
    await db.commit()
    return {"available": True}


@router.get("/campaigns/{campaign_id}/candidates/{candidate_id}/cv")
async def extracted_cv(campaign_id: str, candidate_id: str, db: AsyncSession = Depends(get_db),
                       principal: Principal = Depends(current_principal)):
    cv = await owned_candidate(db, campaign_id, candidate_id, principal)
    available = await db.scalar(select(CampaignCVModel.encrypted_original_pdf.is_not(None)).where(CampaignCVModel.id == cv.id))
    return {"candidate_id": cv.candidate_id, "filename": cv.source_filename,
            "original_available": available, "pages": cv.source_locations or [],
            "text": cv.redacted_text or "", "stage0_status": cv.stage0_status,
            "extraction_error": cv.extraction_error_code}


async def owned_pair(db, pair_id, principal, lock=False):
    query = select(CampaignPairModel).where(CampaignPairModel.id == pair_id)
    if lock:
        query = query.with_for_update()
    pair = (await db.execute(query)).scalar_one_or_none()
    if pair is None:
        raise HTTPException(404, "Candidate assessment not found")
    await _owned(db, pair.campaign_id, principal)
    return pair


@router.get("/pairs/{pair_id}")
async def candidate_detail(pair_id: str, db: AsyncSession = Depends(get_db), principal: Principal = Depends(current_principal)):
    pair = await owned_pair(db, pair_id, principal)
    cv = await db.get(CampaignCVModel, pair.cv_id)
    jd = await db.get(CampaignJDModel, pair.jd_id)
    cv.document_available = await db.scalar(select(CampaignCVModel.encrypted_original_pdf.is_not(None)).where(CampaignCVModel.id == cv.id))
    result = candidate_view(pair, cv, jd, await db.get(CandidateReviewModel, pair.id), detail=True)
    events = (await db.execute(select(CandidateReviewEventModel).where(CandidateReviewEventModel.pair_id == pair.id)
                              .order_by(CandidateReviewEventModel.created_at.desc(), CandidateReviewEventModel.id))).scalars().all()
    result["history"] = [{"actor_id": e.actor_id, "created_at": e.created_at, "snapshot": e.snapshot} for e in events]
    result["other_roles"] = []
    for other, source, role, review, available in await pool_rows(db, pair.campaign_id, cv_ids={pair.cv_id}):
        if other.cv_id == pair.cv_id and other.id != pair.id:
            result["other_roles"].append({"pair_id": other.id, "jd_key": role.jd_key,
                "role_title": role.job_snapshot.get("title"), "status": other.status, "score": other.composite_score})
    result["scoring_policy"] = jd.policy_snapshot
    return result


class VerifiedFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experience_years: Years | None = None
    work_authorized: Literal["unknown", "eligible", "ineligible"] = "unknown"
    education_meets_requirement: bool | None = None


class ReviewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pair_versions: dict[str, int] = Field(min_length=1, max_length=100)
    decision: Literal["UNREVIEWED", "SHORTLIST", "HOLD", "NOT_PROCEEDING"]
    notes: str | None = Field(default=None, max_length=10000)
    reason: str = Field(default="", max_length=2000)
    tags: list[str] | None = Field(default=None, max_length=20)
    assignee_id: str | None = Field(default=None, max_length=64)
    verified_facts: VerifiedFacts | None = None

    @field_validator("pair_versions")
    @classmethod
    def versions(cls, value):
        if any(not key or len(key) > 64 or version < 0 for key, version in value.items()):
            raise ValueError("Invalid assessment version")
        return value

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, value):
        if value is None:
            return value
        if any(not tag.strip() or len(tag) > 50 for tag in value):
            raise ValueError("Tags must contain 1–50 characters")
        return list(dict.fromkeys(tag.strip() for tag in value))


@router.patch("/reviews")
async def update_reviews(payload: ReviewUpdate, db: AsyncSession = Depends(get_db),
                         principal: Principal = Depends(current_principal)):
    if payload.decision == "NOT_PROCEEDING" and not payload.reason.strip():
        raise HTTPException(422, "A reason is required when choosing not to proceed")
    updated = []
    # Pair locks serialize first review creation and subsequent version checks. All-or-nothing batch.
    for pair_id, expected in sorted(payload.pair_versions.items()):
        pair = await owned_pair(db, pair_id, principal, lock=True)
        review = await db.get(CandidateReviewModel, pair.id)
        if (review.version if review else 0) != expected:
            raise HTTPException(409, "A candidate decision changed; refresh before saving")
        if payload.assignee_id:
            campaign = await db.get(CampaignModel, pair.campaign_id)
            member = await db.get(CampaignMemberModel, (pair.campaign_id, payload.assignee_id))
            if payload.assignee_id != campaign.owner_id and member is None:
                raise HTTPException(422, "Assignee must belong to this campaign")
        if review is None:
            review = CandidateReviewModel(pair_id=pair.id, notes="", reason="", tags=[], verified_facts={}, version=0)
            db.add(review)
        review.decision = payload.decision
        review.reason = payload.reason.strip()
        if payload.notes is not None:
            review.notes = payload.notes
        if payload.tags is not None:
            review.tags = payload.tags
        if payload.verified_facts is not None:
            review.verified_facts = payload.verified_facts.model_dump()
        if "assignee_id" in payload.model_fields_set:
            review.assignee_id = payload.assignee_id or None
        review.version += 1
        review.reviewer_id = principal.id
        review.updated_at = datetime.now(timezone.utc)
        snapshot = review_view(review)
        snapshot["updated_at"] = review.updated_at.isoformat()
        db.add(CandidateReviewEventModel(id=str(uuid4()), pair_id=pair.id, actor_id=principal.id,
                                         snapshot=snapshot, created_at=review.updated_at))
        updated.append({"pair_id": pair.id, "review": review_view(review)})
    await db.commit()
    return {"results": updated}


class SavedView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    filters: PoolFilters
    columns: list[Literal["requirements", "experience", "strengths", "questions"]] = Field(default_factory=list, max_length=4)


@router.get("/views")
async def list_views(db: AsyncSession = Depends(get_db), principal: Principal = Depends(current_principal)):
    rows = (await db.execute(select(RecruiterViewModel).where(RecruiterViewModel.owner_id == principal.id)
                            .order_by(RecruiterViewModel.name, RecruiterViewModel.id))).scalars()
    return [{"id": row.id, "name": row.name, "filters": row.filters, "columns": row.columns} for row in rows]


@router.post("/views", status_code=201)
async def save_view(payload: SavedView, db: AsyncSession = Depends(get_db), principal: Principal = Depends(current_principal)):
    if not payload.name.strip():
        raise HTTPException(422, "Name must not be blank")
    view = RecruiterViewModel(id=str(uuid4()), owner_id=principal.id, name=payload.name.strip(), filters=payload.filters.model_dump(), columns=payload.columns)
    db.add(view)
    await db.commit()
    return {"id": view.id, "name": view.name, "filters": view.filters, "columns": view.columns}


@router.delete("/views/{view_id}", status_code=204)
async def delete_view(view_id: str, db: AsyncSession = Depends(get_db), principal: Principal = Depends(current_principal)):
    row = await db.get(RecruiterViewModel, view_id)
    if row is None or row.owner_id != principal.id:
        raise HTTPException(404, "Saved view not found")
    await db.delete(row)
    await db.commit()


@router.get("/campaigns/{campaign_id}/members")
async def campaign_members(campaign_id: str, db: AsyncSession = Depends(get_db), principal: Principal = Depends(current_principal)):
    campaign = await _owned(db, campaign_id, principal)
    owner = await db.get(RecruiterUserModel, campaign.owner_id)
    rows = (await db.execute(select(CampaignMemberModel).where(CampaignMemberModel.campaign_id == campaign.id))).scalars()
    return {"can_manage": campaign.owner_id == principal.id or principal.role == "admin", "members":
            [{"id": campaign.owner_id, "username": owner.username if owner else campaign.owner_id, "owner": True}] +
            [{"id": row.user_id, "username": row.username, "owner": False} for row in rows]}


class MemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)


@router.post("/campaigns/{campaign_id}/members")
async def add_member(campaign_id: str, payload: MemberInput, db: AsyncSession = Depends(get_db), principal: Principal = Depends(current_principal)):
    campaign = await _owned(db, campaign_id, principal)
    if campaign.owner_id != principal.id and principal.role != "admin":
        raise HTTPException(403, "Only the campaign owner can manage reviewers")
    user = (await db.execute(select(RecruiterUserModel).where(RecruiterUserModel.username == payload.username,
                                                             RecruiterUserModel.is_active.is_(True)))).scalar_one_or_none()
    if user is None:
        raise HTTPException(404, "Active reviewer not found")
    if user.id != campaign.owner_id and await db.get(CampaignMemberModel, (campaign.id, user.id)) is None:
        db.add(CampaignMemberModel(campaign_id=campaign.id, user_id=user.id, username=user.username))
        await db.commit()
    return {"id": user.id, "username": user.username}


@router.delete("/campaigns/{campaign_id}/members/{user_id}", status_code=204)
async def remove_member(campaign_id: str, user_id: str, db: AsyncSession = Depends(get_db), principal: Principal = Depends(current_principal)):
    campaign = await _owned(db, campaign_id, principal)
    if campaign.owner_id != principal.id and principal.role != "admin":
        raise HTTPException(403, "Only the campaign owner can manage reviewers")
    member = await db.get(CampaignMemberModel, (campaign.id, user_id))
    if member:
        await db.delete(member)
        await db.commit()


def spreadsheet_cell(value):
    text = "" if value is None else str(value)
    # Prevent spreadsheet formula execution in candidate-controlled text and notes.
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


@router.get("/campaigns/{campaign_id}/roles/{jd_key}/export")
async def export_pool(campaign_id: str, jd_key: str, filters: str = Query("{}", max_length=12000),
                      format: Literal["csv", "json"] = "csv", db: AsyncSession = Depends(get_db),
                      principal: Principal = Depends(current_principal)):
    await _owned(db, campaign_id, principal)
    jd = await _jd(db, campaign_id, jd_key)
    parsed = parse_filters(filters)
    validate_requirements(parsed, jd)
    selected, _ = project(await pool_rows(db, campaign_id, jd_key, parsed.search), parsed)
    if format == "json":
        # An explicit portable handoff for recruiting systems, without vendor credentials or network writes.
        return {"schema_version": "recruiter-handoff-v1", "campaign_id": campaign_id, "jd_key": jd_key, "candidates": selected}
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["Candidate", "Candidate ID", "Role", "Screening outcome", "Fit score", "Recruiter decision",
                     "Fit summary", "Requirement evidence", "Open questions", "Decision reason", "Notes", "Tags", "Reviewer", "Reviewed at"])
    for row in selected:
        writer.writerow([spreadsheet_cell(v) for v in [row["source_filename"], row["candidate_id"], row["role_title"],
            row["status"], row["score"], row["review"]["decision"], row["summary"],
            "; ".join(f'{target.get("target_text", target["target_id"])}: {target.get("status")} — {target.get("supporting_text", "")}' for target in row["stage2_target_assessments"]),
            "; ".join(str(v) for v in row["review_reasons"] + row["verification_reasons"]),
            row["review"]["reason"], row["review"]["notes"], ", ".join(row["review"]["tags"]),
            row["review"]["reviewer_id"], row["review"]["updated_at"]]])
    return Response(buffer.getvalue().encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="candidate-shortlist.csv"'})
