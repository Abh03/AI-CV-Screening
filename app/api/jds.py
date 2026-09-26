"""Owner-scoped, upload-first JD review and immutable approval."""
import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import Principal, current_principal
from app.jd_intake import ExtractedJD, extract_profile
from app.models.database import JDDraftModel, ApprovedJDModel, get_db
from app.stage0_extraction.pipeline import ingest_pdf

router = APIRouter(prefix="/api/v1/jds", tags=["JD intake"])


def processing_stale(draft):
    started = draft.provenance.get("processing_started_at")
    if not started:
        return False
    budget = settings.PDF_OCR_MAX_PAGES * settings.PDF_OCR_TIMEOUT_SECONDS + settings.PROVIDER_TIMEOUT_SECONDS + 120
    return datetime.fromisoformat(started) + timedelta(seconds=budget) < datetime.now(timezone.utc)


def view(draft):
    return {"draft_id": draft.id, "status": draft.status, "pages": draft.pages,
            "profile": draft.profile, "error_code": draft.error_code,
            "expires_at": draft.expires_at, "provenance": draft.provenance}


async def owned_draft(db, draft_id, principal, *, lock=False):
    query = select(JDDraftModel).where(JDDraftModel.id == draft_id,
                                     JDDraftModel.owner_id == principal.id)
    if lock:
        query = query.with_for_update()
    draft = (await db.execute(query)).scalar_one_or_none()
    if draft is None:
        raise HTTPException(404, "JD draft not found")
    if draft.status == "PROCESSING" and processing_stale(draft):
        draft.status, draft.error_code = "FAILED", "JD_EXTRACTION_INTERRUPTED"
        await db.commit()
    if draft.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc) and draft.status != "APPROVED":
        draft.pages = []
        draft.profile = None
        draft.status = "EXPIRED"
        await db.commit()
        raise HTTPException(410, "Draft expired; upload the PDF again")
    return draft


@router.post("/extract")
async def upload_jd(request: Request, retry: bool = False,
                    db: AsyncSession = Depends(get_db), principal: Principal = Depends(current_principal)):
    if request.headers.get("content-type", "").split(";")[0].lower() != "application/pdf":
        raise HTTPException(415, "Expected application/pdf")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > settings.PDF_MAX_BYTES:
            raise HTTPException(413, "JD PDF exceeds size limit")
        data.extend(chunk)
    digest = hashlib.sha256(data).hexdigest()
    draft = (await db.execute(select(JDDraftModel).where(
        JDDraftModel.owner_id == principal.id, JDDraftModel.pdf_hash == digest).with_for_update())).scalar_one_or_none()
    if draft:
        expired = draft.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc)
        if draft.status in {"REVIEW", "APPROVED"} and (not expired or draft.status == "APPROVED"):
            return view(draft)
        if draft.status == "PROCESSING" and not processing_stale(draft):
            raise HTTPException(409, "Extraction is already processing; retrieve this draft later")
        if draft.status == "PROCESSING":
            draft.status, draft.error_code = "FAILED", "JD_EXTRACTION_INTERRUPTED"
        if not retry and not expired:
            return view(draft)
        draft.status = "PROCESSING"
        draft.error_code = None
        draft.pages = []
        draft.profile = None
        draft.expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    else:
        draft = JDDraftModel(id=uuid4().hex, owner_id=principal.id, pdf_hash=digest,
            status="PROCESSING", pages=[], expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            provenance={"pdf_hash": digest, "schema_version": "jd-v1", "extraction_version": "jd-pdf-v1",
                        "prompt_version": "jd-prompt-v1", "provider": settings.LLM_PROVIDER,
                        "model": settings.GROQ_MODEL if settings.LLM_PROVIDER == "groq" else
                                 settings.OPENROUTER_MODEL if settings.LLM_PROVIDER == "openrouter" else
                                 "gemini-3.6-flash" if settings.LLM_PROVIDER == "gemini" else "mock"})
        db.add(draft)
    attempt_token = uuid4().hex
    draft.provenance = {**draft.provenance, "processing_started_at": datetime.now(timezone.utc).isoformat(),
                        "attempt_token": attempt_token}
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "This PDF is already being extracted; retry later")
    status, error_code, profile_data, pages = "FAILED", None, None, []
    try:
        extracted = await asyncio.to_thread(ingest_pdf, bytes(data), redact=False)
        if extracted.status != "success":
            error_code = extracted.code
        elif len(extracted.redacted_text) > 60000:
            error_code = "JD_TEXT_LIMIT"
        else:
            pages = extracted.pages
            profile = await extract_profile(extracted.redacted_text)
            profile_data = profile.model_dump(mode="json")
            status = "REVIEW"
    except Exception:
        error_code = "JD_PROVIDER_ERROR"
    draft = (await db.execute(select(JDDraftModel).where(JDDraftModel.id == draft.id)
        .with_for_update().execution_options(populate_existing=True))).scalar_one()
    if draft.status != "PROCESSING" or draft.provenance.get("attempt_token") != attempt_token:
        return view(draft)
    draft.status, draft.error_code, draft.profile, draft.pages = status, error_code, profile_data, pages
    await db.commit()
    return view(draft)


@router.get("/drafts/{draft_id}")
async def get_draft(draft_id: str, db: AsyncSession = Depends(get_db),
                    principal: Principal = Depends(current_principal)):
    return view(await owned_draft(db, draft_id, principal))


@router.post("/drafts/{draft_id}/approve")
async def approve(draft_id: str, payload: ExtractedJD, db: AsyncSession = Depends(get_db),
                  principal: Principal = Depends(current_principal)):
    draft = await owned_draft(db, draft_id, principal, lock=True)
    existing = (await db.execute(select(ApprovedJDModel).where(
        ApprovedJDModel.draft_id == draft.id))).scalar_one_or_none()
    if existing:
        if existing.profile != payload.screening_profile(existing.id):
            raise HTTPException(409, "Approved versions are immutable; upload a new PDF version")
        return {"approved_jd_id": existing.id, "profile": existing.profile}
    if draft.status != "REVIEW":
        raise HTTPException(409, "Only a successfully extracted review draft can be approved")
    identifier = uuid4().hex
    approved = ApprovedJDModel(id=identifier, owner_id=principal.id, draft_id=draft.id,
        profile=payload.screening_profile(identifier),
        provenance={**draft.provenance, "owner_id": principal.id, "version": 1,
                    "review_uncertainties": payload.uncertainties}, approved_at=datetime.now(timezone.utc))
    db.add(approved)
    draft.status = "APPROVED"
    # Source text is temporary; retain hashes and approved requirements as provenance.
    draft.pages = []
    draft.profile = payload.model_dump(mode="json")
    await db.commit()
    return {"approved_jd_id": identifier, "profile": approved.profile}


@router.get("/approved")
async def list_approved(db: AsyncSession = Depends(get_db), principal: Principal = Depends(current_principal)):
    rows = (await db.execute(select(ApprovedJDModel).where(ApprovedJDModel.owner_id == principal.id)
                            .order_by(ApprovedJDModel.approved_at.desc()).limit(100))).scalars().all()
    return {"jds": [{"approved_jd_id": row.id, "profile": row.profile,
                     "approved_at": row.approved_at, "provenance": row.provenance} for row in rows]}
