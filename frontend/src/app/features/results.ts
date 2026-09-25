import { Component, DestroyRef, inject, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { EMPTY, catchError, distinctUntilChanged, map, switchMap } from 'rxjs';
import { CampaignApi } from '../api/campaign-api';
import { OutcomeStatus, outcomeStatuses, PairResult, ResultsPage } from '../api/contracts';
import { describeError } from '../core/http-errors';
import { LocalCampaigns } from '../core/local-campaigns';

const pageSize = 30;
type Tab = 'SUCCESS' | OutcomeStatus;

@Component({ standalone: true, imports: [RouterLink], template: `
  <div class="page-heading"><div><p class="eyebrow">Per-JD results</p><h1>JD {{ jd }}</h1><p>Only successful evaluations appear in ranking. Review and failures remain separate.</p></div><a [routerLink]="['/campaigns', id]">Back to campaign</a></div>
  <nav class="tab-nav" aria-label="Result status">@for (status of tabs; track status) { <a [routerLink]="[]" [queryParams]="{ status: status, page: 1 }" [attr.aria-current]="tab() === status ? 'page' : null">{{ label(status) }}</a> }</nav>
  @if (error()) { <div class="error-banner" role="alert">{{ error() }}</div> }
  @if (loading()) { <p role="status">Loading results…</p> }
  @if (data(); as result) { <p class="muted" role="status">{{ result.total }} {{ label(tab()).toLowerCase() }} result(s). Page {{ page() }} of {{ maxPage(result.total) }}.</p>
    @if (result.results.length) { <div class="table-scroll"><table><thead><tr>@if (tab() === 'SUCCESS') { <th scope="col">Rank</th> }<th scope="col">Candidate</th><th scope="col">Status</th><th scope="col">Final score</th><th scope="col">Tier</th><th scope="col">Details</th></tr></thead><tbody>
      @for (row of result.results; track row.candidate_id) { <tr>@if (tab() === 'SUCCESS') { <td>{{ row.rank ?? '—' }}</td> }<td><strong>{{ name(row) ?? row.candidate_id }}</strong>@if (name(row)) { <small>Candidate ID: {{ row.candidate_id }}</small> }</td><td><span class="badge">{{ label(row.status) }}</span>@if (row.provisional) { <span class="badge warning">Provisional</span> }@if (row.is_mock) { <span class="badge warning">Mock result</span> }</td><td>{{ score(row.score) }}</td><td>{{ row.tier ?? '—' }}</td><td><button type="button" class="text-button" (click)="select(row)">View details</button></td></tr> }
    </tbody></table></div>
      <div class="pagination"><button type="button" class="secondary" (click)="goPage(-1)" [disabled]="page() === 1">Previous</button><span>Page {{ page() }}</span><button type="button" class="secondary" (click)="goPage(1)" [disabled]="page() >= maxPage(result.total)">Next</button></div>
    } @else { <section class="card"><h2>No results here yet</h2><p>Refresh the campaign monitor to check processing status.</p></section> }
  }
  @if (selected(); as row) { <div class="panel-backdrop" (click)="close()"></div><aside class="detail-panel" role="dialog" aria-modal="true" aria-labelledby="detail-title" tabindex="-1" id="detail-panel" (keydown.escape)="close()" (keydown.tab)="trapTab($event)"><div class="card-title"><h2 id="detail-title">Candidate details</h2><button type="button" class="secondary" (click)="close()" id="detail-close">Close</button></div>
    <p><strong>{{ name(row) ?? row.candidate_id }}</strong></p><p>Candidate ID: {{ row.candidate_id }}</p><p>Status: {{ label(row.status) }}@if (row.provisional) { · Provisional }@if (row.is_mock) { · Mock result }</p><p>Final score: {{ score(row.score) }} · Tier: {{ row.tier ?? '—' }}</p>
    <h3>Category scores</h3>@if (keys(row.category_scores).length) { <ul class="counts">@for (key of keys(row.category_scores); track key) { <li><span>{{ key }}</span><strong>{{ score(row.category_scores[key]) }}</strong></li> }</ul> } @else { <p>No category scores available.</p> }
    <h3>Stage 1</h3><p>Decision: {{ row.stage1_decision ?? 'Unavailable' }}</p>@if (row.stage1_checks.length) { <ul>@for (check of row.stage1_checks; track $index) { <li><pre>{{ formatCheck(check) }}</pre></li> }</ul> } @else { <p>No check details available.</p> }
    @if (row.verification_reasons.length) { <h3>Verification reasons</h3><ul>@for (reason of row.verification_reasons; track reason) { <li>{{ reason }}</li> }</ul> }
    @if (row.review_reasons.length) { <h3>Review reasons</h3><ul>@for (reason of row.review_reasons; track reason) { <li>{{ reason }}</li> }</ul> }
    @if (row.failure_code || row.error_message) { <h3>Outcome</h3><p>{{ row.failure_code ?? row.error_message }}</p> }
    <h3>Evidence references</h3>@if (row.evidence.length) { <ul>@for (e of row.evidence; track e.citation) { <li><strong>{{ e.citation }}</strong><br>Document {{ e.document_id ?? '—' }}, chunk {{ e.chunk_id ?? '—' }}<pre>{{ formatCheck(e.source_location) }}</pre></li> }</ul> } @else { <p>No verified citations available.</p> }<p class="muted">The backend does not provide an excerpt or document link here.</p>
  </aside> }
` })
export class ResultsComponent {
  private readonly route = inject(ActivatedRoute); private readonly api = inject(CampaignApi);
  private readonly destroyRef = inject(DestroyRef); private readonly router = inject(Router);
  readonly local = inject(LocalCampaigns);
  readonly id = this.route.snapshot.paramMap.get('id') ?? ''; readonly jd = this.route.snapshot.paramMap.get('jd') ?? '';
  readonly tabs: Tab[] = ['SUCCESS', ...outcomeStatuses];
  readonly tab = signal<Tab>('SUCCESS'); readonly page = signal(1); readonly data = signal<ResultsPage | null>(null);
  readonly error = signal(''); readonly loading = signal(false); readonly selected = signal<PairResult | null>(null);
  private trigger: HTMLElement | null = null;
  constructor() {
    this.api.status(this.id).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: campaign => { if (campaign.intake_report) this.local.saveNames(this.id, campaign.intake_report); }, error: () => { /* Result request reports access and service errors. */ } });
    this.route.queryParamMap.pipe(map(params => {
      const raw = params.get('status'); const tab: Tab = raw === 'SUCCESS' || outcomeStatuses.some(s => s === raw) ? raw as Tab : 'SUCCESS';
      const page = Number(params.get('page')); return { tab, page: Number.isSafeInteger(page) && page > 0 ? page : 1 };
    }), distinctUntilChanged((a, b) => a.tab === b.tab && a.page === b.page), switchMap(({ tab, page }) => {
      this.tab.set(tab); this.page.set(page); this.selected.set(null); this.loading.set(true); this.error.set(''); this.data.set(null);
      const call = tab === 'SUCCESS' ? this.api.rankings(this.id, this.jd, pageSize, (page - 1) * pageSize) : this.api.outcomes(this.id, this.jd, tab, pageSize, (page - 1) * pageSize);
      return call.pipe(catchError(error => { this.error.set(describeError(error)); this.loading.set(false); return EMPTY; }));
    }), takeUntilDestroyed(this.destroyRef)).subscribe(result => { this.data.set(result); this.loading.set(false); });
  }
  label(status: string): string { return status.replaceAll('_', ' ').toLowerCase().replace(/^./, c => c.toUpperCase()); }
  name(row: PairResult): string | null { return row.source_filename ?? this.local.name(this.id, row.candidate_id); }
  score(value: number | null | undefined): string { return value === null || value === undefined ? '—' : String(value); }
  keys(value: Record<string, unknown>): string[] { return Object.keys(value); }
  maxPage(total: number): number { return Math.max(1, Math.ceil(total / pageSize)); }
  formatCheck(value: unknown): string { return value == null ? 'Location unavailable' : JSON.stringify(value, null, 2); }
  goPage(delta: number): void { void this.router.navigate([], { relativeTo: this.route, queryParams: { status: this.tab(), page: this.page() + delta } }); }
  select(row: PairResult): void { this.trigger = document.activeElement as HTMLElement; this.selected.set(row); setTimeout(() => document.getElementById('detail-close')?.focus(), 0); }
  close(): void { this.selected.set(null); setTimeout(() => this.trigger?.focus(), 0); }
  trapTab(event: Event): void { event.preventDefault(); document.getElementById('detail-close')?.focus(); }
}
