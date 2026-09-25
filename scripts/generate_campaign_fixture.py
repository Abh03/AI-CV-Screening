"""Generate synthetic PDF ZIP and JD JSON for a repeatable campaign smoke test.

Synthetic CVs exercise plumbing only; use representative PDFs for capacity claims.
"""
import argparse
import json
import zipfile
from pathlib import Path

import fitz


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--jds", type=int, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.count <= 2000 or not 1 <= args.jds <= 100:
        parser.error("count must be 1-2000 and jds 1-100")
    args.archive.parent.mkdir(parents=True, exist_ok=True)
    args.jobs.parent.mkdir(parents=True, exist_ok=True)
    jobs = [{"job_id": f"role-{i:02d}", "title": f"Operations Role {i:02d}",
             "jd_category_queries": {"EXPERIENCE": "operations planning and scheduling",
                                     "SKILLS": "planning reporting communication",
                                     "PROJECTS": "planning project reporting system",
                                     "EDUCATION": "bachelor of science"},
             "hard_filter_rules": {"require_work_authorization": False}}
            for i in range(args.jds)]
    args.jobs.write_text(json.dumps(jobs, indent=2) + "\n", encoding="utf-8")
    with zipfile.ZipFile(args.archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for i in range(args.count):
            document = fitz.open()
            page = document.new_page()
            page.insert_text((40, 60), f"Synthetic Candidate {i:05d}")
            page.insert_text((40, 100), "EXPERIENCE")
            page.insert_text((40, 130),
                             f"Operations planning, scheduling and reporting project {i:05d}.")
            page.insert_text((40, 155),
                             "Worked with a team to manage the project and use data in a system.")
            page.insert_text((40, 180), "SKILLS")
            page.insert_text((40, 210), "Planning, reporting, communication and spreadsheets.")
            page.insert_text((40, 235),
                             "Built a database and maintained a service for the team in production.")
            page.insert_text((40, 280), "PROJECTS")
            page.insert_text((40, 310),
                             "Designed a planning project and built a reporting system for the team.")
            page.insert_text((40, 355), "EDUCATION")
            page.insert_text((40, 385), "Bachelor of Science from a university.")
            archive.writestr(f"candidate-{i:05d}.pdf", document.tobytes())
            document.close()
    print(json.dumps({"pdfs": args.count, "jds": args.jds,
                      "archive_bytes": args.archive.stat().st_size}))


if __name__ == "__main__":
    main()
