import { Component, DestroyRef, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { EMPTY, catchError, switchMap } from 'rxjs';
import { CandidateSearch, RecruiterApi, RecruiterCandidate } from '../api/recruiter-api';
import { CandidateDetailComponent } from '../shared/candidate-detail';
import { CvViewerComponent } from '../shared/cv-viewer';
import { displayReason, recruiterLabel } from '../shared/recruiter-display';
import { describeError } from '../core/http-errors';

@Component({ standalone: true, imports: [FormsModule, RouterLink, CandidateDetailComponent, CvViewerComponent], template: `
  <div class="page-heading"><div><p class="eyebrow">Candidate search</p><h1>Find a candidate</h1><p>See every role assessment, the stage reached, and the reason for an outcome.</p></div><a routerLink="/">Back to campaigns</a></div>
  <section class="card"><form (ngSubmit)="search()" class="candidate-search"><div><label for="candidate-search">Name in filename, candidate ID, skill, or CV text</label><input id="candidate-search" name="query" [(ngModel)]="query" maxlength="255" placeholder="e.g. Jane, candidate ID, Python"></div><button [disabled]="loading()">Search candidates</button></form>
    @if (campaign) { <p>Searching within this campaign. <a routerLink="/candidates" [queryParams]="{ search: query }">Search all my campaigns</a></p> }
  </section>
  @if (error()) { <p class="error-banner" role="alert">{{ error() }}</p> }
  <p role="status">{{ loading() ? 'Searching all processing stages…' : total() + ' matching candidates. Each role is shown separately.' }}</p>
  <div class="table-scroll"><table><thead><tr><th>Candidate</th><th>Campaign / role</th><th>Screening status</th><th>Stage reached and reason</th><th>Recruiter decision</th><th>Actions</th></tr></thead><tbody>
    @for (row of rows(); track row.pair_id) { <tr><td><strong>{{ row.source_filename }}</strong><small>{{ row.candidate_id }}</small></td><td>{{ row.campaign_name || 'Campaign' }}<br><a [routerLink]="['/campaigns', row.campaign_id, 'jds', row.jd_key]">{{ row.role_title }}</a></td><td><span class="badge">{{ label(row.status) }}</span></td><td><ol class="compact-timeline">@for (stage of row.stage_history; track stage.stage) { <li><strong>{{ stage.stage }}:</strong> {{ label(stage.state) }}@if (stage.reason) { <p>{{ reason(stage.reason) }}</p> }</li> }</ol></td><td>{{ label(row.review.decision) }}</td><td><div class="row-actions"><button class="text-button" (click)="cv.set(row)">View CV</button><button class="text-button" (click)="selected.set(row.pair_id)">Review full history</button></div></td></tr> }
    @empty { @if (!loading()) { <tr><td colspan="6"><div class="empty-state"><h2>No matching candidates</h2><p>Try a filename, candidate ID, or a phrase from the CV.</p></div></td></tr> } }
  </tbody></table></div>
  @if (rejectedTotal()) { <section class="card"><h2>Files not accepted at upload ({{ rejectedTotal() }})</h2><p>These files stopped at campaign intake and have no candidate assessment or stored CV.</p><div class="table-scroll"><table><thead><tr><th>File</th><th>Campaign</th><th>Stage</th><th>Reason</th></tr></thead><tbody>@for (item of rejected(); track $index) { <tr><td>{{ item.filename }}</td><td><a [routerLink]="['/campaigns', item.campaign_id]">{{ item.campaign_name || item.campaign_id }}</a></td><td>CV upload intake</td><td>{{ reason(item.reason) }}</td></tr> }</tbody></table></div></section> }
  <div class="pagination"><button class="secondary" (click)="goPage(-1)" [disabled]="page() === 1 || loading()">Previous</button><span>Page {{ page() }} of {{ maxPage() }}</span><button class="secondary" (click)="goPage(1)" [disabled]="page() >= maxPage() || loading()">Next</button></div>
  @if (selected(); as pair) { <candidate-detail [pair]="pair" (closed)="selected.set(null)" (changed)="search()" /> }
  @if (cv(); as candidate) { <cv-viewer [campaign]="candidate.campaign_id" [candidate]="candidate.candidate_id" (closed)="cv.set(null)" /> }
` })
export class CandidatesComponent {
  private readonly route = inject(ActivatedRoute); private readonly router = inject(Router); private readonly api = inject(RecruiterApi); private readonly destroyRef = inject(DestroyRef);
  query = ''; campaign = ''; readonly rows = signal<RecruiterCandidate[]>([]); readonly total = signal(0); readonly page = signal(1);
  readonly loading = signal(false); readonly error = signal(''); readonly selected = signal<string | null>(null); readonly cv = signal<RecruiterCandidate | null>(null);
  readonly rejected = signal<CandidateSearch['intake_rejections']>([]); readonly rejectedTotal = signal(0);
  readonly label = recruiterLabel; readonly reason = displayReason;
  constructor() {
    this.route.queryParamMap.pipe(switchMap(params => {
      this.query = params.get('search') || ''; this.campaign = params.get('campaign') || '';
      const page = Number(params.get('page')); this.page.set(Number.isSafeInteger(page) && page > 0 ? page : 1);
      this.loading.set(true); this.error.set(''); this.rows.set([]);
      return this.api.search(this.query, this.campaign, (this.page() - 1) * 30).pipe(catchError(e => { this.loading.set(false); this.error.set(describeError(e)); return EMPTY; }));
    }), takeUntilDestroyed(this.destroyRef)).subscribe(result => { this.setResults(result); this.loading.set(false); });
  }
  search(): void { void this.router.navigate([], { relativeTo: this.route, queryParams: { search: this.query.trim(), campaign: this.campaign || null, page: 1 } }).then(changed => {
    if (!changed) this.api.search(this.query, this.campaign).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: result => this.setResults(result), error: e => this.error.set(describeError(e)) });
  }); }
  goPage(delta: number): void { void this.router.navigate([], { relativeTo: this.route, queryParams: { search: this.query, campaign: this.campaign || null, page: this.page() + delta } }); }
  private setResults(result: CandidateSearch): void { this.rows.set(result.results); this.total.set(result.total); this.rejected.set(result.intake_rejections || []); this.rejectedTotal.set(result.intake_rejection_total || 0); }
  maxPage(): number { return Math.max(1, Math.ceil(Math.max(this.total(), this.rejectedTotal()) / 30)); }
}
