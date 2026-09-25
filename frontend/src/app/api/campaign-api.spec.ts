import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withXhr } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { describe, expect, it, beforeEach, afterEach } from 'vitest';
import { CampaignApi } from './campaign-api';

describe('CampaignApi', () => {
  let api: CampaignApi;
  let http: HttpTestingController;
  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(withXhr()), provideHttpClientTesting(), CampaignApi] });
    api = TestBed.inject(CampaignApi); http = TestBed.inject(HttpTestingController);
  });
  afterEach(() => http.verify());
  it('sends the ZIP File as the request body with progress enabled', () => {
    const file = new File(['zip bytes'], 'sample.zip', { type: 'application/zip' });
    api.upload('campaign-1', file).subscribe();
    const request = http.expectOne('/api/v1/campaigns/campaign-1/archive');
    expect(request.request.body).toBe(file);
    expect(request.request.headers.get('Content-Type')).toBe('application/zip');
    expect(request.request.reportProgress).toBe(true);
    request.flush({ campaign_id: 'campaign-1', status: 'INTAKE', accepted_count: 0, rejected_count: 0, accepted: [], rejected: [] });
  });
  it('sends the stable idempotency key and server pagination parameters', () => {
    api.create({ job_profiles: [], idempotency_key: 'fixed-key' }).subscribe();
    const created = http.expectOne('/api/v1/campaigns');
    expect(created.request.body.idempotency_key).toBe('fixed-key');
    created.flush({ campaign_id: 'campaign-1', status: 'INTAKE', created: true, upload_url: '/api/v1/campaigns/campaign-1/archive' });
    api.outcomes('campaign-1', 'jd-1', 'REVIEW_REQUIRED', 30, 60).subscribe();
    const results = http.expectOne(request => request.url.endsWith('/outcomes'));
    expect(results.request.params.get('status')).toBe('REVIEW_REQUIRED');
    expect(results.request.params.get('limit')).toBe('30');
    expect(results.request.params.get('offset')).toBe('60');
    results.flush({ campaign_id: 'campaign-1', jd_key: 'jd-1', status: 'REVIEW_REQUIRED', total: 0, limit: 30, offset: 60, results: [] });
  });
  it('requests an owned campaign page and an immutable JD definition', () => {
    api.list(20, 40).subscribe();
    const listing = http.expectOne(request => request.url === '/api/v1/campaigns' && request.method === 'GET');
    expect(listing.request.params.get('limit')).toBe('20');
    expect(listing.request.params.get('offset')).toBe('40');
    listing.flush({ total: 0, limit: 20, offset: 40, campaigns: [] });
    api.definition('campaign-1', 'ops').subscribe();
    const definition = http.expectOne('/api/v1/campaigns/campaign-1/jds/ops/definition');
    definition.flush({ campaign_id: 'campaign-1', jd_key: 'ops', stage3_cap: 30,
      job_profile: { job_id: 'ops', title: 'Operations', jd_category_queries: { SKILLS: 'logistics' },
        hard_filter_rules: { min_years_experience: 0, degree_requirement: null, require_work_authorization: false } } });
  });
});
