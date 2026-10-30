import { Component, DestroyRef, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { catchError, EMPTY, filter, fromEvent, merge, switchMap, timer } from 'rxjs';
import { RecruiterApi, RecruiterCandidate } from '../api/recruiter-api';
import { CandidateDetailComponent } from '../shared/candidate-detail';
import { describeError } from '../core/http-errors';
import { downloadBlob, recruiterLabel, displayReason, score } from '../shared/recruiter-display';

@Component({ standalone: true, imports: [RouterLink, CandidateDetailComponent], template: `
  <div class="page-heading"><div><p class="eyebrow">Single CV analysis</p><h1>{{ candidate()?.source_filename || 'Tracking CV' }}</h1>
    <p>{{ candidate()?.role_title || 'Checking this CV against the selected JD' }}</p></div>
    <div class="actions"><a routerLink="/">Campaigns</a><button class="secondary" (click)="refresh()">Refresh</button>
      @if (candidate() && terminal()) { <button (click)="download()" [disabled]="downloading()">{{ downloading() ? 'Preparing PDF…' : 'Download report PDF' }}</button> }
    </div></div>
  @if (error()) { <p class="error-banner" role="alert">{{ error() }}</p> }
  @if (candidate(); as row) {
    <section class="card single-outcome"><p class="eyebrow">Result</p><h2>{{ label(row.status) }}</h2>
      @if (row.score !== null) {
        <p class="single-total"><strong>{{ score(row.score) }}<small>/ 100 overall fit</small></strong><span>{{ row.tier ? label(row.tier) : 'Human review required' }}</span></p>
        @if (row.summary) { <p class="single-summary">{{ row.summary }}</p> }
        <p class="muted">The detailed assessment explains each category score below. The overall score is calculated from those scores using the scoring weights.</p>
      } @else { <p>The analysis stopped before a detailed score was available. The stage trace and findings below explain where and why.</p> }
      @if (row.failure_code) { <p><strong>Reason:</strong> {{ reason(row.failure_code) }}</p> }
      @if (row.error_message) { <p>{{ row.error_message }}</p> }
      <button class="secondary" (click)="showDetail.set(true)">Open full assessment and CV</button>
    </section>
    @if (row.flags.length || row.review_reasons.length || eligibilityFindings(row).length) {
      <section class="card"><h2>Findings to verify</h2>
        @for (check of eligibilityFindings(row); track $index) { <div class="single-finding"><strong>{{ label(check.rule) }} · {{ label(check.status) }}</strong><p>{{ check.message }}</p></div> }
        @for (flag of row.flags; track $index) { <div class="single-finding"><strong>{{ label(flag.severity) }} {{ label(flag.type) }}</strong><p>{{ flag.description }}</p></div> }
        @if (!row.flags.length) { @for (item of row.review_reasons; track $index) { <p>{{ reason(item) }}</p> } }
      </section>
    }
    @if (row.score !== null) {
      <section class="card"><h2>Why the CV received these scores</h2><p class="muted">The rationale is from the detailed assessment. It reflects the evidence available in this CV.</p>
        <div class="single-score-grid">@for (entry of categories; track entry) {
          <article class="single-score-card"><div class="single-score-head"><h3>{{ label(entry) }}</h3><strong>{{ score(row.category_scores[entry]) }} / 100</strong></div>
            <p>{{ row.assessments?.[entry]?.rationale || 'No category explanation was stored.' }}</p>
            @if (row.assessments?.[entry]?.claims?.length) { <details><summary>Supporting assessment claims</summary><ul>
              @for (claim of row.assessments![entry].claims || []; track $index) { <li>{{ claim.claim }} @if (claim.quote) { <blockquote>{{ claim.quote }}</blockquote> }</li> }
            </ul></details> }
          </article>
        }</div>
        <p class="muted single-formula">Overall = skills × 40% + experience × 30% + projects × 20% + education × 10%.</p>
      </section>
    }
    @if (row.improvement_suggestions?.length) { <section class="card"><h2>How this CV could document a stronger match</h2><ol class="single-suggestions">
      @for (item of row.improvement_suggestions || []; track $index) { <li>{{ item }}</li> }
    </ol><p class="muted">These suggestions identify evidence the CV could add or clarify; they do not guarantee a score.</p></section> }
    <section class="card"><h2>Stage trace</h2><ol class="stage-timeline">@for (stage of row.stage_history; track stage.stage) {
      <li><strong>{{ stage.stage }}</strong><span>{{ label(stage.state) }}</span>@if (stage.reason) { <p>{{ reason(stage.reason) }}</p> }</li>
    }</ol></section>
    @if (row.stage2_score !== null) { <section class="card"><h2>Job requirement matching</h2>
      <p><strong>Supported JD relevance coverage: {{ (row.stage2_score * 100).toFixed(1) }}%</strong></p>
      <p class="muted">This is a retrieval coverage measure, not the final fit score.</p>
      <details><summary>See all {{ (row.stage2_target_assessments || []).length }} requirement findings</summary><div class="single-targets">
        @for (target of row.stage2_target_assessments || []; track target.target_id) { <div class="single-target"><strong>{{ target.target_text || target.target_id }}</strong><span>{{ label(target.status) }}</span><p>{{ target.supporting_text || 'No supporting passage found' }}</p></div> }
      </div></details>
    </section> }
  } @else { <p role="status">Loading analysis…</p> }
  @if (showDetail()) { <candidate-detail [pair]="pair" (closed)="showDetail.set(false)" /> }
`, styles: [`
  .single-outcome{border-top:4px solid #0c7166}.single-outcome h2{margin:.2rem 0 .8rem}
  .single-total{display:flex;align-items:center;gap:1rem;flex-wrap:wrap}.single-total strong{font-size:2.25rem;color:#0c7166;line-height:1}
  .single-total small{font-size:.85rem;font-weight:500;margin-top:.4rem}.single-total span{font-weight:700}
  .single-summary{font-size:1.08rem;max-width:80ch}.single-finding{padding:.75rem 0;border-top:1px solid #dce5e9}.single-finding p{margin:.25rem 0}
  .single-score-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem}.single-score-card{border:1px solid #dce5e9;border-radius:8px;padding:1rem;background:#f8fbfb;min-width:0}
  .single-score-head{display:flex;justify-content:space-between;align-items:baseline;gap:.5rem}.single-score-head h3{margin:0}.single-score-head strong{white-space:nowrap;color:#0c7166}
  .single-score-card p{margin:.6rem 0}.single-score-card li{margin:.5rem 0}.single-score-card blockquote{font-size:.9rem}.single-formula{margin:1rem 0 0}
  .single-suggestions li{margin:.6rem 0}.single-targets{margin-top:.5rem}.single-target{padding:.75rem 0;border-top:1px solid #dce5e9}.single-target span{display:inline-block;margin-left:.75rem;color:#526a79}.single-target p{margin:.25rem 0;color:#425b68}
  @media(max-width:760px){.single-score-grid{grid-template-columns:1fr}}
` ] })
export class SingleCvComponent {
  private readonly route = inject(ActivatedRoute);
  private readonly api = inject(RecruiterApi);
  private readonly destroyRef = inject(DestroyRef);
  readonly pair = this.route.snapshot.paramMap.get('pair') ?? '';
  readonly candidate = signal<RecruiterCandidate | null>(null);
  readonly error = signal(''); readonly showDetail = signal(false); readonly downloading = signal(false);
  readonly categories = ['skills', 'experience', 'projects', 'education'];
  readonly label = recruiterLabel; readonly reason = displayReason; readonly score = score;
  constructor() {
    merge(timer(0, 3000), fromEvent(document, 'visibilitychange')).pipe(
      filter(() => !document.hidden && !this.terminal()),
      switchMap(() => this.api.detail(this.pair).pipe(catchError(error => { this.error.set(describeError(error)); return EMPTY; }))),
      takeUntilDestroyed(this.destroyRef)
    ).subscribe(row => { this.candidate.set(row); this.error.set(''); });
  }
  terminal(): boolean { return ['SUCCESS', 'REVIEW_REQUIRED', 'EVALUATION_FAILED', 'FILTER_REJECTED', 'PROCESSING_FAILED', 'EXTRACTION_FAILED', 'CUTOFF_EXCLUDED'].includes(this.candidate()?.status || ''); }
  eligibilityFindings(row: RecruiterCandidate) { return row.stage1_checks.filter(check => check.status === 'FAIL' || check.status === 'REVIEW'); }
  refresh(): void { this.api.detail(this.pair).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: row => { this.candidate.set(row); this.error.set(''); }, error: e => this.error.set(describeError(e)) }); }
  download(): void {
    const row = this.candidate();
    if (!row || this.downloading()) return;
    this.downloading.set(true); this.error.set('');
    this.api.singleCvReport(this.pair).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: blob => { downloadBlob(blob, `${row.source_filename?.replace(/\.pdf$/i, '') || 'candidate'}-analysis.pdf`); this.downloading.set(false); },
      error: e => { this.error.set(describeError(e)); this.downloading.set(false); }
    });
  }
}
