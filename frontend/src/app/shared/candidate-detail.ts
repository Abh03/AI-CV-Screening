import { Component, DestroyRef, ElementRef, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DatePipe } from '@angular/common';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Member, RecruiterApi, RecruiterCandidate, Review } from '../api/recruiter-api';
import { describeError } from '../core/http-errors';
import { CvViewerComponent } from './cv-viewer';
import { displayReason, recruiterLabel, score, trapDialogTab } from './recruiter-display';
import { DialogFocusDirective } from './dialog-focus';
import { Subscription } from 'rxjs';

@Component({ selector: 'candidate-detail', standalone: true, imports: [FormsModule, DatePipe, CvViewerComponent, DialogFocusDirective], template: `
  <div class="panel-backdrop" (click)="closed.emit()"></div>
  <aside class="detail-panel recruiter-detail" recruiterDialog role="dialog" aria-modal="true" aria-labelledby="candidate-title" tabindex="-1"
    (keydown.escape)="closed.emit()" (keydown.tab)="trapTab($event)">
    <div class="card-title"><div><p class="eyebrow">Candidate assessment</p><h2 id="candidate-title">{{ row()?.source_filename || 'Candidate details' }}</h2></div><button class="secondary" (click)="closed.emit()">Close details</button></div>
    @if (error()) { <p class="error-banner" role="alert">{{ error() }}</p> }
    @if (row(); as candidate) {
      <p class="muted">{{ candidate.role_title }} · Candidate ID {{ candidate.candidate_id }}</p>
      <div class="actions"><span class="badge">{{ label(candidate.status) }}</span><span class="badge">{{ label(candidate.review.decision) }}</span>
        @if (candidate.provisional) { <span class="badge warning">Verification outstanding</span> }
        @if (candidate.is_mock) { <span class="badge warning">Demonstration assessment</span> }
        <button (click)="openCv()">View CV</button>
      </div>
      <section class="detail-section"><h3>Fit summary</h3><p>{{ candidate.summary || 'No detailed assessment summary is available for this screening outcome.' }}</p>
        <div class="score-overview"><strong>{{ score(candidate.score) }}</strong><span>{{ candidate.tier ? label(candidate.tier) : 'No final assessment band' }}</span></div>
        <p class="muted">Scores reflect documented fit for this role, not a probability of hiring success. Category weights: skills 40%, experience 30%, projects 20%, education 10%. Policy {{ candidate.scoring_policy_version || 'unavailable' }}.</p>
      </section>
      <section class="detail-section"><h3>Screening history</h3><ol class="stage-timeline">@for (stage of candidate.stage_history; track stage.stage) { <li><strong>{{ stage.stage }}</strong><span>{{ label(stage.state) }}</span>@if (stage.reason) { <p>{{ reason(stage.reason) }}</p> }</li> }</ol>
        @if (candidate.failure_code) { <p>Outcome reason: {{ reason(candidate.failure_code) }}</p> }
      </section>
      <section class="detail-section"><h3>Eligibility and facts</h3><p>Extracted experience: {{ candidate.experience_years === null ? 'Unknown' : candidate.experience_years + ' years' }} · Source: {{ label(candidate.experience_source) }}. Total extracted experience may differ from role-relevant experience.</p>
        @for (check of candidate.effective_eligibility_checks || candidate.stage1_checks; track $index) { <article class="check-row"><strong>{{ label(check.rule) }}</strong><span class="badge" [class.warning]="check.status !== 'PASS'">{{ label(check.status) }}</span><p>{{ check.message }}</p></article> }
      </section>
      <section class="detail-section"><h3>Requirement evidence</h3>
        @for (target of candidate.stage2_target_assessments || []; track target.target_id) { <article class="requirement-card"><strong>{{ target.target_text || target.target_id }}</strong><p>{{ label(target.status) }} · {{ (target.coverage * 100).toFixed(0) }}% concept coverage</p>
          @if (target.supporting_text) { <blockquote>{{ target.supporting_text }}</blockquote><button class="text-button" (click)="openCv(target.supporting_text)">Find supporting passage in CV</button> }
        </article> } @empty { <p>Requirement matching was not completed or no structured targets are available.</p> }
      </section>
      <section class="detail-section"><h3>Category assessments</h3>@for (category of categories; track category) { <article><div class="card-title"><h4>{{ label(category) }}</h4><strong>{{ score(candidate.category_scores[category]) }}</strong></div>
        @if (candidate.assessments?.[category]; as assessment) { <p>{{ assessment.rationale }}</p>@for (claim of assessment.claims || []; track $index) { <p>{{ claim.claim }}</p>@if (claim.quote) { <blockquote>{{ claim.quote }}</blockquote> }<button class="text-button" (click)="showEvidence(claim.citation)">View source evidence</button> } }
      </article> }</section>
      <section class="detail-section"><h3>Questions to resolve</h3>
        @for (item of candidate.verification_reasons; track $index) { <p>{{ reason(item) }}</p> }
        @for (item of candidate.review_reasons; track $index) { <p>{{ reason(item) }}</p> }
        @for (flag of candidate.flags; track $index) { <article><strong>{{ label(flag.type) }} · {{ label(flag.severity) }}</strong><p>{{ flag.description }}</p></article> }
        <ul>@for (question of questions(candidate); track question) { <li>{{ question }}</li> } @empty { <li>No documented unresolved questions. Use the requirement evidence to guide the interview.</li> }</ul>
      </section>
      <section class="detail-section"><h3>Recruiter decision</h3>
        <details><summary>Record verified facts</summary><p class="muted">Record facts you have confirmed with the candidate. These update recruiter filters and are audited separately from the original AI assessment.</p>
          <label for="verified-years">Verified experience years (blank if unknown)</label><input id="verified-years" type="number" min="0" step="0.1" [(ngModel)]="verifiedYears">
          <label for="verified-auth">Verified work authorization</label><select id="verified-auth" [(ngModel)]="verifiedAuthorization"><option value="unknown">Not verified</option><option value="eligible">Confirmed eligible</option><option value="ineligible">Confirmed ineligible</option></select>
          <label for="verified-education">Verified education requirement</label><select id="verified-education" [(ngModel)]="verifiedEducation"><option value="unknown">Not verified</option><option value="yes">Confirmed meets requirement</option><option value="no">Confirmed does not meet requirement</option></select>
        </details>
        <label for="candidate-decision">Decision</label><select id="candidate-decision" [(ngModel)]="decision">@for (value of decisions; track value) { <option [value]="value">{{ label(value) }}</option> }</select>
        <label for="candidate-reason">Decision reason {{ decision === 'NOT_PROCEEDING' ? '(required)' : '' }}</label><textarea id="candidate-reason" [(ngModel)]="decisionReason" maxlength="2000" rows="2"></textarea>
        <label for="candidate-notes">Recruiter notes</label><textarea id="candidate-notes" [(ngModel)]="notes" maxlength="10000" rows="4"></textarea>
        <label for="candidate-tags">Tags (comma separated)</label><input id="candidate-tags" [(ngModel)]="tags" maxlength="1000">
        <label for="candidate-assignee">Assigned reviewer</label><select id="candidate-assignee" [(ngModel)]="assignee"><option value="">Unassigned</option>@for (member of members(); track member.id) { <option [value]="member.id">{{ member.username }}</option> }</select>
        <div class="actions"><button (click)="save()" [disabled]="saving() || (decision === 'NOT_PROCEEDING' && !decisionReason.trim())">{{ saving() ? 'Saving…' : 'Save decision' }}</button><span role="status">{{ saved() }}</span></div>
      </section>
      <section class="detail-section"><h3>Decision history</h3>@for (event of candidate.history || []; track $index) { <article><p><strong>{{ label(event.snapshot.decision) }}</strong> · {{ event.actor_id }} · {{ event.created_at | date:'medium' }}</p><p>{{ event.snapshot.reason }}</p><p>{{ event.snapshot.notes }}</p></article> } @empty { <p>No recruiter decisions have been recorded.</p> }</section>
      @if (candidate.other_roles?.length) { <section class="detail-section"><h3>Other roles in this campaign</h3>@for (role of candidate.other_roles; track role.pair_id) { <button class="text-button" (click)="load(role.pair_id)">{{ role.role_title }} · {{ label(role.status) }} · {{ score(role.score) }}</button> }</section> }
      <details><summary>Technical details</summary><pre>{{ technical(candidate) }}</pre></details>
    } @else { <p role="status">Loading assessment…</p> }
  </aside>
  @if (cvOpen() && row(); as candidate) { <cv-viewer [campaign]="candidate.campaign_id" [candidate]="candidate.candidate_id" [page]="sourcePage()" [quote]="sourceQuote()" (closed)="cvOpen.set(false)" /> }
` })
export class CandidateDetailComponent {
  readonly pair = input.required<string>(); readonly closed = output<void>(); readonly changed = output<void>();
  readonly row = signal<RecruiterCandidate | null>(null); readonly error = signal(''); readonly saving = signal(false); readonly saved = signal('');
  readonly members = signal<Member[]>([]); readonly cvOpen = signal(false); readonly sourceQuote = signal(''); readonly sourcePage = signal<number | null>(null);
  readonly categories = ['skills', 'experience', 'projects', 'education'];
  readonly decisions: Review['decision'][] = ['UNREVIEWED', 'SHORTLIST', 'HOLD', 'NOT_PROCEEDING'];
  decision: Review['decision'] = 'UNREVIEWED'; decisionReason = ''; notes = ''; tags = ''; assignee = '';
  verifiedYears: number | null = null; verifiedAuthorization = 'unknown'; verifiedEducation = 'unknown';
  readonly label = recruiterLabel; readonly score = score; readonly reason = displayReason; readonly trapTab = trapDialogTab;
  private readonly api = inject(RecruiterApi); private readonly destroyRef = inject(DestroyRef); private readonly element = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly trigger = document.activeElement as HTMLElement;
  private detailRequest?: Subscription; private memberRequest?: Subscription;
  constructor() {
    effect(() => this.load(this.pair()));
    this.destroyRef.onDestroy(() => this.trigger?.focus());
    setTimeout(() => this.element.nativeElement.querySelector<HTMLElement>('button')?.focus(), 0);
  }
  load(pair: string): void {
    this.detailRequest?.unsubscribe(); this.memberRequest?.unsubscribe();
    this.error.set(''); this.row.set(null);
    this.detailRequest = this.api.detail(pair).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: row => {
      this.row.set(row); this.decision = row.review.decision; this.decisionReason = row.review.reason;
      this.notes = row.review.notes; this.tags = row.review.tags.join(', '); this.assignee = row.review.assignee_id || '';
      this.verifiedYears = row.review.verified_facts?.experience_years ?? null;
      this.verifiedAuthorization = row.review.verified_facts?.work_authorized || 'unknown';
      this.verifiedEducation = row.review.verified_facts?.education_meets_requirement == null ? 'unknown' : row.review.verified_facts.education_meets_requirement ? 'yes' : 'no';
      this.memberRequest = this.api.members(row.campaign_id).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: result => this.members.set(result.members), error: e => this.error.set(describeError(e)) });
    }, error: e => this.error.set(describeError(e)) });
  }
  openCv(quote = '', page: number | null = null): void { this.sourceQuote.set(quote); this.sourcePage.set(page); this.cvOpen.set(true); }
  showEvidence(citation: string): void {
    const ref = this.row()?.evidence.find(e => e.citation === citation);
    if (!ref) { this.error.set('No verified source reference is available for this claim.'); return; }
    const page = ref.source_location?.['page_number']; this.openCv(ref.text || '', typeof page === 'number' ? page : null);
  }
  questions(row: RecruiterCandidate): string[] {
    const missing = (row.stage2_target_assessments || []).filter(t => t.status !== 'DIRECT').map(t => `Ask for a concrete example of ${t.target_text || t.target_id}.`);
    const checks = row.stage1_checks.filter(c => c.status === 'REVIEW').map(c => `Confirm ${c.rule}: ${c.message}`);
    return [...checks, ...missing].slice(0, 8);
  }
  technical(row: RecruiterCandidate): string { return JSON.stringify({ candidate_id: row.candidate_id, pair_id: row.pair_id, status: row.status, policy: row.scoring_policy, failure_code: row.failure_code }, null, 2); }
  save(): void {
    const row = this.row(); if (!row) return; this.saving.set(true); this.error.set(''); this.saved.set('');
    this.api.update({ pair_versions: { [row.pair_id]: row.review.version }, decision: this.decision, reason: this.decisionReason,
      notes: this.notes, tags: this.tags.split(',').map(t => t.trim()).filter(Boolean), assignee_id: this.assignee || null,
      verified_facts: { experience_years: this.verifiedYears, work_authorized: this.verifiedAuthorization,
        education_meets_requirement: this.verifiedEducation === 'unknown' ? null : this.verifiedEducation === 'yes' } }).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => { this.saving.set(false); this.saved.set('Decision saved'); this.changed.emit(); this.load(row.pair_id); },
      error: e => { this.saving.set(false); this.error.set(describeError(e)); }
    });
  }
}
