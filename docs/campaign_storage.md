# Campaign storage contract (Phase 2)

Each campaign has one owner and an immutable set of JD and policy snapshots. JD
keys are unique within a campaign. Each accepted candidate has a campaign-local
ID and one document version in that campaign. A changed PDF under the same ID is
an idempotency conflict; an equal PDF hash under a different ID remains a separate
candidate. The candidate-JD pair row is the authoritative outcome. Every accepted
CV creates one pair per JD, including a CV whose extraction later fails.

Pair statuses `EXTRACTION_FAILED`, `FILTER_REJECTED`, `PROCESSING_FAILED`,
`CUTOFF_EXCLUDED`, `SUCCESS`, `REVIEW_REQUIRED`, and `EVALUATION_FAILED` are
terminal. `PENDING`, `RUNNING`, and `SHORTLISTED` are nonterminal. Campaign and JD
counts reconcile pair rows against the CV and JD counts. A campaign can be marked
complete only when every pair is terminal; later phases add the coordinator that
makes that transition.

Raw PDF bytes may be stored only encrypted in `campaign_cvs.encrypted_pdf` while
Stage 0 is pending or running. Both Stage 0 terminal paths clear that column in
the same transaction as the result. A database constraint prevents terminal
Stage 0 rows from retaining PDF bytes. Redacted text, source locations, hashes,
and outcomes remain sensitive. All application reads must check campaign ownership;
default status and ranking reads omit CV text and raw bytes. Storage and backup
retention for these sensitive records must follow the deployment's existing
access controls until an explicit deletion policy is defined. Logs must contain
IDs and error codes only, never PDF or CV content.
