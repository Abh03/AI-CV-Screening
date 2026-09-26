"""Repair name leaks only in the two superseded capacity trials."""
import asyncio
import json
import re
import zipfile
from pathlib import Path

import pymupdf
from dotenv import dotenv_values
from sqlalchemy import select, delete
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.models.database import CampaignCVModel, CampaignPairModel, DocumentVersionModel

CAMPAIGNS = ("0dcf5e5a-2dcb-4042-9e1f-51288a1687ba", "9b5edd3c-36d5-4d3a-b0c1-4dae0d9d61e1", "ec8bbc11-e38c-4d06-99ec-adeecf426c33")


async def main():
    url = make_url(dotenv_values("docker/.env")["DATABASE_URL"]).set(host="127.0.0.1", port=5432)
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    changed = []
    deleted = 0
    from tests.test_phone_extensions import phone_pattern
    phone = phone_pattern()
    try:
        with zipfile.ZipFile(r"C:\Abhyudit_Files\Infinite\CV-Benchmark-Dataset-Generator\output\cvs\CV_1000.zip") as archive:
            async with sessions() as db:
                cvs = (await db.execute(select(CampaignCVModel).where(
                    CampaignCVModel.campaign_id.in_(CAMPAIGNS)))).scalars().all()
                for cv in cvs:
                    if not cv.redacted_text:
                        continue
                    with pymupdf.open(stream=archive.read(cv.source_filename), filetype="pdf") as pdf:
                        original = pdf[0].get_text()
                    name = original.splitlines()[0].strip() if original.strip() else None
                    pattern = re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.IGNORECASE) if name else None
                    stored = cv.redacted_text + json.dumps(cv.source_locations)
                    if not (pattern and pattern.search(stored)) and not phone.search(stored):
                        continue
                    def clean(value):
                        if isinstance(value, str):
                            value = pattern.sub("[REDACTED_NAME]", value) if pattern else value
                            return phone.sub("[REDACTED_PHONE]", value)
                        if isinstance(value, list):
                            return [clean(item) for item in value]
                        if isinstance(value, dict):
                            return {key: clean(item) for key, item in value.items()}
                        return value
                    cv.redacted_text = clean(cv.redacted_text)
                    cv.source_locations = clean(cv.source_locations)
                    pairs = (await db.execute(select(CampaignPairModel).where(
                        CampaignPairModel.cv_id == cv.id))).scalars().all()
                    for pair in pairs:
                        pair.result_snapshot = clean(pair.result_snapshot)
                        pair.stage1_details = clean(pair.stage1_details)
                    result = await db.execute(delete(DocumentVersionModel).where(
                        DocumentVersionModel.candidate_id == cv.candidate_id))
                    deleted += result.rowcount
                    changed.append({"campaign_id": cv.campaign_id, "file": cv.source_filename})
                await db.commit()
        result = {"input": {"campaign_ids": CAMPAIGNS}, "output": {
            "repaired_documents": len(changed), "removed_stale_retrieval_documents": deleted,
            "examples": changed[:5], "existing_pair_outcomes_retained": True}}
        Path("artifacts/phase6/trial-redaction-repair.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
    finally:
        await engine.dispose()


asyncio.run(main())
