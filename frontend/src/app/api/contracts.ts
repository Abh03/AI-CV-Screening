export const categories = ['SKILLS', 'EXPERIENCE', 'PROJECTS', 'EDUCATION'] as const;
export type Category = typeof categories[number];
export type PairStatus = 'PENDING' | 'EXTRACTING' | 'EXTRACTED' | 'STAGE1_PASSED' | 'STAGE2_READY' | 'SHORTLISTED' | 'SUCCESS' | 'REVIEW_REQUIRED' | 'EVALUATION_FAILED' | 'FILTER_REJECTED' | 'PROCESSING_FAILED' | 'CUTOFF_EXCLUDED' | 'EXTRACTION_FAILED' | string;
export const outcomeStatuses = ['REVIEW_REQUIRED', 'EVALUATION_FAILED', 'FILTER_REJECTED', 'PROCESSING_FAILED', 'CUTOFF_EXCLUDED', 'EXTRACTION_FAILED'] as const;
export type OutcomeStatus = typeof outcomeStatuses[number];

export interface DegreeRequirement { level: string; level_aliases: string[]; fields: string[]; field_aliases: string[] }
export interface JobProfile {
  job_id: string; title: string;
  jd_category_queries: Partial<Record<Category, string>>;
  hard_filter_rules: { min_years_experience: number; degree_requirement: DegreeRequirement | null; require_work_authorization: boolean };
}
export interface CampaignCreate { job_profiles: JobProfile[]; idempotency_key: string }
export interface CampaignCreated { campaign_id: string; status: string; created: boolean; upload_url: string }
export interface CampaignListItem { campaign_id: string; status: string; created_at: string; updated_at: string; completed_at: string | null; jd_count: number; accepted_count: number }
export interface CampaignListPage { total: number; limit: number; offset: number; campaigns: CampaignListItem[] }
export interface JdDefinition { campaign_id: string; jd_key: string; job_profile: JobProfile; stage3_cap: number }
export interface IntakeReport { accepted_count: number; rejected_count: number; accepted: { name: string; candidate_id: string }[]; rejected: { name: string; code: string }[] }
export interface UploadResponse extends IntakeReport { campaign_id: string; status: string }
export interface CampaignCounts { jds: number; cvs: number; pairs: Record<string, number>; terminal_pairs: number }
export interface CampaignStatus { campaign_id: string; status: string; counts: CampaignCounts; stage0: Record<string, number>; stage3_retry_waiting: number; intake_report: IntakeReport | null }
export interface JdSummary { jd_key: string; title: string; status: string; stage3_cap: number; counts: Record<string, number>; stage3_retry_waiting: number }
export interface JdsResponse { campaign_id: string; jds: JdSummary[] }
export interface EvidenceRef { citation: string; document_id: string | null; chunk_id: string | null; source_location: Record<string, unknown> | null }
export interface PairResult {
  candidate_id: string; source_filename?: string | null; status: PairStatus; rank: number | null; stage2_rank: number | null; stage2_score: number | null;
  score: number | null; tier: string | null; category_scores: Record<string, number | null>;
  provisional: boolean; verification_required: boolean; verification_reasons: string[];
  stage1_decision: string | null; stage1_checks: unknown[]; review_reasons: string[];
  failure_code: string | null; error_message: string | null; is_mock: boolean; evidence: EvidenceRef[];
}
export interface ResultsPage { campaign_id: string; jd_key: string; total: number; limit: number; offset: number; results: PairResult[]; jd_status?: string; status?: OutcomeStatus }
