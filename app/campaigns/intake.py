"""Bounded campaign archive intake shared by the API and folder importer."""
import hashlib
import re
import zipfile
from pathlib import PurePosixPath
from uuid import uuid5, NAMESPACE_URL

from app.config import settings
from app.campaigns.persistence import reserve_cv


class ArchiveLimitError(ValueError):
    pass


def _member_name(raw: str) -> str | None:
    name = raw.replace("\\", "/")
    parts = PurePosixPath(name).parts
    if (not name or name.startswith("/") or not parts or
            any(part in {"", ".", ".."} for part in name.split("/")) or
            re.match(r"^[A-Za-z]:", name) or len(name) > 512):
        return None
    return name


async def accept_pdf(db, *, campaign_id, owner_id, source_name, data, ordinal):
    if len(data) > settings.PDF_MAX_BYTES:
        return {"name": source_name, "code": "PDF_TOO_LARGE"}, None
    candidate_id = str(uuid5(NAMESPACE_URL, f"{campaign_id}:{ordinal}:{source_name}"))
    created, cv = await reserve_cv(
        db, campaign_id=campaign_id, owner_id=owner_id, candidate_id=candidate_id,
        source_filename=source_name, content_hash=hashlib.sha256(data).hexdigest(),
        pdf_bytes=data)
    return None, cv.id if created or cv.stage0_status == "PENDING" else None


async def import_zip(db, *, campaign_id: str, owner_id: str, archive):
    """Read one member at a time; report rejected entries in archive order."""
    try:
        bundle = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("Invalid ZIP archive") from exc
    with bundle:
        infos = bundle.infolist()
        if len(infos) > settings.CAMPAIGN_MAX_MEMBERS:
            raise ArchiveLimitError("ZIP member count exceeds limit")
        if sum(info.file_size for info in infos) > settings.CAMPAIGN_UNCOMPRESSED_MAX_BYTES:
            raise ArchiveLimitError("ZIP uncompressed size exceeds limit")
        accepted, rejected, queued = [], [], []
        seen = set()
        for ordinal, info in enumerate(infos):
            if info.is_dir():
                continue
            name = _member_name(info.filename)
            label = info.filename[:512]
            if name is None:
                rejected.append({"name": label, "code": "UNSAFE_PATH"})
                continue
            leaf = PurePosixPath(name).name
            key = leaf.casefold()
            if key in seen:
                rejected.append({"name": label, "code": "DUPLICATE_NAME"})
                continue
            seen.add(key)
            if not leaf.lower().endswith(".pdf"):
                rejected.append({"name": label, "code": "NOT_PDF"})
                continue
            if info.file_size > settings.PDF_MAX_BYTES:
                rejected.append({"name": label, "code": "PDF_TOO_LARGE"})
                continue
            if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                rejected.append({"name": label, "code": "UNSUPPORTED_COMPRESSION"})
                continue
            try:
                with bundle.open(info) as source:
                    data = source.read(settings.PDF_MAX_BYTES + 1)
                    if source.read(1):
                        raise ArchiveLimitError("ZIP member exceeds PDF limit")
            except (RuntimeError, zipfile.BadZipFile, OSError, EOFError):
                rejected.append({"name": label, "code": "CORRUPT_MEMBER"})
                continue
            error, cv_id = await accept_pdf(
                db, campaign_id=campaign_id, owner_id=owner_id,
                source_name=leaf, data=data, ordinal=ordinal)
            del data
            if error:
                rejected.append(error)
            else:
                accepted.append({"name": label, "candidate_id": str(uuid5(
                    NAMESPACE_URL, f"{campaign_id}:{ordinal}:{leaf}"))})
                if cv_id:
                    queued.append(cv_id)
        return {"accepted_count": len(accepted), "rejected_count": len(rejected),
                "accepted": accepted, "rejected": rejected}, queued
