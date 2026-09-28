import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { of, Subject, throwError } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';
import { CandidatePool, RecruiterApi, RecruiterCandidate } from '../api/recruiter-api';
import { ResultsComponent } from './recruiter-results';
import { CandidatesComponent } from './candidates';
import { CvViewerComponent } from '../shared/cv-viewer';
import { CandidateDetailComponent } from '../shared/candidate-detail';

const candidate = (id: string): RecruiterCandidate => ({
  candidate_id: id, source_filename: id + '.pdf', pair_id: id, campaign_id: 'campaign', jd_key: 'role', role_title: 'Engineer',
  status: 'SUCCESS', score: 82, rank: 1, stage2_rank: 1, stage2_score: 0.9, tier: 'TIER_1', category_scores: { skills: 90, experience: 80, projects: 75, education: 65 },
  provisional: false, verification_required: false, verification_reasons: [], stage1_decision: 'PASS', stage1_checks: [],
  review_reasons: [], failure_code: null, error_message: null, is_mock: false, evidence: [],
  experience_years: 3, experience_source: 'cv_extracted', cv_available: false, stage0_status: 'SUCCEEDED', updated_at: '', flags: [],
  summary: 'Built Python services', stage_history: [], review: { decision: 'UNREVIEWED', reason: '', notes: '', tags: [], version: 0, assignee_id: null, reviewer_id: null, updated_at: null },
  stage2_target_assessments: [{ target_id: 'python', target_text: 'Python', category: 'SKILLS', status: 'DIRECT', coverage: 1, supporting_text: 'Built Python services' }],
});
const pool: CandidatePool = {
  campaign_id: 'campaign', campaign_name: 'Engineering hiring', jd_key: 'role', role_title: 'Engineer', jd_status: 'COMPLETED', stage3_cap: 15,
  total: 36, limit: 30, offset: 0, results: [candidate('Jane'), candidate('Alex')], requirements: [],
  analytics: { total: 36, assessed: 15, review_needed: 2, shortlisted: 0, status_counts: { SUCCESS: 15, CUTOFF_EXCLUDED: 21 }, decision_counts: {}, score_distribution: [0, 2, 13], requirements: [] },
  filtered_analytics: { total: 36, assessed: 15, review_needed: 2, shortlisted: 0, status_counts: {}, decision_counts: {}, score_distribution: [0, 2, 13], requirements: [] }
};
function apiMock() {
  return { pool: vi.fn(() => of(pool)), views: vi.fn(() => of([])), members: vi.fn(() => of({ can_manage: true, members: [] })),
    detail: vi.fn((id: string) => of(candidate(id))), update: vi.fn(() => of({})),
    cv: vi.fn(() => of({ filename: 'Jane.pdf', original_available: false, pages: [{ page_number: 1, blocks: [{ block_number: 0, text: 'Built Python services', bbox: [] }] }], text: '', stage0_status: 'SUCCEEDED', extraction_error: null })),
    restore: vi.fn(() => of({})), search: vi.fn(() => of({ total: 1, results: [{ ...candidate('Rejected'), status: 'FILTER_REJECTED', score: null, tier: null, stage_history: [{ stage: 'Eligibility checks', state: 'FAIL', reason: 'Experience below minimum' }, { stage: 'Detailed assessment', state: 'NOT_REACHED', reason: null }] }] })) };
}

describe('Recruiter workspace workflows', () => {
  it('renders recruiter information, opens CVs directly, and filters on the server before pagination', async () => {
    const api = apiMock();
    TestBed.configureTestingModule({ providers: [provideRouter([{ path: 'campaigns/:id/jds/:jd', component: ResultsComponent }]), { provide: RecruiterApi, useValue: api }] });
    const harness = await RouterTestingHarness.create();
    const component = await harness.navigateByUrl('/campaigns/campaign/jds/role', ResultsComponent);
    expect(harness.routeNativeElement?.textContent).toContain('Engineering hiring');
    expect(harness.routeNativeElement?.textContent).toContain('Not reviewed');
    const cvButton = Array.from(harness.routeNativeElement!.querySelectorAll('button')).find(b => b.textContent?.trim() === 'View CV')!;
    cvButton.click(); harness.detectChanges();
    expect(api.cv).toHaveBeenCalledWith('campaign', 'Jane');
    expect(harness.routeNativeElement?.textContent).toContain('original PDF was not retained');
    component.cvCandidate.set(null); component.filters.search = 'Python'; component.apply();
    await harness.fixture.whenStable();
    expect(api.pool).toHaveBeenLastCalledWith('campaign', 'role', expect.objectContaining({ search: 'Python' }), 0);
    component.goPage(1); await harness.fixture.whenStable();
    expect(api.pool).toHaveBeenLastCalledWith('campaign', 'role', expect.objectContaining({ search: 'Python' }), 30);
  });

  it('compares fresh assessments and submits explicit versions for a bulk decision', async () => {
    const api = apiMock();
    TestBed.configureTestingModule({ providers: [provideRouter([{ path: 'campaigns/:id/jds/:jd', component: ResultsComponent }]), { provide: RecruiterApi, useValue: api }] });
    const harness = await RouterTestingHarness.create(); const component = await harness.navigateByUrl('/campaigns/campaign/jds/role', ResultsComponent);
    component.toggleCandidate(candidate('Jane')); component.toggleCandidate(candidate('Alex')); component.compare(); harness.detectChanges();
    expect(component.comparison().length).toBe(2); expect(api.detail).toHaveBeenCalledWith('Jane');
    expect(harness.routeNativeElement?.textContent).toContain('Compare candidates');
    component.closeCompare(); component.bulkDecision = 'HOLD'; component.bulkReason = 'Discuss with hiring manager'; component.saveBulk();
    expect(api.update).toHaveBeenCalledWith({ pair_versions: { Jane: 0, Alex: 0 }, decision: 'HOLD', reason: 'Discuss with hiring manager' });
    expect(component.chosen()).toEqual([]);
  });

  it('searches rejected candidates and explains the reached and unreached stages', async () => {
    const api = apiMock();
    TestBed.configureTestingModule({ providers: [provideRouter([{ path: 'candidates', component: CandidatesComponent }]), { provide: RecruiterApi, useValue: api }] });
    const harness = await RouterTestingHarness.create(); await harness.navigateByUrl('/candidates?search=Rejected', CandidatesComponent);
    expect(api.search).toHaveBeenCalledWith('Rejected', '', 0);
    expect(harness.routeNativeElement?.textContent).toContain('Experience below minimum');
    expect(harness.routeNativeElement?.textContent).toContain('Not reached');
    expect(harness.routeNativeElement?.textContent).toContain('Eligibility requirement not met');
  });

  it('keeps notes intact when a concurrent reviewer causes a conflict', () => {
    const api = apiMock(); api.update.mockReturnValue(throwError(() => ({ message: 'Decision changed; refresh' })));
    TestBed.configureTestingModule({ imports: [CandidateDetailComponent], providers: [{ provide: RecruiterApi, useValue: api }] });
    const fixture = TestBed.createComponent(CandidateDetailComponent); fixture.componentRef.setInput('pair', 'Jane'); fixture.detectChanges();
    const component = fixture.componentInstance; component.notes = 'Important interview context'; component.save();
    expect(component.notes).toBe('Important interview context'); expect(component.error()).toContain('Decision changed');
    expect(component.saving()).toBe(false);
  });

  it('shows extracted evidence without injecting candidate HTML', () => {
    const api = apiMock();
    api.cv.mockReturnValue(of({ filename: 'Jane.pdf', original_available: false, pages: [{ page_number: 1, blocks: [{ block_number: 0, text: '<img src=x onerror=alert(1)> Built Python services', bbox: [] }] }], text: '', stage0_status: 'SUCCEEDED', extraction_error: null }));
    TestBed.configureTestingModule({ imports: [CvViewerComponent], providers: [{ provide: RecruiterApi, useValue: api }] });
    const fixture = TestBed.createComponent(CvViewerComponent);
    fixture.componentRef.setInput('campaign', 'campaign'); fixture.componentRef.setInput('candidate', 'Jane'); fixture.componentRef.setInput('quote', 'Built Python services'); fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('img')).toBeNull();
    expect(fixture.nativeElement.querySelector('.evidence-highlight').textContent).toContain('<img src=x');
  });

  it('cancels stale pool responses when the user changes filters', async () => {
    const first = new Subject<CandidatePool>(), second = new Subject<CandidatePool>(); const api = apiMock();
    api.pool.mockReturnValueOnce(first).mockReturnValueOnce(second);
    TestBed.configureTestingModule({ providers: [provideRouter([{ path: 'campaigns/:id/jds/:jd', component: ResultsComponent }]), { provide: RecruiterApi, useValue: api }] });
    const harness = await RouterTestingHarness.create(); const component = await harness.navigateByUrl('/campaigns/campaign/jds/role', ResultsComponent);
    component.filters.search = 'Python'; component.apply(); await harness.fixture.whenStable();
    second.next({ ...pool, total: 1 }); first.next({ ...pool, total: 99 });
    expect(component.data()?.total).toBe(1);
  });
});
