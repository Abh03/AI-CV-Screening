"""Compare stored redaction with source PII without printing candidate PII."""
import argparse
import asyncio
import json
import re
import zipfile
from collections import Counter
from pathlib import Path

import pymupdf
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


async def main(args):
    values = dotenv_values(args.env_file)
    url = make_url(values["DATABASE_URL"]).set(host=args.database_host, port=args.database_port)
    engine = create_async_engine(url, pool_size=1, max_overflow=0)
    metadata = ({item["candidate_id"] + ".pdf": item for item in json.loads(args.source_metadata.read_text())}
                if args.source_metadata else {})
    try:
        async with engine.connect() as db:
            rows = (await db.execute(text("SELECT source_filename,stage0_status,redacted_text,"
                "source_locations,encrypted_pdf IS NOT NULL AS retained_raw FROM campaign_cvs "
                "WHERE campaign_id=:id"), {"id": args.campaign_id})).mappings().all()
        counts = Counter()
        examples = []
        with zipfile.ZipFile(args.archive) as archive:
            for row in rows:
                counts["documents"] += 1
                if row["stage0_status"] in {"SUCCEEDED", "FAILED"} and row["retained_raw"]:
                    counts["terminal_raw_pdf_leaks"] += 1
                if row["stage0_status"] != "SUCCEEDED":
                    continue
                counts["successful_documents"] += 1
                stored = (row["redacted_text"] or "") + json.dumps(row["source_locations"])
                if re.search(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", stored):
                    counts["email_pattern_leaks"] += 1
                known = metadata.get(row["source_filename"])
                if known:
                    for field in ("name", "email", "location"):
                        counts["metadata_" + field + "_checks"] += 1
                        if known[field].casefold() in stored.casefold():
                            counts["metadata_" + field + "_leaks"] += 1
                    phone_core = re.split(r"(?:x|ext\.?\s*)", known["phone"], flags=re.IGNORECASE)[0]
                    phone_digits = re.sub(r"\D", "", phone_core)
                    counts["metadata_phone_checks"] += 1
                    if len(phone_digits) >= 8 and phone_digits in re.sub(r"\D", "", row["redacted_text"] or ""):
                        counts["metadata_phone_leaks"] += 1
                with pymupdf.open(stream=archive.read(row["source_filename"]), filetype="pdf") as pdf:
                    source = pdf[0].get_text()
                if not source.strip():
                    counts["image_only_name_checks_unavailable"] += 1
                    continue
                name = source.splitlines()[0].strip()
                counts["source_name_checks"] += 1
                if name and name.casefold() in stored.casefold():
                    counts["source_name_leaks"] += 1
                    if len(examples) < 5:
                        examples.append({"file": row["source_filename"], "field": "name"})
                for email in re.findall(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", source):
                    counts["source_email_checks"] += 1
                    if email.casefold() in stored.casefold():
                        counts["source_email_leaks"] += 1
        result = {"input": {"archive": str(args.archive), "campaign_id": args.campaign_id},
                  "output": dict(counts), "leak_examples_without_pii": examples,
                  "limits": ["Native PDF comparisons require a text layer; supplied metadata also checks image-only names and contacts.",
                             "Exact source locations are audited separately; work/project locations may intentionally remain. This is not complete anonymization."]}
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))
        if any(counts[key] for key in ("terminal_raw_pdf_leaks", "email_pattern_leaks",
                                       "source_name_leaks", "source_email_leaks", "metadata_name_leaks",
                                       "metadata_email_leaks", "metadata_phone_leaks")):
            raise SystemExit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--database-host", default="127.0.0.1")
    parser.add_argument("--database-port", type=int, default=5432)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--source-metadata", type=Path, help="Optional source PII for image-only PDF checks")
    asyncio.run(main(parser.parse_args()))
