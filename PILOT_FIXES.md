# Pilot repairs — 27 September 2026

Source report: `artifacts/retest-20260927/PILOT_RESULTS.md`. The root folder did not contain `PILOT_RESULTS.md`. Findings were verified against the persisted pilot inputs, evidence, source CV text, model outputs and provider status logs. Existing pilot records remain unchanged.

## Verified causes and implemented changes

- **Stage 2:** The approved contracts already set `minimum_coverage` to zero. Lowering that threshold cannot resolve the failures. A separate eligibility gate demanded DIRECT coverage of at least one applied target. Java responsibilities used broad OR groups; .NET, QA and Data responsibilities bundled several AND groups. The action detector also missed common verbs such as reviewed, documented, investigated and used. Coverage v2 recognizes those verbs, retains applied mandatory-technology guidance even with approved responsibilities, and accepts a partial applied target only when one passage supports at least two groups and half the target. Partial evidence remains labeled PARTIAL. Skill lists, negation and a single generic partial anchor still cannot qualify alone. The 15-candidate cap and approved minimum coverage remain enforced. Preferences no longer activate an otherwise inapplicable category or reduce mandatory coverage; their bonus remains capped at 10 percentage points.
- **Experience:** When an explicit overall claim is missing, calculate elapsed months from the union of employment date ranges within a recognized work-history section. Overlapping jobs and gaps are handled without double counting. Numeric month/year, year/month, named months and Present are supported. Keep exact source date excerpts, merged intervals and the as-of date. Project and education dates do not count. Year-only, invalid, future, reversed, undated-role and conflicting overall claims remain unresolved. Near a minimum boundary, month-only date uncertainty routes to review rather than rejection. CV-extracted experience still requires recruiter verification under the existing policy.
- **Stage 3 citations:** Explicitly forbid ASCII and Unicode ellipses and joined/paraphrased quote spans in the prompt; the verifier rejects ellipses even if present literally in a cited source. Exact source and numeric-claim checks remain active.
- **Stage 3 payloads:** Default context budget is 12,000 characters and the escaped UTF-8 user prompt budget is 16,000 bytes. Repeated source text is sent once, with category citation snippets pointing to that source through `source_tag`. The trusted registry retains the full text for every category tag. Oversized contexts lose whole snippets, never sliced or rewritten quotes; omission metadata causes review. Registry and prompt are rebuilt together after trimming. HTTP 413 triggers a smaller-context retry within the configured retry count; a context that cannot fit returns a stable size-specific failure. Provider limits vary, so a byte budget does not guarantee acceptance by every provider.
- **Campaign accounting:** API aggregate counts now identify selected pairs using Stage 3 attempts or explicit shortlisted/running/success states. The load observer uses these counts so Stage 2 REVIEW_REQUIRED cases do not inflate the LLM selection count.
- **Audit:** Updated Stage 1, Stage 2 and prompt policy versions, and recorded context/prompt budgets in run policy snapshots. Existing coverage-v1 snapshots remain readable but cannot silently qualify under v2.

## Offline pilot replay

Reproduce with:

```powershell
.\venv\Scripts\python.exe -m scripts.pilot_repair_replay artifacts/retest-20260927/pilot2-export.stage-io.json artifacts/retest-20260927/repair-replay.json
```

| Role | Previous Stage 2 eligible | Repaired eligible using retained evidence |
|---|---:|---:|
| Java | 6 | 11 |
| .NET | 0 | 5 |
| QA | 0 | 4 |
| Data | 0 | 2 |

These are eligibility counts before applying the updated Stage 1 experience filter. CV0102 now establishes nine elapsed months (with month-date uncertainty still well below three years), so it is rejected for Java's three-year minimum. The replay does not use hidden benchmark labels, rerun retrieval, or call a provider. New delivery queries may retrieve further evidence in a fresh run. These results are not calibrated accuracy measurements; Stage 2 remains marked UNVALIDATED.

All six reconstructed Stage 3 prompts fit the new byte budget at approximately 10.3–15.0 KB, compared with 13.8–24.5 KB before, while preserving complete source CV context. Fresh provider outputs are needed to verify that the quotation instructions and compact citation representation improve live citation quality.

Regression coverage includes overlap/gap/current-date arithmetic, ambiguous dates and threshold precision, partial responsibility evidence, mandatory technology delivery, optional-category weights, ASCII/Unicode ellipsis rejection, escaped Unicode prompt limits, shared-source citation identity, HTTP 413 shrinking retries, and campaign selection accounting. Infrastructure and live-provider checks require a fresh small pilot before scaling to the full dataset.

Final offline validation: **389 passed, 14 deselected** using `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` and `pytest tests -q -m 'not infrastructure and not integration and not worker and not model'`. `git diff --check` passed. No live provider campaign or full-dataset run was started.
