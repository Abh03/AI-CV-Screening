import { Component, inject, signal } from '@angular/core';
import { FormArray, FormBuilder, FormControl, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { HttpEventType } from '@angular/common/http';
import { finalize } from 'rxjs';
import { CampaignApi } from '../api/campaign-api';
import { categories, Category, JobProfile, UploadResponse, JdDraft, SkillCluster, RelevanceTarget } from '../api/contracts';
import { publicConfig } from '../core/config';
import { describeError } from '../core/http-errors';
import { LocalCampaigns } from '../core/local-campaigns';

type JdForm = ReturnType<CreateComponent['newJd']>;
type DraftState = { busy: boolean; error: string; draft?: JdDraft; approvedId?: string; revising?: boolean; file?: File };
const degreeLevels = ['NONE', 'SECONDARY', 'HIGHER SECONDARY', 'DIPLOMA', 'BACHELOR', 'MASTER', 'PHD'];

@Component({ standalone: true, imports: [ReactiveFormsModule, RouterLink], template: `
  <div class="page-heading"><div><p class="eyebrow">Campaign setup</p><h1>New campaign</h1><p>Add job descriptions, review them, then upload a ZIP of PDF CVs.</p></div><a routerLink="/">Back to campaigns</a></div>
  @if (campaignId()) { <section class="card"><h2>Upload CVs</h2><p>Campaign <code>{{ campaignId() }}</code> is saved. You can open it if this page closes.</p>
    <label for="archive">ZIP archive</label><input id="archive" type="file" accept=".zip,application/zip" (change)="chooseFile($event)" [disabled]="uploading()">
    @if (file()) { <p>{{ file()!.name }} · {{ (file()!.size / 1048576).toFixed(1) }} MiB</p> }
    @if (fileError()) { <p class="error" role="alert">{{ fileError() }}</p> }
    @if (uploading()) { <p role="status">{{ uploaded() === 100 ? 'Upload sent; waiting for server intake.' : 'Sending ZIP: ' + uploaded() + '%' }}</p><progress [value]="uploaded()" max="100"></progress> }
    <div class="actions"><button type="button" (click)="upload()" [disabled]="!file() || uploading()">{{ uploadError() ? 'Retry upload' : 'Upload ZIP' }}</button><a [routerLink]="['/campaigns', campaignId()]">Open campaign</a></div>
    @if (uploadError()) { <p class="error" role="alert">{{ uploadError() }} Check the campaign status before retrying if the connection was interrupted.</p> }
    @if (report()) { <h3>Intake report</h3><p>{{ report()!.accepted_count }} accepted · {{ report()!.rejected_count }} rejected</p>
      @if (report()!.rejected.length) { <table><caption>Rejected ZIP entries</caption><thead><tr><th scope="col">File</th><th scope="col">Reason code</th></tr></thead><tbody>@for (item of report()!.rejected; track $index) { <tr><td>{{ item.name }}</td><td>{{ item.code }}</td></tr> }</tbody></table> }
      @if (report()!.accepted_count === 0) { <p role="status">No PDFs were accepted. This campaign remains at intake; select another ZIP.</p> }
    }
  </section> } @else {
    <form [formGroup]="form" (ngSubmit)="review()" novalidate>
      <div class="campaign-name"><label for="campaign-name">Campaign name (optional)</label><input id="campaign-name" formControlName="name" maxlength="255" placeholder="e.g. Backend engineers, September"></div>
      <div formArrayName="jds">@for (jd of jds.controls; track jd; let i = $index) {
        <section class="card" [formGroupName]="i"><div class="card-title"><h2>Job description {{ i + 1 }}</h2><div class="compact-actions"><button type="button" class="secondary" (click)="move(i, -1)" [disabled]="i === 0">Move up</button><button type="button" class="secondary" (click)="move(i, 1)" [disabled]="i === jds.length - 1">Move down</button><button type="button" class="secondary" (click)="remove(i)" [disabled]="jds.length === 1">Remove</button></div></div>
          <label [for]="'pdf-' + i">Upload JD PDF (up to 10 MiB)</label><input [id]="'pdf-' + i" type="file" accept=".pdf,application/pdf" (change)="chooseJd($event, jd)" [disabled]="state(jd).busy || !!state(jd).approvedId">
          @if (state(jd).busy) { <p role="status">Processing job description…</p> }
          @if (state(jd).error) { <p class="error" role="alert">{{ state(jd).error }}</p>@if (!state(jd).draft?.profile) { <button type="button" (click)="extract(jd, true)" [disabled]="state(jd).busy">Retry extraction</button> } }
          @if (state(jd).draft?.profile) {
          <p role="status">{{ state(jd).approvedId ? 'Approved version saved' : 'Review extracted requirements and explicitly approve this JD.' }}</p>
          @for (uncertainty of state(jd).draft!.profile!.uncertainties; track $index) { <p class="muted">Needs judgment: {{ uncertainty }}</p> }
          <details><summary>Extracted PDF text by page</summary>@for (page of state(jd).draft!.pages; track page.page_number) { <h3>Page {{ page.page_number }}</h3>@for (block of page.blocks; track $index) { <p style="white-space: pre-wrap">{{ block.text }}</p> } }</details>
          <div class="grid two"><div><label [for]="'id-' + i">JD draft ID</label><input [id]="'id-' + i" formControlName="jobId" readonly></div>
          <div><label [for]="'title-' + i">Title</label><input [id]="'title-' + i" formControlName="title" maxlength="255" [attr.aria-invalid]="jd.controls.title.invalid && jd.controls.title.touched">@if (jd.controls.title.invalid && jd.controls.title.touched) { <p class="error" role="alert">Enter a title.</p> }</div></div>
          <fieldset><legend>Category requirements</legend><p class="muted">Review all four. Use “No explicit requirement” when the PDF does not specify one.</p>
            <div class="grid two">@for (category of categories; track category) { <div><label [for]="category + '-' + i">{{ categoryLabel(category) }}</label><textarea [id]="category + '-' + i" [formControl]="queryControl(jd, category)" rows="2"></textarea></div> }</div></fieldset>
          <fieldset><legend>Job relevance</legend><p>Review responsibilities, applied skills and domain context against the PDF. These guide ranking and do not add hard filters. Domain context must be a preference.</p>
            <div formArrayName="relevanceTargets">@for (target of jd.controls.relevanceTargets.controls; track target; let t = $index) {
              <div class="target-row" [formGroupName]="t">
<div class="target-skill"><label [for]="'target-text-' + i + '-' + t">Skill / responsibility</label><input [id]="'target-text-' + i + '-' + t" formControlName="text" maxlength="600"></div>
<div><label [for]="'target-category-' + i + '-' + t">Category</label><select [id]="'target-category-' + i + '-' + t" formControlName="category">@for (category of categories; track category) { <option [value]="category">{{ categoryLabel(category) }}</option> }</select></div>
<div><label [for]="'target-treatment-' + i + '-' + t">Treatment</label><select [id]="'target-treatment-' + i + '-' + t" formControlName="treatment"><option value="requirement">Requirement</option><option value="preference">Preference</option></select></div>
<div><label [for]="'target-importance-' + i + '-' + t">Importance</label><input [id]="'target-importance-' + i + '-' + t" formControlName="importance" type="number" min="1" max="5"></div>
<button type="button" class="secondary" (click)="jd.controls.relevanceTargets.removeAt(t)" [disabled]="!!state(jd).approvedId" [attr.aria-label]="'Remove ' + target.controls.text.value">Remove</button></div>
            }</div><button type="button" class="secondary" (click)="jd.controls.relevanceTargets.push(newTarget())" [disabled]="!!state(jd).approvedId || jd.controls.relevanceTargets.length >= 48">Add relevance target</button>
          </fieldset>
          <fieldset><legend>Hard filters</legend><div class="grid three"><div><label [for]="'years-' + i">Minimum years of experience</label><input [id]="'years-' + i" type="number" min="0" step="0.5" formControlName="years"></div><div><label [for]="'degree-' + i">Minimum degree</label><select [id]="'degree-' + i" formControlName="degree">@for (level of degreeLevels; track level) { <option [value]="level">{{ level }}</option> }</select></div><div><label [for]="'fields-' + i">Degree fields</label><input [id]="'fields-' + i" formControlName="fields" placeholder="Computer Science, Engineering"><small>Comma-separated. Requires a degree level.</small></div></div>
            <div class="grid two"><div><label [for]="'field-aliases-' + i">Degree field aliases</label><input [id]="'field-aliases-' + i" formControlName="fieldAliases"></div><div><label [for]="'level-aliases-' + i">Degree level aliases</label><input [id]="'level-aliases-' + i" formControlName="levelAliases"></div></div>
            <label class="check"><input type="checkbox" formControlName="authorization">Require work authorization</label></fieldset>
          <fieldset><legend>Approved skills</legend><p>Each line: canonical term | comma-separated aliases | comma-separated acceptable substitutes. Missing required skill evidence sends a CV to review.</p>
            <label [for]="'required-' + i">Required skills</label><textarea [id]="'required-' + i" formControlName="requiredSkills" rows="4"></textarea>
            <label [for]="'preferred-' + i">Preferred skills</label><textarea [id]="'preferred-' + i" formControlName="preferredSkills" rows="3"></textarea></fieldset>
          <button type="button" (click)="approve(jd)" [disabled]="state(jd).busy || !!state(jd).approvedId">{{ state(jd).approvedId ? 'Approved' : 'Approve reviewed JD' }}</button>
          @if (state(jd).approvedId) { <button type="button" class="secondary" (click)="editJd(jd)">Edit JD</button> }
          }
        </section> }
      </div>
      @if (formError()) { <p class="error" role="alert">{{ formError() }}</p> }
      <div class="actions"><button type="button" class="secondary" (click)="add()" [disabled]="jds.length >= maxJds">Add JD</button><button type="submit">Review campaign</button></div>
    </form>
    @if (reviewing()) { <section class="card" id="review" tabindex="-1"><h2>Review before creation</h2><p>{{ jds.length }} JD(s). Stage 3 selection is capped at {{ cap }} candidates per JD.</p><ol>@for (job of jobs(); track job.job_id) { <li><strong>{{ job.title }}</strong> ({{ job.job_id }}) · {{ queryNames(job) }} · minimum {{ job.hard_filter_rules.min_years_experience }} years · {{ job.hard_filter_rules.degree_requirement?.level ?? 'No degree filter' }}</li> }</ol><div class="actions"><button type="button" class="secondary" (click)="reviewing.set(false)">Edit</button><button type="button" (click)="create()" [disabled]="creating()">{{ creating() ? 'Creating…' : 'Create campaign' }}</button></div>@if (createError()) { <p class="error" role="alert">{{ createError() }}</p> }</section> }
  }
` })
export class CreateComponent {
  private readonly fb = inject(FormBuilder);
  private readonly api = inject(CampaignApi);
  private readonly local = inject(LocalCampaigns);
  private readonly router = inject(Router);
  readonly categories = categories;
  readonly degreeLevels = degreeLevels;
  readonly cap = publicConfig.stage3Cap;
  readonly maxJds = publicConfig.maxJds;
  readonly form = this.fb.group({ name: ['', Validators.maxLength(255)], jds: this.fb.array([this.newJd()]) });
  readonly formError = signal(''); readonly reviewing = signal(false); readonly creating = signal(false);
  readonly createError = signal(''); readonly campaignId = signal(''); readonly file = signal<File | null>(null);
  readonly fileError = signal(''); readonly uploadError = signal(''); readonly uploading = signal(false);
  readonly uploaded = signal(0); readonly report = signal<UploadResponse | null>(null);
  readonly draftStates = signal(new Map<JdForm, DraftState>());
  state(jd: JdForm): DraftState { return this.draftStates().get(jd) ?? { busy: false, error: '' }; }
  private setState(jd: JdForm, state: DraftState): void { this.draftStates.update(map => new Map(map).set(jd, state)); }
  chooseJd(event: Event, jd: JdForm): void {
    const file = (event.target as HTMLInputElement).files?.[0]; if (!file) return;
    if (!file.name.toLowerCase().endsWith('.pdf') || file.size > 10 * 1048576) { this.setState(jd, { busy: false, error: 'Choose a PDF no larger than 10 MiB.' }); return; }
    this.setState(jd, { busy: false, error: '', file }); this.extract(jd);
  }
  extract(jd: JdForm, retry = false): void {
    const file = this.state(jd).file; if (!file) return;
    this.reviewing.set(false); this.setState(jd, { file, busy: true, error: '' });
    this.api.extractJd(file, retry).subscribe({ next: draft => {
      const p = draft.profile;
      this.setState(jd, { file, draft, busy: false, error: p ? '' : draft.error_code === 'EXTRACTION_UNRELIABLE'
        ? 'The server could not read this PDF reliably, even after OCR. Try a PDF exported from the original document or a clearer scan. (EXTRACTION_UNRELIABLE)'
        : this.extractionError(draft.error_code ?? draft.status) });
      if (!p) return;
      jd.controls.relevanceTargets.clear();
      for (const target of p.relevance_contract?.targets ?? []) jd.controls.relevanceTargets.push(this.newTarget(target));
      jd.controls.minimumCoverage.setValue(p.relevance_contract?.minimum_coverage ?? 0);
      jd.patchValue({ jobId: draft.draft_id, title: p.title, skills: p.jd_category_queries.SKILLS ?? '',
        experience: p.jd_category_queries.EXPERIENCE ?? '', projects: p.jd_category_queries.PROJECTS ?? '', education: p.jd_category_queries.EDUCATION ?? '',
        years: p.hard_filter_rules.min_years_experience, authorization: p.hard_filter_rules.require_work_authorization,
        degree: p.hard_filter_rules.degree_requirement?.level ?? 'NONE', fields: p.hard_filter_rules.degree_requirement?.fields.join(', ') ?? '',
        fieldAliases: p.hard_filter_rules.degree_requirement?.field_aliases.join(', ') ?? '', levelAliases: p.hard_filter_rules.degree_requirement?.level_aliases.join(', ') ?? '',
        requiredSkills: this.skillLines(p.must_have_skills ?? []), preferredSkills: this.skillLines(p.nice_to_have_skills ?? []) });
    }, error: error => this.setState(jd, { file, busy: false, error: describeError(error) }) });
  }
  private extractionError(code: string): string {
    const messages: Record<string, string> = {
      JD_PROVIDER_ERROR: 'The AI service could not process the JD. Retry extraction.',
      JD_PROVIDER_INVALID_OUTPUT: 'The AI service returned an incomplete or invalid JD response. Retry extraction.',
      JD_PROVIDER_RATE_LIMITED: 'The AI service is busy. Wait briefly, then retry extraction.',
      JD_PROVIDER_UNAVAILABLE: 'The AI service is temporarily unavailable. Retry extraction.',
      JD_PROVIDER_CONFIGURATION: 'The AI service configuration needs attention. Contact your administrator.'
    };
    return `${messages[code] ?? 'Extraction needs attention.'} (${code})`;
  }
  private skillLines(skills: SkillCluster[]): string { return skills.map(s => `${s.canonical} | ${s.aliases.join(', ')} | ${s.substitutes.join(', ')}`).join('\n'); }
  private parseSkills(text: string): SkillCluster[] { return text.split('\n').filter(s => s.trim()).map(line => {
    const [canonical, aliases = '', substitutes = ''] = line.split('|');
    return { canonical: canonical.trim(), aliases: aliases.split(',').map(s => s.trim()).filter(Boolean), substitutes: substitutes.split(',').map(s => s.trim()).filter(Boolean) };
  }); }
  approve(jd: JdForm): void {
    jd.markAllAsTouched(); const state = this.state(jd); if (state.busy || state.approvedId || !state.draft?.profile) return;
    if (jd.invalid) { this.setState(jd, { ...state, error: 'Check the JD fields: enter a title, a skill or responsibility for each row, and importance between 1 and 5.' }); return; }
    const job = this.jobs()[this.jds.controls.indexOf(jd)];
    const { job_id, ...profile } = job;
    this.setState(jd, { ...state, busy: true, error: '' });
    this.api.approveJd(state.draft.draft_id, { ...profile, schema_version: 'jd-v1', uncertainties: state.draft.profile.uncertainties }, !!state.revising).subscribe({
      next: result => { jd.disable(); this.setState(jd, { ...state, busy: false, approvedId: result.approved_jd_id }); },
      error: error => this.setState(jd, { ...state, busy: false, error: describeError(error) }) });
  }
  editJd(jd: JdForm): void {
    const state = this.state(jd); if (state.busy || !state.approvedId) return;
    jd.enable(); this.setState(jd, { ...state, approvedId: undefined, revising: true, error: '' });
    this.reviewing.set(false); this.formError.set(''); this.idempotencyKey = crypto.randomUUID();
  }
  private idempotencyKey = crypto.randomUUID();
  get jds(): FormArray<JdForm> { return this.form.controls.jds; }
  newTarget(target?: RelevanceTarget) { return this.fb.group({
    targetId: [target?.target_id ?? `target_${crypto.randomUUID().replaceAll('-', '')}`, [Validators.required, Validators.pattern(/^[A-Za-z0-9_-]+$/)]],
    category: this.fb.control<Category>(target?.category ?? 'EXPERIENCE', { nonNullable: true }),
    kind: this.fb.control<RelevanceTarget['kind']>(target?.kind ?? 'responsibility', { nonNullable: true }),
    treatment: this.fb.control<RelevanceTarget['treatment']>(target?.treatment ?? 'requirement', { nonNullable: true }),
    text: [target?.text ?? '', [Validators.required, Validators.maxLength(600)]],
    sourceQuote: [target?.source_quote ?? '', [Validators.maxLength(2000)]],
    importance: [target?.importance ?? 1, [Validators.required, Validators.min(1), Validators.max(5)]],
    evidenceTerms: [target?.evidence_terms.map(group => group.join(', ')).join('\n') ?? '']
  }); }
  newJd() { return this.fb.group({ relevanceTargets: this.fb.array<ReturnType<CreateComponent['newTarget']>>([]), minimumCoverage: [0], requiredSkills: [''], preferredSkills: [''], jobId: ['', [Validators.required, Validators.maxLength(64)]], title: ['', [Validators.required, Validators.maxLength(255)]],
    skills: [''], experience: [''], projects: [''], education: [''], years: [0, [Validators.required, Validators.min(0), Validators.max(100)]], degree: ['NONE'], fields: [''], fieldAliases: [''], levelAliases: [''], authorization: [false] }); }
  queryControl(jd: JdForm, category: Category): FormControl<string | null> { return jd.controls[category.toLowerCase() as 'skills' | 'experience' | 'projects' | 'education']; }
  categoryLabel(category: Category): string { return category.charAt(0) + category.slice(1).toLowerCase(); }
  add(): void { if (this.jds.length < this.maxJds) { this.jds.push(this.newJd()); this.reviewing.set(false); } }
  remove(index: number): void { if (this.jds.length > 1) { this.jds.removeAt(index); this.reviewing.set(false); } }
  move(index: number, direction: number): void { const next = index + direction; if (next < 0 || next >= this.jds.length) return; const control = this.jds.at(index); this.jds.removeAt(index); this.jds.insert(next, control); this.reviewing.set(false); }
  jobs(): JobProfile[] { return this.jds.controls.map(jd => { const v = jd.getRawValue(); const queries: JobProfile['jd_category_queries'] = {};
    for (const category of categories) { const value = (v[category.toLowerCase() as 'skills' | 'experience' | 'projects' | 'education'] ?? '').trim(); if (value) queries[category] = value; }
    const fields = (v.fields ?? '').split(',').map(s => s.trim()).filter(Boolean);
    return { job_id: (v.jobId ?? '').trim(), title: (v.title ?? '').trim(), jd_category_queries: queries,
      relevance_contract: { version: 'relevance-v1', minimum_coverage: v.minimumCoverage ?? 0,
        targets: (v.relevanceTargets ?? []).map(target => ({ target_id: target.targetId ?? '',
          category: target.category, kind: target.kind, treatment: target.treatment,
          text: (target.text ?? '').trim(), source_quote: (target.sourceQuote || target.text || '').trim(), importance: Number(target.importance),
          evidence_terms: (target.evidenceTerms || (target.text ?? '').slice(0, 120)).split('\n').filter(line => line.trim()).map(line => line.split(',').map(term => term.trim()).filter(Boolean)) })) },
      must_have_skills: this.parseSkills(v.requiredSkills ?? ''), nice_to_have_skills: this.parseSkills(v.preferredSkills ?? ''),
      hard_filter_rules: { min_years_experience: Number(v.years), require_work_authorization: !!v.authorization,
        degree_requirement: v.degree === 'NONE' ? null : { level: v.degree ?? 'NONE', fields,
          field_aliases: (v.fieldAliases ?? '').split(',').map(s => s.trim()).filter(Boolean),
          level_aliases: (v.levelAliases ?? '').split(',').map(s => s.trim()).filter(Boolean) } } }; }); }
  queryNames(job: JobProfile): string { return Object.keys(job.jd_category_queries).join(', '); }
  review(): void {
    this.form.markAllAsTouched(); this.formError.set('');
    const jobs = this.jobs(); const ids = jobs.map(j => j.job_id);
    if (this.jds.controls.some(jd => !this.state(jd).approvedId)) this.formError.set('Upload, review and approve every JD before creating the campaign.');
    else if (this.form.invalid || jobs.some(j => !j.job_id || !j.title || !Object.keys(j.jd_category_queries).length || !Number.isFinite(j.hard_filter_rules.min_years_experience))) this.formError.set('Complete each JD and enter at least one category query.');
    else if (new Set(ids).size !== ids.length) this.formError.set('JD IDs must be distinct.');
    else if (this.jds.controls.some(jd => jd.controls.degree.value === 'NONE' && !!jd.controls.fields.value?.trim())) this.formError.set('Degree fields require a minimum degree.');
    else { this.reviewing.set(true); setTimeout(() => document.getElementById('review')?.focus(), 0); }
  }
  create(): void {
    this.review(); if (this.formError()) return;
    this.creating.set(true); this.createError.set('');
    this.api.create({ name: this.form.controls.name.value?.trim() || undefined, approved_jd_ids: this.jds.controls.map(jd => this.state(jd).approvedId!), idempotency_key: this.idempotencyKey }).pipe(finalize(() => this.creating.set(false))).subscribe({
      next: response => { this.campaignId.set(response.campaign_id); this.local.remember(response.campaign_id); },
      error: error => this.createError.set(describeError(error))
    });
  }
  chooseFile(event: Event): void {
    const selected = (event.target as HTMLInputElement).files?.[0] ?? null;
    this.file.set(selected); this.fileError.set(''); this.uploadError.set('');
    if (selected && (!selected.name.toLowerCase().endsWith('.zip') || selected.size > publicConfig.archiveMaxBytes)) this.fileError.set('Choose a ZIP archive no larger than 256 MiB.');
  }
  upload(): void {
    const file = this.file(), id = this.campaignId();
    if (!file || !id || this.fileError()) return;
    this.uploading.set(true); this.uploaded.set(0); this.uploadError.set('');
    this.api.upload(id, file).pipe(finalize(() => this.uploading.set(false))).subscribe({ next: event => {
      if (event.type === HttpEventType.UploadProgress && event.total) this.uploaded.set(Math.round(event.loaded / event.total * 100));
      if (event.type === HttpEventType.Response && event.body) { this.report.set(event.body); this.local.saveNames(id, event.body); if (event.body.accepted_count) void this.router.navigate(['/campaigns', id]); }
    }, error: error => this.uploadError.set(describeError(error)) });
  }
}
