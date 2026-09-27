## 2. Campaign Result Diagnostics & Root Cause Analysis

### 2.1 Final state, reconciliation, and qualifications

The final database state is COMPLETED: 4,000 terminal, unique comparisons, all four JDs COMPLETED. Database timestamps are 10:33:32–12:45:27 UTC on September 26; approximately 2h 11m 55s of database campaign time. The observer reported 7,917.344 seconds including its intake/poll boundary. These are close but not identical timing definitions.

| Stage | Input | Output / continued |
|---|---:|---|
| Intake | 1,000 PDFs | 1,000 accepted; zero rejected |
| Stage 0 | 1,000 CVs / 1,634 pages | 1,000 SUCCEEDED; 43 OCR pages in 30 scanned PDFs |
| Stage 1 | 4,000 comparisons | 4,000 REVIEW, zero PASS/FAIL; all continue |
| Stage 2 | 4,000 comparisons | 30 per JD selected, 3,880 cutoff exclusions |
| Stage 3 | 120 comparisons / 117 unique CVs | 8 SUCCESS, 97 REVIEW_REQUIRED, 15 EVALUATION_FAILED |

Stage 0 finished at about 814.75 seconds; all four shortlists were ready by about 1,491.453 seconds. Per-JD exact top-30, complete ranks 1–1,000, deterministic ordering, uniqueness, and final ranking checks passed. All 120 selected comparisons have at least one Stage 3 attempt. This proves they entered processing; it does not prove they all received a usable response. There are 114 persisted Stage 3 evaluation snapshots, including failure snapshots; all 105 success/review assessments have snapshots, while six retry-exhaustion outcomes have no assessment snapshot.

| JD | Selected | SUCCESS | Review | Failure |
|---|---:|---:|---:|---:|
| Java | 30 | 3 | 18 | 9 |
| .NET | 30 | 4 | 24 | 2 |
| QA | 30 | 1 | 25 | 4 |
| Data | 30 | 0 | 30 | 0 |

**Processing completion and model assessment coverage are different.** Only 6.7% of selected comparisons have Stage 3 SUCCESS, 80.8% require review, and 12.5% failed. All eight ranking entries remain provisional because of Stage 1 verification requirements. SUCCESS is not a recommendation to hire, and execution failure is not candidate rejection.

### 2.2 Independent expectation comparison

Generator Stage 1 expectations total PASS 323, FAIL 2,333, REVIEW 1,344. Actual decisions total REVIEW 4,000. These expected labels include known generator experience and skill facts, whereas current pipeline policy only permits certain verified facts to reject. The mismatch is therefore partly a product-policy mismatch and partly missing campaign fact extraction.

| JD | Expected PASS / FAIL / REVIEW | Strong matches available | Strong matches selected | Selected expected FAIL |
|---|---|---:|---:|---:|
| Java | 60 / 666 / 274 | 60 | 15 | 6 |
| .NET | 102 / 763 / 135 | 102 | 11 | 14 |
| QA | 91 / 351 / 558 | 91 | 14 | 5 |
| Data | 70 / 553 / 377 | 70 | 13 | 4 |

| JD | Selected benchmark bands |
|---|---|
| Java | 15 high, 11 moderate, 4 low |
| .NET | 11 high, 7 moderate, 12 unrelated |
| QA | 14 high, 15 moderate, 1 low |
| Data | 13 high, 16 moderate, 1 low |

The system sent its own exact top 30 to Stage 3, but these selections contained only 53 of 323 high-labelled comparisons across the four jobs (16.4%). With a fixed cap of 30, retrieving every high match is impossible; however, there were enough high matches to fill each list without low/unrelated choices. The current selected high-match share is 53/120, or 44.2%.

For a secondary comparison, the overlap with each benchmark top 30 sorted by relevance then filename is Java 4, .NET 1, QA 6, Data 2. Many benchmark scores tie, so this exact-membership statistic is tie-sensitive and is not the primary quality metric. High-band coverage and unrelated selections give clearer evidence.

### 2.3 Confirmed cause A: experience is absent and missing skills do not reject

**Confidence: 100% for why Stage 1 returned REVIEW for everyone.** Source hardcodes `candidate_yoe=None` and UNKNOWN provenance. All four jobs have positive experience minima. The database confirms 4,000 EXPERIENCE_UNKNOWN checks, 4,000 AUTHORIZATION_NOT_REQUIRED checks, and 4,000 EDUCATION_NOT_REQUIRED checks.

Skill checks recorded 5,767 REQUIRED_SKILL_FOUND and 10,233 REQUIRED_SKILL_UNCERTAIN across 16,000 group checks. Missing skills produce REVIEW, not FAIL. No hard degree/authorization filter was active, so no remaining enabled rule could reject these campaign candidates.

Even adding automatic experience extraction alone would not create hard rejections under current rules: `cv_extracted` experience remains unverified and gets REVIEW before a minimum-years rejection is considered. The product must define which facts permit automatic exclusion, rather than simply passing inferred years as recruiter verified.

**Effect:** All candidates consume retrieval and compete for the limited shortlist. Stage 1 is a verification-warning stage in this campaign, despite being described as a hard filter.

### 2.4 Confirmed cause B: masking removes technical information

**Confidence: 100% that the failure mode exists; 80% that it materially contributes to shortlist errors; its total effect is unmeasured.**

Local reproduction with the installed masker:

```text
Input:  Java | Python | SQL | C# | .NET | AWS | Azure | Spring Boot | Selenium | Apache Spark
spaCy:  Java -> PERSON
Output: [REDACTED_NAME] | Python | SQL | C# | .NET | AWS | Azure | Spring Boot | Selenium | Apache Spark
```

Both header and body functions remove Java in this example. Java is absent from the body whitelist. Saved rankings additionally show candidates with Java masked in context and corresponding REQUIRED_SKILL_UNCERTAIN checks; `stage-output-sample.json` contains placeholders inside skills and summary text. Header GPE/LOC removal can also erase technical terms, and header recognition does not stop at PROFILE.

This is a data-loss problem before retrieval and LLM assessment. It cannot be repaired by giving the LLM different weights after the missing term is gone. The audit did not run a complete raw-versus-masked accuracy ablation, so it cannot attribute a percentage of missed strong candidates to masking.

### 2.5 Confirmed cause C: project sections are lost through heading recognition

**Confidence: 100% for the reproduced heading defect and affected retrieved evidence; 95% for a substantial contribution to missing-project reviews.**

The section aliases omit `SELECTED PROJECTS`. Local PDF extraction/chunking replays for readable `CV0526.pdf` and `CV0888.pdf` preserve that heading but produce **zero PROJECTS-category chunks**. Their project text is assigned to the previously active category. Source-page provenance remains, but semantic categorization is wrong.

Database diagnostics show all 240 selected PROJECTS snippets (two per selected comparison) came from SUMMARY/EXPERIENCE sections, not PROJECTS. Generator candidates indicate 71 of the 120 selected comparisons actually had projects: Java 14, .NET 20, QA 19, Data 18. Those facts establish a distinction between missing source projects and missing retrieved project evidence.

This does not mean every fallback snippet lacks project facts: some may contain misclassified project content. It means category isolation and fallback are not providing the intended project evidence. For three jobs the retrieval query `No explicit requirement` makes date/general-text selections particularly uninformative.

Host replays are used only for the two readable examples; OCR-dependent examples could not be used as equivalent host replays. Campaign OCR success is established separately by the saved database results.

### 2.6 Confirmed cause D: sparse search is mostly inactive because of query construction

**Confidence: 100% that sparse contribution is nearly absent; 90% that the query construction contributes to shortlist weakness.**

New read-only probes parsed all 16 stored category queries and checked matching campaign-candidate chunks. Fifteen queries matched zero chunks. Only the .NET skills query matched chunks (35 stored matching chunks at probe time). This count is across matching stored document versions for these candidate IDs, so it is not a count of distinct campaign candidates. More direct historical evidence is the sparse-rank metadata retained in the campaign itself:

| JD | Returned Stage 2 evidence snippets | With non-null sparse rank |
|---|---:|---:|
| Java | 7,122 | 0 |
| .NET | 7,122 | 36 |
| QA | 7,122 | 0 |
| Data | 7,122 | 0 |

Only 36 of 28,488 returned snippets had sparse participation. Most of the claimed hybrid path therefore operated as dense retrieval followed by reranking. Long prose and comma-separated required/preferred lists are compiled as restrictive AND queries; alternatives and programming-language punctuation are not represented faithfully.

Changing RRF's `k` cannot fix a branch that returns no hits. A new sparse engine is also insufficient if it receives unsuitable queries or masked technical text.

### 2.7 Confirmed cause E: reranker scoring collapses candidate ranking to skills only

**Confidence: 100% for the observed collapse and .NET zero-tie selection; 95% that this is a major cause of poor shortlist quality.**

Saved Stage 2 category scores, across all 4,000 comparisons:

| JD | Experience zero / 1,000 | Projects zero / 1,000 | Education zero / 1,000 | Skills zero / 1,000 | Stage 2 rank-30 score |
|---|---:|---:|---:|---:|---:|
| Java | 1,000 | 1,000 | 1,000 | 904 | 1.0209 |
| .NET | 1,000 | 1,000 | 1,000 | 986 | 0.0000 |
| QA | 1,000 | 1,000 | 1,000 | 908 | 0.5477 |
| Data | 1,000 | 1,000 | 1,000 | 927 | 0.7497 |

Therefore the actual campaign candidate score was `0.30 × skills_score` for every comparison. Experience's nominal 40% weight had no influence. Projects and education also had no influence. This is a direct result measurement, not a guess based on the model's name.

The general passage reranker sees generic minimum-years/education prose, literal `No explicit requirement`, or long responsibility lists. All their retained category averages are nonpositive and clamped to zero. Raw relevance outputs have not been calibrated across these different query types; their suitability as global candidate scores has not been established.

For .NET there were only 14 positive-score candidates. The cutoff still required 30 selections, so **16 slots were filled from 986 zero-score candidates by candidate UUID**. The first 14 contain all 11 high and three moderate selections; the tied remainder contains four moderate and all 12 unrelated selections. This directly explains the unrelated candidates entering the .NET shortlist. Deterministic selection worked, but deterministic tie-breaking supplied no job relevance.

Potential subcauses include long multi-skill query dilution, general-search reranker mismatch to hiring requirements, masked skills, and fragmented passages. Their separate contributions require controlled replays; the zero collapse itself is confirmed.

**JD relevance information is also narrowed before retrieval. Confidence: 100% for the omission, 65% for its independent effect on missed strong candidates.** Java's source PDF describes digital banking, payment APIs, event consumers, and operational responsibilities. Its approved experience query is generic minimum-years prose and its projects query is `No explicit requirement`; its skills query is a tool list. Those source responsibilities/domain facts are not supplied as separate structured requirement fields downstream. The full JD text is not sent again in Stage 3. The .NET profile retains more responsibility context in PROJECTS, but other profiles do not. An approved profile can therefore be valid JSON while omitting relevant source context. The correct fix is a deliberate, recruiter-reviewed relevance contract, without turning every responsibility or domain preference into a hard filter.

### 2.8 Confirmed cause F: the LLM receives incomplete and fragmented evidence

**Confidence: 95% that evidence insufficiency materially drives missing-information reviews; 100% for the illustrated sample.**

Only two passages per category enter Stage 3. Block-local chunking does not keep a job's title, employment dates, responsibilities, and technologies together. Selected experience snippets are often date lines or tiny fragments. Of 60 selected experience slots per JD, the counts shorter than 65 characters were Java 28, .NET 40, QA 49, Data 49. Length alone is not proof of missing evidence, but the saved sample demonstrates the consequence.

`CV0888.pdf` ranks first for Data Engineering with Stage 2 score 1.5075. Its summary states 2.33 years, below the 3-year requirement; Stage 1 nevertheless records unknown experience. Its first PROJECTS passage is `[Section: EXPERIENCE] 2024 - 2026`, with reranker score -11.2411. The model then correctly says project detail is missing from the supplied evidence and returns a review assessment. The retrieved text is inadequate; the LLM never sees all source material to correct that limitation.

`CV0526.pdf`, .NET rank 1, has two experience passages of 39 characters each. Passing a citation membership check on these passages does not prove they justify the model's experience score of 78. This is an evidence-support risk, not a proven false claim without further source comparison.

### 2.9 Confirmed cause G: review policy reacts to optional/masked information

**Confidence: 100% for policy routing; 95% that overbroad missing-information handling substantially increases review volume.**

Final reviews contain MISSING_INFORMATION for 92 of the 97 review cases. Reasons overlap: invalid experience citations occur in seven cases, missing project citations in five, high/critical review flags in five, and other missing/invalid citations account for additional review reasons.

The system prompt explicitly treats missing graduation years and project details as missing information. Python routes **any** such flag to review, even LOW severity and even if the underlying item is optional. All four jobs have no degree filter; three have `PROJECTS: No explicit requirement`, but education/projects still receive scores and can block a final ranking. Masking older graduation years supplies a missing-information trigger that the model was told to notice.

Examples from persisted flags include missing Docker/Git or other preferred tools, unspecified graduation years, and project details unrelated to a mandatory JD item. These can be useful annotations but are not necessarily reasons to withhold the entire assessment from ranking. The policy conflates a missing optional detail with an unresolved mandatory requirement.

Data Engineering's 30 assessments all came from Groq according to their stored Stage 3 policy, and all became review. Provider switching cannot explain that job's empty final leaderboard; retrieval/prompt/review policy is the more direct explanation.