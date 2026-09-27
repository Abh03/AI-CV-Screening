# Pilot results — 27 September 2026

The 1,000-CV run has not been started. Yesterday’s campaign remains in the database and has been exported.

Current pilot: `77cecffb-8624-463d-a336-5ce7138698be`; status: **COMPLETED**.
36 unique CVs × 4 JDs = 144 comparisons. This deliberately stratified sample is not an estimate of whole-dataset accuracy.

## Stage inputs and outputs

| Stage | Input | Output |
|---|---|---|
| Intake / Stage 0 | 36 source PDFs | Extracted text and source blocks; all 36 succeeded |
| Stage 1 | Extracted CV text, required skill alternatives and hard-filter rules | Decisions, rule checks and source excerpts for 144 comparisons |
| Stage 2 | Filter survivors, approved relevance targets and source chunks | Evidence, category coverage scores, ranks and eligibility reasons |
| Stage 3 | Retrieved evidence plus citable CV context and JD requirements | Structured assessments, citation validation, review reasons and final outcomes |

Full per-comparison inputs and outputs: [pilot2-export.stage-io.json](pilot2-export.stage-io.json). The Stage 3 prompt is reconstructed from the persisted inputs and current code. Parsed model output is retained as llm_raw_output inside each evaluation; HTTP error bodies are not retained.

## Actual versus benchmark

“Expected to cross Stage 1” means generator PASS + REVIEW. The generator does not supply exact Stage 2/3 outcome labels; its relevance score supplies a proxy top list within the current cap of 15 per JD.

| JD | Stage 1 crossed: actual / expected | Stage 2 → LLM: actual / benchmark cap | Stage 2 held for review | LLM success / review / failure |
|---|---:|---:|---:|---:|
| Java Backend Engineer | 11 / 21 | 6 / 15 | 5 | 0 / 4 / 2 |
| C# / .NET Backend Engineer | 5 / 13 | 0 / 13 | 5 | 0 / 0 / 0 |
| Test Automation Engineer | 4 / 26 | 0 / 15 | 4 | 0 / 0 / 0 |
| Data Engineer | 3 / 12 | 0 / 12 | 3 | 0 / 0 / 0 |

Stage 0: 36/36 passed, matching the expected count. Counts after Stage 0 are JD–CV comparisons, not distinct CVs.

## Findings

1. Filtering now excludes missing mandatory skills and explicit below-minimum overall experience. Generator REVIEW labels often represent missing skills, while the new policy treats those as FAIL. This policy difference accounts for much of the mismatch.
2. All generator PASS cases in this pilot survive Stage 1 after the REST API wording fix. They still receive REVIEW where total experience cannot be established from an explicit source claim.
3. Stage 2 is the main remaining bottleneck. Candidates with strong skills and nonzero category scores are withheld unless at least one applied responsibility target has direct lexical support. All .NET, QA and Data survivors were withheld in this pilot. The extracted relevance contracts often combine several concepts with AND; coverage scoring is lexical rather than a calibrated semantic assessor.
4. Optional domain preferences can activate category weights. This can penalize otherwise suitable candidates and needs review alongside the responsibility contract.
5. Stage 3 remains problematic: four requests returned usable structured responses, but two received HTTP 413. All four responses went to review, with missing/unsupported claim support in every case. There are 15 claim quotes containing ellipses. All four evaluated contexts were complete, so missing supplied CV context does not explain these cases. Citation guardrails correctly withhold unsupported outputs.

Final result: 121 FILTER_REJECTED, 17 Stage 2 REVIEW_REQUIRED, 4 Stage 3 REVIEW_REQUIRED and 2 EVALUATION_FAILED; zero SUCCESS. The four HTTP 200 responses report 29,018 total tokens; rejected requests do not report token usage.

The existing campaign observer counts every REVIEW_REQUIRED pair as selected, including Stage 2 review cases. Its selected count therefore reads 23 even though only 6 entered Stage 3. This report uses persisted stage attempts and stage evidence to distinguish them. The observer needs correction before a full campaign.

Example: CV0102 reached the Java LLM shortlist although the benchmark records 0.75 years against a 3-year minimum. Stage 1 could not establish an explicit overall-years claim; the LLM noticed the short employment record and sent it to review. CV0888’s explicit 2.33-year claim was correctly rejected for jobs requiring 3 years.

## Repairs during testing

- Updated readiness to the current migration revision.
- Made JD extraction and audit metadata use the configured Gemini model. Gemini still rejected extraction, so these pilots used Groq.
- Corrected JD skill OR alternatives against PDF-backed source metadata before approval.
- Added REST API singular/plural identity equivalents.
- Set Groq completion tokens to 4,096 and low reasoning effort for GPT-OSS; reserved one 8,000-token request per budget window.

Regression validation after repairs: **367 passed, 14 skipped**. Infrastructure/live-provider tests are excluded from that suite count; the pilot separately exercised the real API, PostgreSQL retrieval, queues, extraction and Groq.

## Limits and next step

Pilot 1 is preserved separately. Pilot 2 uses the same approved JD profiles and CVs, with the runtime repairs. Yesterday used a 30-candidate cap; these pilots use the current 15-candidate cap. Provider, prompt, policy and JD-profile differences prevent attribution of all changes to a single fix.

Analyse the Stage 2 relevance contract and eligibility gate, source-based experience policy, and Stage 3 review reasons before authorizing the full dataset run. Do not tune to hidden candidate labels or simply relax evidence checks to make the benchmark look better.
