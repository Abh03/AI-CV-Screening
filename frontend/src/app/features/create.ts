import { Component, inject, signal } from '@angular/core';
import { FormArray, FormBuilder, FormControl, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { HttpEventType } from '@angular/common/http';
import { finalize } from 'rxjs';
import { CampaignApi } from '../api/campaign-api';
import { categories, Category, JobProfile, UploadResponse } from '../api/contracts';
import { publicConfig } from '../core/config';
import { describeError } from '../core/http-errors';
import { LocalCampaigns } from '../core/local-campaigns';

type JdForm = ReturnType<CreateComponent['newJd']>;
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
      <div formArrayName="jds">@for (jd of jds.controls; track jd; let i = $index) {
        <section class="card" [formGroupName]="i"><div class="card-title"><h2>Job description {{ i + 1 }}</h2><div class="compact-actions"><button type="button" class="secondary" (click)="move(i, -1)" [disabled]="i === 0">Move up</button><button type="button" class="secondary" (click)="move(i, 1)" [disabled]="i === jds.length - 1">Move down</button><button type="button" class="secondary" (click)="remove(i)" [disabled]="jds.length === 1">Remove</button></div></div>
          <div class="grid two"><div><label [for]="'id-' + i">JD ID</label><input [id]="'id-' + i" formControlName="jobId" maxlength="64" [attr.aria-invalid]="jd.controls.jobId.invalid && jd.controls.jobId.touched"><small>Distinct ID, up to 64 characters.</small>@if (jd.controls.jobId.invalid && jd.controls.jobId.touched) { <p class="error" role="alert">Enter a JD ID.</p> }</div>
          <div><label [for]="'title-' + i">Title</label><input [id]="'title-' + i" formControlName="title" maxlength="255" [attr.aria-invalid]="jd.controls.title.invalid && jd.controls.title.touched">@if (jd.controls.title.invalid && jd.controls.title.touched) { <p class="error" role="alert">Enter a title.</p> }</div></div>
          <fieldset><legend>Category search queries</legend><p class="muted">Fill at least one. These guide evidence retrieval for this JD.</p>
            <div class="grid two">@for (category of categories; track category) { <div><label [for]="category + '-' + i">{{ categoryLabel(category) }}</label><textarea [id]="category + '-' + i" [formControl]="queryControl(jd, category)" rows="2"></textarea></div> }</div></fieldset>
          <fieldset><legend>Hard filters</legend><div class="grid three"><div><label [for]="'years-' + i">Minimum years of experience</label><input [id]="'years-' + i" type="number" min="0" step="0.5" formControlName="years"></div><div><label [for]="'degree-' + i">Minimum degree</label><select [id]="'degree-' + i" formControlName="degree">@for (level of degreeLevels; track level) { <option [value]="level">{{ level }}</option> }</select></div><div><label [for]="'fields-' + i">Degree fields</label><input [id]="'fields-' + i" formControlName="fields" placeholder="Computer Science, Engineering"><small>Comma-separated. Requires a degree level.</small></div></div>
            <label class="check"><input type="checkbox" formControlName="authorization">Require work authorization</label></fieldset>
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
  readonly form = this.fb.group({ jds: this.fb.array([this.newJd()]) });
  readonly formError = signal(''); readonly reviewing = signal(false); readonly creating = signal(false);
  readonly createError = signal(''); readonly campaignId = signal(''); readonly file = signal<File | null>(null);
  readonly fileError = signal(''); readonly uploadError = signal(''); readonly uploading = signal(false);
  readonly uploaded = signal(0); readonly report = signal<UploadResponse | null>(null);
  private idempotencyKey = crypto.randomUUID();
  get jds(): FormArray<JdForm> { return this.form.controls.jds; }
  newJd() { return this.fb.group({ jobId: ['', [Validators.required, Validators.maxLength(64)]], title: ['', [Validators.required, Validators.maxLength(255)]],
    skills: [''], experience: [''], projects: [''], education: [''], years: [0, [Validators.required, Validators.min(0)]], degree: ['NONE'], fields: [''], authorization: [true] }); }
  queryControl(jd: JdForm, category: Category): FormControl<string | null> { return jd.controls[category.toLowerCase() as 'skills' | 'experience' | 'projects' | 'education']; }
  categoryLabel(category: Category): string { return category.charAt(0) + category.slice(1).toLowerCase(); }
  add(): void { if (this.jds.length < this.maxJds) { this.jds.push(this.newJd()); this.reviewing.set(false); } }
  remove(index: number): void { if (this.jds.length > 1) { this.jds.removeAt(index); this.reviewing.set(false); } }
  move(index: number, direction: number): void { const next = index + direction; if (next < 0 || next >= this.jds.length) return; const control = this.jds.at(index); this.jds.removeAt(index); this.jds.insert(next, control); this.reviewing.set(false); }
  jobs(): JobProfile[] { return this.jds.controls.map(jd => { const v = jd.getRawValue(); const queries: JobProfile['jd_category_queries'] = {};
    for (const category of categories) { const value = (v[category.toLowerCase() as 'skills' | 'experience' | 'projects' | 'education'] ?? '').trim(); if (value) queries[category] = value; }
    const fields = (v.fields ?? '').split(',').map(s => s.trim()).filter(Boolean);
    return { job_id: (v.jobId ?? '').trim(), title: (v.title ?? '').trim(), jd_category_queries: queries,
      hard_filter_rules: { min_years_experience: Number(v.years), require_work_authorization: !!v.authorization,
        degree_requirement: v.degree === 'NONE' ? null : { level: v.degree ?? 'NONE', fields, field_aliases: [], level_aliases: [] } } }; }); }
  queryNames(job: JobProfile): string { return Object.keys(job.jd_category_queries).join(', '); }
  review(): void {
    this.form.markAllAsTouched(); this.formError.set('');
    const jobs = this.jobs(); const ids = jobs.map(j => j.job_id);
    if (this.form.invalid || jobs.some(j => !j.job_id || !j.title || !Object.keys(j.jd_category_queries).length || !Number.isFinite(j.hard_filter_rules.min_years_experience))) this.formError.set('Complete each JD and enter at least one category query.');
    else if (new Set(ids).size !== ids.length) this.formError.set('JD IDs must be distinct.');
    else if (this.jds.controls.some(jd => jd.controls.degree.value === 'NONE' && !!jd.controls.fields.value?.trim())) this.formError.set('Degree fields require a minimum degree.');
    else { this.reviewing.set(true); setTimeout(() => document.getElementById('review')?.focus(), 0); }
  }
  create(): void {
    this.review(); if (this.formError()) return;
    this.creating.set(true); this.createError.set('');
    this.api.create({ job_profiles: this.jobs(), idempotency_key: this.idempotencyKey }).pipe(finalize(() => this.creating.set(false))).subscribe({
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
