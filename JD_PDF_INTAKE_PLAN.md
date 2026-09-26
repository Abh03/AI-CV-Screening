# JD PDF intake and approved screening profile plan

## Major goal

Replace manual JD creation with an upload-first flow. For each JD PDF, extract its text, ask an LLM once to produce a validated structured profile, fill an editable review form, and require recruiter approval before that profile becomes the saved JD used for screening. Reuse the same approved profile for Stage 1 rules, Stage 2 retrieval, and Stage 3 evaluation. A campaign remains a batch of one or more approved JDs screened against a ZIP of candidate CV PDFs.

## Recruiter flow

1. Upload one JD PDF (repeat for additional openings). The server validates PDF size, pages, integrity, and text extraction; use OCR for scanned pages. Show a useful extraction error or review state when text is unreliable.
2. Send extracted JD text to the configured LLM once for structured extraction. Validate its response with a versioned Pydantic schema. Never interpret instructions embedded in the PDF as instructions to the system.
3. Prefill an editable form with title, required and preferred skills (canonical terms, aliases and substitutes), minimum experience, degree/field, authorization requirement, and the four category requirements used by later stages. Show the PDF text or page references alongside uncertain fields. Treat missing or ambiguous requirements as needing recruiter judgment; do not invent hard filters.
4. The recruiter edits and explicitly approves each JD. Validate the approved form on the server, then save an immutable/versioned approved profile and provenance (owner, PDF hash, extraction/schema/model versions, approval time). Only approved JDs may be attached to a screening campaign. Keep temporary extraction drafts separate from approved JD records; decide draft retention and retry behavior before implementation.
5. Upload the ZIP of candidate CVs and screen every candidate against every approved JD. Reuse the saved profile without calling the JD extraction LLM for each candidate. Preserve the existing per-JD progress and results screens.

## What exists and what changes

| Area | Existing code | Decision |
| --- | --- | --- |
| PDF text/OCR | `app/stage0_extraction/parser.py`, `pipeline.py`, `integrity.py` use PyMuPDF, ordered blocks, integrity checks, and Tesseract fallback. | Reuse bounded PDF validation, reading order, and OCR. Add a JD-specific extraction path: the current `ingest_pdf` applies CV PII masking and may mask a JD title, so do not feed its redacted output to JD profiling. |
| JD schema | `app/stage1_rules/jd_profiler.py` has `SkillCluster`, `JDProfile`, and text encapsulation. | Extend or replace with one versioned approved-profile contract that also carries `jd_category_queries` and nested `hard_filter_rules`. Align `job_title` with the API's current `title`; avoid two incompatible JD shapes. Tighten field constraints and validate LLM output and recruiter edits. |
| JD LLM call | `app/stage3_evaluation/llm_client.py` supports schema-bound candidate evaluation. | Add a separate JD extraction prompt/client path using the approved-profile schema. Reuse provider configuration, timeouts, and retry patterns where suitable; do not reuse the Stage 3 evaluation prompt/schema. |
| Skill matching | `app/stage1_rules/jd_matcher.py` matches canonical terms, aliases, and substitutes, but is called only by its tests. | Reuse its term matching logic after reviewing semantics. Its current default 0.50 threshold can pass a CV missing half the listed must-haves; define explicit required-skill policy and record per-skill evidence/reasons. Missing text in a CV can be uncertain, so decide when it is safe to reject versus send for review. |
| Other Stage 1 rules | `app/stage1_rules/contracts.py` and `rules_engine.py` check authorization, experience, and degree. Campaign worker `app/workers/tasks.py` passes unknown authorization/experience, so these often become REVIEW. | Feed approved JD hard rules into the existing engine. Preserve PASS/REVIEW/FAIL distinction and provenance; do not claim CV text verifies authorization or years. Integrate skill checks into the campaign worker and the single-screening orchestrator consistently. |
| Later stages | Stage 2 uses `jd_category_queries`; Stage 3 prompt uses title and category requirements. | Generate or approve category requirements from the JD and save them in the same profile. Continue using them for retrieval and evaluation; consider whether Stage 3 should also see approved required/preferred skills explicitly. |
| Storage/API/UI | `campaign_jds.job_snapshot` stores manually submitted JSON; `POST /api/v1/campaigns` takes structured `job_profiles`; `frontend/src/app/features/create.ts` is a manual builder. | Add owner-scoped PDF extraction/draft and approval APIs, approved JD storage/versioning, and migration. Change campaign creation to reference approved JD versions or snapshot them atomically. Replace manual blank entry with upload, prefilled review, approval, then CV ZIP intake. Existing campaigns must remain readable. |

## Implementation order

1. Finalize the approved-profile schema and Stage 1 skill decision policy. Distinguish mandatory requirements, preferences, acceptable substitutes, and unclear statements. Define how uncertain CV evidence becomes REVIEW and how approved JD versions remain stable during a running campaign.
2. Implement JD-specific PDF extraction and a schema-constrained LLM extraction service. Add bounded input/output, retry and failure states, and tests with text PDFs, scanned PDFs, ambiguous JDs, and prompt-injection text.
3. Add draft review/approval endpoints and approved JD persistence with ownership, idempotency, provenance, and migration. Campaign creation should accept approved JD references and snapshot the approved versions. Keep old campaign reads working.
4. Build frontend upload and prefilled review/edit/approve UI for multiple JDs. Show extraction errors and clear approval state; retain existing campaign CV ZIP intake, monitoring, and results.
5. Wire approved skills and hard filters into Stage 1 for campaign pairs and the single-screening path. Keep precise PASS/REVIEW/FAIL reasons in stored results. Generate/use approved category requirements in Stages 2 and 3.
6. Verify end to end: PDF upload -> extraction -> edited approval -> saved version -> campaign -> candidate screening. Test retry without duplicate JD LLM calls, owner isolation, old campaign compatibility, and a mix of definite mismatches and uncertain CV evidence. Run targeted backend/frontend tests and one small PDF campaign integration check before broad load testing.
## Completion criteria

- Recruiter can create a JD from a PDF without manually transcribing it, inspect/edit the extraction, and explicitly approve it.
- Every screening job uses the exact approved JD version; JD extraction runs once per uploaded version rather than once per candidate.
- Stage 1 evaluates approved required skills alongside existing hard rules with explainable decisions; Stages 2 and 3 receive requirements derived from that same approved JD.
- Existing campaigns and result views remain usable, and the system never screens against an unapproved extraction.

Implementation steps 1–5 are complete. Offline verification and a small live
campaign on the local production Compose stack pass for step 6; recruiter quality
review and capacity validation remain. See [JD_PDF_INTAKE_STATUS.md](JD_PDF_INTAKE_STATUS.md)
for implemented behavior, test results, and remaining rollout checks.
