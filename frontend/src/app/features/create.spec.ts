import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';
import { CreateComponent } from './create';
import { CampaignApi } from '../api/campaign-api';

const profile = { schema_version: 'jd-v1' as const, title: 'Engineer',
  jd_category_queries: { SKILLS: 'Python', EXPERIENCE: 'Software', PROJECTS: 'APIs', EDUCATION: 'No explicit requirement' },
  hard_filter_rules: { min_years_experience: 0, degree_requirement: null, require_work_authorization: false },
  must_have_skills: [{ canonical: 'Python', aliases: ['py'], substitutes: ['Java'] }], nice_to_have_skills: [], uncertainties: ['Years unspecified'],
  relevance_contract: { version: 'relevance-v1' as const, minimum_coverage: 0, targets: [{
    target_id: 'delivery', category: 'EXPERIENCE' as const, kind: 'responsibility' as const,
    treatment: 'requirement' as const, text: 'Deliver Python services', source_quote: 'Python software engineering',
    importance: 2, evidence_terms: [['Python'], ['service', 'services']]
  }] } };

describe('JD review and approval', () => {
  function setup() {
    const api = { extractJd: vi.fn(() => of({ draft_id: 'draft', status: 'REVIEW', profile, pages: [], error_code: null })),
      approveJd: vi.fn(() => of({ approved_jd_id: 'approved', profile: { ...profile, job_id: 'approved' } })),
      create: vi.fn(() => of({ campaign_id: 'campaign', status: 'INTAKE', created: true, upload_url: '' })) };
    TestBed.configureTestingModule({ imports: [CreateComponent], providers: [provideRouter([]), { provide: CampaignApi, useValue: api }] });
    const fixture = TestBed.createComponent(CreateComponent);
    const component = fixture.componentInstance;
    return { component, api, fixture };
  }
  it('prefills requirements, preserves edits and submits only approved references', () => {
    const { component, api } = setup(); const jd = component.jds.at(0);
    component.create(); expect(api.create).not.toHaveBeenCalled();
    component.chooseJd({ target: { files: [new File(['pdf'], 'jd.pdf')] } } as unknown as Event, jd);
    expect(jd.controls.title.value).toBe('Engineer');
    expect(jd.controls.requiredSkills.value).toBe('Python | py | Java');
    expect(component.cap).toBe(15);
    expect(jd.controls.relevanceTargets.length).toBe(1);
    jd.controls.relevanceTargets.at(0).controls.text.setValue('Build Python services');
    component.create(); expect(api.create).not.toHaveBeenCalled();
    jd.controls.title.setValue('Edited Engineer'); component.approve(jd);
    expect(api.approveJd.mock.calls[0]).toEqual(['draft', expect.objectContaining({ title: 'Edited Engineer', must_have_skills: profile.must_have_skills }), false]);
    expect(api.approveJd.mock.calls[0]).toEqual(['draft', expect.objectContaining({ relevance_contract: {
      ...profile.relevance_contract, targets: [{ ...profile.relevance_contract.targets[0], text: 'Build Python services' }]
    } }), false]);
    expect(jd.disabled).toBe(true);
    component.create(); expect(api.create.mock.calls[0]).toEqual([expect.objectContaining({ approved_jd_ids: ['approved'] })]);
  });
  it('reopens an approval and requires approval of the edited version', () => {
    const { component, api } = setup(); const jd = component.jds.at(0);
    component.chooseJd({ target: { files: [new File(['pdf'], 'jd.pdf')] } } as unknown as Event, jd);
    component.approve(jd); component.editJd(jd);
    expect(jd.enabled).toBe(true); expect(component.state(jd).approvedId).toBeUndefined();
    component.create(); expect(api.create).not.toHaveBeenCalled();
    jd.controls.title.setValue('Revised Engineer'); component.approve(jd);
    expect(api.approveJd.mock.calls[1]).toEqual(['draft', expect.objectContaining({ title: 'Revised Engineer' }), true]);
    component.form.controls.name.setValue('September engineers'); component.create();
    expect(api.create.mock.calls[0]).toEqual([expect.objectContaining({ name: 'September engineers' })]);
  });
  it('hides target type while retaining it in the approval payload', () => {
    const { component, fixture } = setup(); const jd = component.jds.at(0);
    component.chooseJd({ target: { files: [new File(['pdf'], 'jd.pdf')] } } as unknown as Event, jd);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[formControlName="kind"]')).toBeNull();
    expect(component.jobs()[0].relevance_contract?.targets[0].kind).toBe('responsibility');
  });
  it('shows validation errors instead of silently ignoring approval', () => {
    const { component, api } = setup(); const jd = component.jds.at(0);
    component.chooseJd({ target: { files: [new File(['pdf'], 'jd.pdf')] } } as unknown as Event, jd);
    component.approve(jd); component.editJd(jd);
    jd.controls.relevanceTargets.at(0).controls.importance.setValue(0);
    component.approve(jd);
    expect(component.state(jd).error).toContain('importance between 1 and 5');
    expect(api.approveJd).toHaveBeenCalledTimes(1);
  });
  it('blocks campaign creation until every uploaded JD is approved', () => {
    const { component, api } = setup(); const jd = component.jds.at(0);
    component.chooseJd({ target: { files: [new File(['pdf'], 'jd.pdf')] } } as unknown as Event, jd);
    component.approve(jd); component.add(); component.create();
    expect(api.create).not.toHaveBeenCalled();
    expect(component.formError()).toContain('approve every JD');
  });
});
