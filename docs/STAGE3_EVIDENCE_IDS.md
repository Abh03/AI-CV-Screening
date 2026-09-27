# Stage 3 evidence ID protocol

Prompt v11 uses a separate provider schema (`EvidenceSelectionOutput`). Category assessments and flags retain citations and claims, but citations now contain `ev_` SHA-256 evidence IDs and claims contain only claim and citation. Quotes are forbidden in provider responses.

IDs include candidate ownership, assessment category, document/chunk identity, source category, location and original text. They survive retrieval ordering changes and cannot be reused across candidates or categories. The bounded prompt and resolver share the same immutable registry. Omitted passages are not selectable.

Python resolves selections into the existing assessment format and attaches the complete original source passage to each claim, retaining its evidence_id. Positional tags remain internal compatibility keys in verification records; provider-supplied positional tags are invalid selections. Unknown IDs, incorrect categories, missing claim coverage and incomplete context require review. Historical persisted assessments remain readable.

Claim support is deliberately conservative: after existing case and whitespace normalization, an ID-backed factual claim must equal an entire source sentence, line or passage. This rejects paraphrases, added expertise/duration and substrings that drop negation or qualifiers. It is not fuzzy matching or a general semantic entailment engine. Suitability judgments belong in rationales. Free-form rationale and summary assertions still depend on the model following the prompt; this validator does not prove arbitrary prose is semantically supported. Human review remains necessary for uncertainty and material flags.

Mock evaluations retain their existing synthetic format and are restricted to development/test environments. No new live provider calls or pilot campaign were run as part of this change; offline tests do not establish a production success rate.
