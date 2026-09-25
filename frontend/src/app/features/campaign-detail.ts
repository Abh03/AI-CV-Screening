import { Component, DestroyRef, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { forkJoin, fromEvent, merge, of, switchMap, timer, filter, EMPTY, catchError } from 'rxjs';
import { CampaignApi } from '../api/campaign-api';
import { CampaignStatus, JdDefinition, JdSummary, categories } from '../api/contracts';
import { publicConfig } from '../core/config';
import { describeError } from '../core/http-errors';
import { LocalCampaigns } from '../core/local-campaigns';
import { countEntries, pairProgress } from '../shared/progress';
import { HttpEventType } from '@angular/common/http';
import { IntakeReport } from '../api/contracts';

@Component({ standalone: true, imports: [RouterLink], template: `
  <div class="page-heading"><div><p class="eyebrow">Campaign monitor</p><h1>Campaign {{ id }}</h1><p>Updates while this tab is visible. Results are separate for each JD.</p></div><button type="button" class="secondary" (click)="refresh()" [disabled]="loading()">Refresh now</button></div>
  @if (error()) { <div class="error-banner" role="alert">{{ error() }}</div> }
  @if (loading() && !campaign()) { <p role="status">Loading campaign…</p> }
  @if (campaign(); as c) {
    @if (c.status === 'INTAKE') { <section class="card"><h2>Continue ZIP intake</h2><p>Select a ZIP of PDF CVs. If a previous upload was interrupted, refresh the campaign first to check whether it was accepted.</p>
      <label for="resume-archive">ZIP archive</label><input id="resume-archive" type="file" accept=".zip,application/zip" (change)="chooseFile($event)" [disabled]="uploading()">
      @if (file()) { <p>{{ file()!.name }} · {{ (file()!.size / 1048576).toFixed(1) }} MiB</p> }
      @if (uploadError()) { <p class="error" role="alert">{{ uploadError() }}</p> }
      @if (uploading()) { <p role="status">{{ uploaded() === 100 ? 'Upload sent; waiting for server intake.' : 'Sending ZIP: ' + uploaded() + '%' }}</p><progress [value]="uploaded()" max="100"></progress> }
      <button type="button" (click)="upload()" [disabled]="!file() || uploading()">Upload ZIP</button>
    </section> }
    <div class="grid three"><section class="stat"><span>Status</span><strong>{{ c.status }}</strong></section><section class="stat"><span>Accepted CVs</span><strong>{{ c.counts.cvs }}</strong></section><section class="stat"><span>Candidate–JD pairs</span><strong>{{ progress(c).complete }} / {{ progress(c).total }}</strong></section></div>
    @if (progress(c).total) { <progress [value]="progress(c).complete" [max]="progress(c).total" [attr.aria-label]="'Terminal pairs: ' + progress(c).complete + ' of ' + progress(c).total"></progress><p class="muted">{{ progress(c).percent }}% of pairs terminal. No completion time estimate is available.</p> }
    <p class="muted" aria-live="polite">Last updated: {{ updated() ? updated()!.toLocaleTimeString() : 'Never' }}</p>
    @if (c.stage3_retry_waiting) { <div class="notice" role="status">{{ c.stage3_retry_waiting }} pair(s) waiting for Stage 3 rate-limit retry.</div> }
    <section class="card"><h2>Stage 0 extraction</h2>@if (entries(c.stage0).length) { <ul class="counts">@for (entry of entries(c.stage0); track entry.label) { <li><span>{{ entry.label }}</span><strong>{{ entry.count }}</strong></li> }</ul> } @else { <p>No PDFs in extraction yet.</p> }</section>
    @if (c.intake_report ?? uploadReport(); as report) { <section class="card"><h2>ZIP intake</h2><p>{{ report.accepted_count }} accepted · {{ report.rejected_count }} rejected</p>@if (report.rejected.length) { <table><caption>Rejected ZIP entries</caption><thead><tr><th scope="col">File</th><th scope="col">Code</th></tr></thead><tbody>@for (item of report.rejected; track $index) { <tr><td>{{ item.name }}</td><td>{{ item.code }}</td></tr> }</tbody></table> }@if (report.accepted_count === 0) { <p>No PDFs were accepted. Select another ZIP to continue intake.</p> }</section> }
    <section><h2>Job descriptions</h2><div class="grid two">@for (jd of jds(); track jd.jd_key) { <article class="card"><div class="card-title"><h3>{{ jd.title }}</h3><span class="badge">{{ jd.status }}</span></div><p class="muted">ID: {{ jd.jd_key }} · Stage 3 cap: {{ jd.stage3_cap }}</p>
      @if (jd.stage3_retry_waiting) { <p>{{ jd.stage3_retry_waiting }} waiting for Stage 3 retry</p> }
      <ul class="counts">@for (entry of entries(jd.counts); track entry.label) { <li><span>{{ entry.label }}</span><strong>{{ entry.count }}</strong></li> }</ul><div class="actions"><a class="button" [routerLink]="['/campaigns', id, 'jds', jd.jd_key]">View results</a><button type="button" class="secondary" (click)="viewDefinition(jd.jd_key)">View submitted JD</button></div></article> } @empty { <p>JD summaries are not available yet.</p> }</div></section>
    @if (definitionLoading()) { <p role="status">Loading submitted JD…</p> }
    @if (definitionError()) { <p class="error" role="alert">{{ definitionError() }}</p> }
    @if (definition(); as d) { <section class="card" id="jd-definition"><div class="card-title"><h2>Submitted JD: {{ d.job_profile.title }}</h2><button type="button" class="secondary" (click)="definition.set(null)">Close</button></div><p>ID: {{ d.jd_key }} · Stage 3 cap: {{ d.stage3_cap }}</p>
      <h3>Category queries</h3><dl>@for (category of categories; track category) { @if (d.job_profile.jd_category_queries[category]; as query) { <dt><strong>{{ category }}</strong></dt><dd>{{ query }}</dd> } }</dl>
      <h3>Hard filters</h3><p>Minimum experience: {{ d.job_profile.hard_filter_rules.min_years_experience }} years</p><p>Work authorization required: {{ d.job_profile.hard_filter_rules.require_work_authorization ? 'Yes' : 'No' }}</p>
      @if (d.job_profile.hard_filter_rules.degree_requirement; as degree) { <p>Degree level: {{ degree.level }}</p><p>Level aliases: {{ degree.level_aliases.join(', ') || 'None' }}</p><p>Fields: {{ degree.fields.join(', ') || 'Any' }}</p><p>Field aliases: {{ degree.field_aliases.join(', ') || 'None' }}</p> }
    </section> }
  }
` })
export class CampaignDetailComponent {
  private readonly route = inject(ActivatedRoute); private readonly api = inject(CampaignApi);
  private readonly local = inject(LocalCampaigns); private readonly destroyRef = inject(DestroyRef);
  readonly id = this.route.snapshot.paramMap.get('id') ?? '';
  readonly campaign = signal<CampaignStatus | null>(null); readonly jds = signal<JdSummary[]>([]);
  readonly loading = signal(false); readonly error = signal(''); readonly updated = signal<Date | null>(null);
  readonly file = signal<File | null>(null); readonly uploading = signal(false); readonly uploaded = signal(0);
  readonly uploadError = signal(''); readonly uploadReport = signal<IntakeReport | null>(null);
  readonly definition = signal<JdDefinition | null>(null); readonly definitionLoading = signal(false);
  readonly definitionError = signal(''); readonly categories = categories;
  readonly entries = countEntries; readonly progress = (c: CampaignStatus) => pairProgress(c.counts);
  constructor() {
    merge(timer(0, publicConfig.pollMs), fromEvent(document, 'visibilitychange')).pipe(
      filter(() => !document.hidden && !this.loading() && !this.isTerminal()),
      switchMap(() => { this.loading.set(true); return forkJoin({ campaign: this.api.status(this.id), jds: this.api.jds(this.id) }).pipe(
        catchError(error => { this.error.set(describeError(error)); this.loading.set(false); return EMPTY; }) ); }),
      takeUntilDestroyed(this.destroyRef)
    ).subscribe(({ campaign, jds }) => { this.campaign.set(campaign); this.local.remember(this.id); if (campaign.intake_report) this.local.saveNames(this.id, campaign.intake_report); this.jds.set(jds.jds); this.updated.set(new Date()); this.error.set(''); this.loading.set(false); });
  }
  private isTerminal(): boolean { const c = this.campaign(); return !!c && (c.status === 'COMPLETED' || c.status === 'FAILED' || c.status === 'CANCELLED'); }
  viewDefinition(jdKey: string): void {
    this.definition.set(null); this.definitionError.set(''); this.definitionLoading.set(true);
    this.api.definition(this.id, jdKey).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: definition => { this.definition.set(definition); this.definitionLoading.set(false); setTimeout(() => document.getElementById('jd-definition')?.scrollIntoView(), 0); },
      error: error => { this.definitionError.set(describeError(error)); this.definitionLoading.set(false); }
    });
  }
  refresh(): void { if (this.loading()) return; this.loading.set(true); forkJoin({ campaign: this.api.status(this.id), jds: this.api.jds(this.id) }).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
    next: ({ campaign, jds }) => { this.campaign.set(campaign); this.local.remember(this.id); if (campaign.intake_report) this.local.saveNames(this.id, campaign.intake_report); this.jds.set(jds.jds); this.updated.set(new Date()); this.error.set(''); this.loading.set(false); },
    error: error => { this.error.set(describeError(error)); this.loading.set(false); }
  }); }
  chooseFile(event: Event): void {
    const file = (event.target as HTMLInputElement).files?.[0] ?? null;
    this.file.set(file); this.uploadError.set('');
    if (file && (!file.name.toLowerCase().endsWith('.zip') || file.size > publicConfig.archiveMaxBytes)) this.uploadError.set('Choose a ZIP archive no larger than 256 MiB.');
  }
  upload(): void {
    const file = this.file(); if (!file || this.uploadError() || this.uploading()) return;
    this.uploading.set(true); this.uploaded.set(0);
    this.api.upload(this.id, file).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: event => { if (event.type === HttpEventType.UploadProgress && event.total) this.uploaded.set(Math.round(event.loaded / event.total * 100));
        if (event.type === HttpEventType.Response && event.body) { this.uploadReport.set(event.body); this.local.saveNames(this.id, event.body); this.uploading.set(false); this.refresh(); } },
      error: error => { this.uploadError.set(describeError(error)); this.uploading.set(false); }
    });
  }
}
