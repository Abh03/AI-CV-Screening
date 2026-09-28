import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { PairResult, RelevanceTarget } from './contracts';

export interface Review {
  decision: 'UNREVIEWED' | 'SHORTLIST' | 'HOLD' | 'NOT_PROCEEDING'; notes: string; reason: string; tags: string[];
  assignee_id: string | null; version: number; reviewer_id: string | null; updated_at: string | null;
  verified_facts?: { experience_years?: number | null; work_authorized?: string; education_meets_requirement?: boolean | null };
}
export interface Assessment { score: number; rationale: string; citations: string[]; claims?: { claim: string; citation: string; quote: string }[] }
export interface RecruiterCandidate extends PairResult {
  pair_id: string; campaign_id: string; campaign_name?: string; jd_key: string; role_title: string;
  experience_years: number | null; experience_source: string; cv_available: boolean; stage0_status: string;
  effective_experience_years?: number | null;
  effective_eligibility_checks?: { rule: string; status: string; message: string; source?: string }[];
  updated_at: string; review: Review; summary: string; search_excerpt?: string;
  stage1_checks: { rule: string; status: string; code: string; message: string }[];
  flags: { type: string; severity: string; description: string }[];
  stage_history: { stage: string; state: string; reason: string | null }[];
  assessments?: Record<string, Assessment>; scoring_policy_version?: string;
  evidence: (PairResult['evidence'][number] & { text?: string })[];
  history?: { actor_id: string; created_at: string; snapshot: Review }[];
  other_roles?: { pair_id: string; jd_key: string; role_title: string; status: string; score: number | null }[];
  scoring_policy?: Record<string, unknown>;
}
export interface PoolFilters {
  search?: string; status?: string; decision?: string; min_score?: number | null; max_score?: number | null;
  category?: string; min_category_score?: number | null; min_years?: number | null; max_years?: number | null;
  experience_unknown?: boolean; eligibility_rule?: string; eligibility_status?: string;
  requirement_ids?: string[]; requirement_status?: string; requirement_mode?: string;
  review_only?: boolean; reason?: string; tag?: string; assignee_id?: string; reviewer_id?: string; reviewed_after?: string;
  sort?: string; direction?: string;
}
export interface PoolAnalytics {
  total: number; assessed: number; review_needed: number; shortlisted: number;
  status_counts: Record<string, number>; decision_counts: Record<string, number>; score_distribution: number[];
  requirements: { target_id: string; text: string; treatment: string; counts: Record<string, number> }[];
}
export interface CandidatePool {
  campaign_id: string; campaign_name: string | null; jd_key: string; role_title: string; jd_status: string; stage3_cap: number;
  total: number; limit: number; offset: number; results: RecruiterCandidate[];
  analytics: PoolAnalytics; filtered_analytics: PoolAnalytics; requirements: RelevanceTarget[];
}
export interface CvDocument {
  candidate_id: string; filename: string; original_available: boolean; text: string; stage0_status: string; extraction_error: string | null;
  pages: { page_number: number; blocks: { block_number: number; text: string; bbox: number[] }[] }[];
}
export interface SavedView { id: string; name: string; filters: PoolFilters; columns: string[] }
export interface Member { id: string; username: string; owner: boolean }
export interface CandidateSearch {
  total: number; results: RecruiterCandidate[];
  intake_rejection_total: number;
  intake_rejections: { campaign_id: string; campaign_name: string | null; filename: string; reason: string }[];
}
export interface ReviewUpdate {
  pair_versions: Record<string, number>; decision: Review['decision']; reason: string;
  notes?: string; tags?: string[]; assignee_id?: string | null;
  verified_facts?: Review['verified_facts'];
}

@Injectable({ providedIn: 'root' })
export class RecruiterApi {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/v1/recruiter';
  private scope(campaign: string, role: string): string {
    return `${this.base}/campaigns/${encodeURIComponent(campaign)}/roles/${encodeURIComponent(role)}`;
  }
  pool(campaign: string, role: string, filters: PoolFilters, offset = 0): Observable<CandidatePool> {
    return this.http.get<CandidatePool>(`${this.scope(campaign, role)}/pool`, { params: { filters: JSON.stringify(filters), limit: 30, offset } });
  }
  search(search: string, campaign = '', offset = 0): Observable<CandidateSearch> {
    return this.http.get<CandidateSearch>(`${this.base}/candidates`, { params: { search, campaign_id: campaign, limit: 30, offset } });
  }
  detail(pair: string): Observable<RecruiterCandidate> { return this.http.get<RecruiterCandidate>(`${this.base}/pairs/${encodeURIComponent(pair)}`); }
  cv(campaign: string, candidate: string): Observable<CvDocument> {
    return this.http.get<CvDocument>(`${this.base}/campaigns/${encodeURIComponent(campaign)}/candidates/${encodeURIComponent(candidate)}/cv`);
  }
  pdf(campaign: string, candidate: string): Observable<Blob> {
    return this.http.get(`${this.base}/campaigns/${encodeURIComponent(campaign)}/candidates/${encodeURIComponent(candidate)}/document`, { responseType: 'blob' });
  }
  restore(campaign: string, candidate: string, file: File): Observable<unknown> {
    return this.http.put(`${this.base}/campaigns/${encodeURIComponent(campaign)}/candidates/${encodeURIComponent(candidate)}/document`, file, { headers: { 'Content-Type': 'application/pdf' } });
  }
  update(body: ReviewUpdate): Observable<unknown> { return this.http.patch(`${this.base}/reviews`, body); }
  views(): Observable<SavedView[]> { return this.http.get<SavedView[]>(`${this.base}/views`); }
  saveView(name: string, filters: PoolFilters, columns: string[]): Observable<SavedView> { return this.http.post<SavedView>(`${this.base}/views`, { name, filters, columns }); }
  deleteView(id: string): Observable<unknown> { return this.http.delete(`${this.base}/views/${encodeURIComponent(id)}`); }
  members(campaign: string): Observable<{ can_manage: boolean; members: Member[] }> {
    return this.http.get<{ can_manage: boolean; members: Member[] }>(`${this.base}/campaigns/${encodeURIComponent(campaign)}/members`);
  }
  addMember(campaign: string, username: string): Observable<unknown> { return this.http.post(`${this.base}/campaigns/${encodeURIComponent(campaign)}/members`, { username }); }
  removeMember(campaign: string, id: string): Observable<unknown> { return this.http.delete(`${this.base}/campaigns/${encodeURIComponent(campaign)}/members/${encodeURIComponent(id)}`); }
  export(campaign: string, role: string, filters: PoolFilters, format = 'csv'): Observable<Blob> {
    return this.http.get(`${this.scope(campaign, role)}/export`, { params: { filters: JSON.stringify(filters), format }, responseType: 'blob' });
  }
}
