import { Component, DestroyRef, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { EMPTY, Subject, catchError, forkJoin, switchMap } from 'rxjs';
import { CandidatePool, Member, PoolFilters, RecruiterApi, RecruiterCandidate, Review, SavedView } from '../api/recruiter-api';
import { CandidateDetailComponent } from '../shared/candidate-detail';
import { CvViewerComponent } from '../shared/cv-viewer';
import { displayReason, downloadBlob, recruiterLabel, score, trapDialogTab } from '../shared/recruiter-display';
import { describeError } from '../core/http-errors';
import { DialogFocusDirective } from '../shared/dialog-focus';

const initialFilters = (): PoolFilters => ({ search: '', status: '', decision: '', category: '', eligibility_rule: '', eligibility_status: '',
  requirement_ids: [], requirement_status: 'DIRECT', requirement_mode: 'all', sort: 'score', direction: 'desc' });

@Component({ standalone: true, imports: [RouterLink, FormsModule, CandidateDetailComponent, CvViewerComponent, DialogFocusDirective], templateUrl: './recruiter-results.html' })
export class ResultsComponent {
  private readonly route = inject(ActivatedRoute); private readonly router = inject(Router); private readonly api = inject(RecruiterApi); private readonly destroyRef = inject(DestroyRef);
  readonly id = this.route.snapshot.paramMap.get('id') || ''; readonly jd = this.route.snapshot.paramMap.get('jd') || '';
  readonly data = signal<CandidatePool | null>(null); readonly loading = signal(false); readonly error = signal(''); readonly notice = signal('');
  readonly page = signal(1); readonly showFilters = signal(true); readonly selected = signal<string | null>(null); readonly cvCandidate = signal<RecruiterCandidate | null>(null);
  readonly chosen = signal<RecruiterCandidate[]>([]); readonly comparison = signal<RecruiterCandidate[]>([]); readonly comparing = signal(false);
  readonly views = signal<SavedView[]>([]); readonly members = signal<Member[]>([]); readonly canManage = signal(false); readonly exporting = signal(false);
  readonly columns = signal(['requirements', 'experience', 'questions']); readonly bulkOpen = signal(false); readonly savingBulk = signal(false);
  readonly categories = ['skills', 'experience', 'projects', 'education']; readonly requirementStates = ['DIRECT', 'PARTIAL', 'MISSING', 'UNASSESSED'];
  readonly statuses = ['PENDING', 'RUNNING', 'STAGE2_READY', 'SHORTLISTED', 'STAGE3_RUNNING', 'SUCCESS', 'REVIEW_REQUIRED', 'FILTER_REJECTED', 'CUTOFF_EXCLUDED', 'EXTRACTION_FAILED', 'PROCESSING_FAILED', 'EVALUATION_FAILED'];
  readonly decisions: Review['decision'][] = ['UNREVIEWED', 'SHORTLIST', 'HOLD', 'NOT_PROCEEDING'];
  readonly optionalColumns = [{ key: 'requirements', label: 'Requirement coverage' }, { key: 'experience', label: 'Extracted experience' }, { key: 'strengths', label: 'Category strengths' }, { key: 'questions', label: 'Open questions' }];
  readonly bands = [{ name: 'Lower score band (0–54.99)', low: 0, high: 54.99 }, { name: 'Middle score band (55–74.99)', low: 55, high: 74.99 }, { name: 'Higher score band (75–100)', low: 75, high: 100 }];
  readonly label = recruiterLabel; readonly score = score; readonly trapTab = trapDialogTab;
  filters = initialFilters(); private applied = initialFilters(); viewId = ''; viewName = ''; memberName = '';
  bulkDecision: Review['decision'] = 'SHORTLIST'; bulkReason = '';
  private readonly requests = new Subject<void>(); private compareTrigger: HTMLElement | null = null;
  constructor() {
    this.requests.pipe(switchMap(() => {
      this.loading.set(true); this.error.set('');
      return this.api.pool(this.id, this.jd, this.applied, (this.page() - 1) * 30).pipe(catchError(e => { this.error.set(describeError(e)); this.loading.set(false); return EMPTY; }));
    }), takeUntilDestroyed(this.destroyRef)).subscribe(pool => {
      this.data.set(pool); this.loading.set(false);
      this.chosen.update(rows => rows.map(row => pool.results.find(fresh => fresh.pair_id === row.pair_id) || row));
    });
    this.route.queryParamMap.pipe(takeUntilDestroyed(this.destroyRef)).subscribe(params => {
      try { this.filters = { ...initialFilters(), ...JSON.parse(params.get('filters') || '{}') as PoolFilters }; this.applied = structuredClone(this.filters); }
      catch { this.filters = initialFilters(); this.applied = initialFilters(); }
      if (!params.has('filters') && params.get('status')) this.filters.status = this.applied.status = params.get('status') || '';
      const page = Number(params.get('page')); this.page.set(Number.isSafeInteger(page) && page > 0 ? page : 1); this.refresh();
    });
    this.loadViews(); this.loadMembers();
  }
  refresh(): void { this.requests.next(); }
  apply(): void {
    this.chosen.set([]); this.notice.set('');
    void this.router.navigate([], { relativeTo: this.route, queryParams: { filters: JSON.stringify(this.filters), page: 1 } }).then(changed => { if (!changed) { this.applied = structuredClone(this.filters); this.page.set(1); this.refresh(); } });
  }
  reset(): void { this.filters = initialFilters(); this.viewId = ''; this.apply(); }
  goPage(delta: number): void { void this.router.navigate([], { relativeTo: this.route, queryParams: { filters: JSON.stringify(this.applied), page: this.page() + delta } }); }
  maxPage(): number { return Math.max(1, Math.ceil((this.data()?.total || 0) / 30)); }
  entries(value: Record<string, number>): { key: string; count: number }[] { return Object.entries(value).map(([key, count]) => ({ key, count })); }
  statusFilter(status: string): void { this.filters = initialFilters(); this.filters.status = status; this.apply(); }
  decisionFilter(decision: string): void { this.filters = initialFilters(); this.filters.decision = decision; this.apply(); }
  reviewFilter(): void { this.filters = initialFilters(); this.filters.review_only = true; this.apply(); }
  scoreFilter(low: number, high: number): void { this.filters = initialFilters(); this.filters.min_score = low; this.filters.max_score = high; this.apply(); }
  requirementFilter(id: string, status: string): void { this.filters = initialFilters(); this.filters.requirement_ids = [id]; this.filters.requirement_status = status; this.apply(); }
  hasRequirement(id: string): boolean { return this.filters.requirement_ids?.includes(id) || false; }
  toggleRequirement(id: string): void { this.filters.requirement_ids = this.hasRequirement(id) ? this.filters.requirement_ids?.filter(v => v !== id) : [...this.filters.requirement_ids || [], id]; }
  toggleDirection(): void { this.filters.direction = this.filters.direction === 'desc' ? 'asc' : 'desc'; this.apply(); }
  hasColumn(key: string): boolean { return this.columns().includes(key); }
  toggleColumn(key: string): void { this.columns.update(columns => columns.includes(key) ? columns.filter(c => c !== key) : [...columns, key]); }
  coverage(row: RecruiterCandidate): string { const targets = row.stage2_target_assessments || []; return targets.length ? `${targets.filter(t => t.status === 'DIRECT').length} / ${targets.length} supported` : 'Not assessed'; }
  missing(row: RecruiterCandidate): string { const count = (row.stage2_target_assessments || []).filter(t => t.status === 'MISSING').length; return count ? `${count} without evidence` : ''; }
  strengths(row: RecruiterCandidate): string[] { return Object.entries(row.category_scores).filter((entry): entry is [string, number] => entry[1] !== null).sort((a, b) => b[1] - a[1]).slice(0, 2).map(([key, value]) => `${this.label(key)} ${value.toFixed(1)}`); }
  openQuestions(row: RecruiterCandidate): string { return [...row.verification_reasons.map(displayReason), ...row.review_reasons.map(displayReason), ...row.flags.map(f => f.description)].join('; ') || (row.score === null ? 'Assessment incomplete' : 'No unresolved assessment questions'); }
  chips(): { key: string; text: string }[] {
    const defaults = initialFilters(); const labels: Record<string, string> = { search: 'Search', status: 'Outcome', decision: 'Decision', min_score: 'Min score', max_score: 'Max score', min_years: 'Min years', max_years: 'Max years', category: 'Category', min_category_score: 'Min category score', eligibility_rule: 'Eligibility rule', eligibility_status: 'Eligibility outcome', review_only: 'Needs review', reason: 'Review reason', tag: 'Tag', assignee_id: 'Assignee', reviewer_id: 'Reviewer', reviewed_after: 'Reviewed after', experience_unknown: 'Unknown experience' };
    const result = Object.entries(this.applied).filter(([key, value]) => !['sort', 'direction', 'requirement_ids', 'requirement_status', 'requirement_mode'].includes(key) && value !== '' && value !== null && value !== false && value !== undefined && value !== defaults[key as keyof PoolFilters]).map(([key, value]) => ({ key, text: `${labels[key] || key}: ${typeof value === 'boolean' ? 'Yes' : this.label(String(value))}` }));
    if (this.applied.requirement_ids?.length) result.push({ key: 'requirement_ids', text: `${this.applied.requirement_ids.length} requirements · ${this.label(this.applied.requirement_status || 'DIRECT')} · ${this.applied.requirement_mode === 'any' ? 'OR' : 'AND'}` });
    return result;
  }
  removeFilter(key: string): void { this.filters = { ...this.applied, [key]: key === 'requirement_ids' ? [] : key === 'review_only' || key === 'experience_unknown' ? false : key.startsWith('min_') || key.startsWith('max_') ? null : '' }; this.apply(); }
  isChosen(id: string): boolean { return this.chosen().some(row => row.pair_id === id); }
  toggleCandidate(row: RecruiterCandidate): void { if (this.isChosen(row.pair_id)) this.chosen.update(rows => rows.filter(r => r.pair_id !== row.pair_id)); else if (this.chosen().length < 100) this.chosen.update(rows => [...rows, row]); else this.notice.set('Select up to 100 candidates for one bulk action.'); }
  compare(): void {
    this.compareTrigger = document.activeElement as HTMLElement; this.comparing.set(true); this.error.set('');
    forkJoin(this.chosen().map(row => this.api.detail(row.pair_id))).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: rows => { this.comparison.set(rows); this.comparing.set(false); setTimeout(() => document.getElementById('close-compare')?.focus(), 0); }, error: e => { this.comparing.set(false); this.error.set(describeError(e)); } });
  }
  closeCompare(): void { this.comparison.set([]); this.compareTrigger?.focus(); }
  targetLabel(row: RecruiterCandidate, id: string): string { return this.label(row.stage2_target_assessments?.find(t => t.target_id === id)?.status || 'UNASSESSED'); }
  targetText(row: RecruiterCandidate, id: string): string { return row.stage2_target_assessments?.find(t => t.target_id === id)?.supporting_text || ''; }
  saveBulk(): void {
    this.savingBulk.set(true); this.error.set('');
    this.api.update({ pair_versions: Object.fromEntries(this.chosen().map(row => [row.pair_id, row.review.version])), decision: this.bulkDecision, reason: this.bulkReason }).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => { this.savingBulk.set(false); this.bulkOpen.set(false); this.chosen.set([]); this.notice.set('Recruiter decisions saved.'); this.refresh(); }, error: e => { this.savingBulk.set(false); this.error.set(describeError(e)); this.bulkOpen.set(false); }
    });
  }
  export(format: string, shortlist = false): void {
    this.exporting.set(true); this.error.set('');
    this.api.export(this.id, this.jd, shortlist ? { decision: 'SHORTLIST' } : this.applied, format).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: blob => { downloadBlob(blob, `candidate-${shortlist ? 'shortlist' : 'pool'}.${format}`); this.exporting.set(false); }, error: e => { this.exporting.set(false); this.error.set(describeError(e)); } });
  }
  loadViews(): void { this.api.views().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: views => this.views.set(views), error: e => this.error.set(describeError(e)) }); }
  useView(id: string): void { this.viewId = id; const view = this.views().find(v => v.id === id); if (view) { this.filters = { ...initialFilters(), ...structuredClone(view.filters) }; this.columns.set(view.columns); this.apply(); } }
  saveView(): void { this.api.saveView(this.viewName, this.applied, this.columns()).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: view => { this.views.update(views => [...views, view]); this.viewId = view.id; this.viewName = ''; this.notice.set('View saved to your account.'); }, error: e => this.error.set(describeError(e)) }); }
  deleteView(): void { this.api.deleteView(this.viewId).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: () => { this.viewId = ''; this.loadViews(); }, error: e => this.error.set(describeError(e)) }); }
  loadMembers(): void { this.api.members(this.id).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: result => { this.members.set(result.members); this.canManage.set(result.can_manage); }, error: e => this.error.set(describeError(e)) }); }
  addMember(): void { this.api.addMember(this.id, this.memberName.trim()).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: () => { this.memberName = ''; this.loadMembers(); this.notice.set('Reviewer now has campaign access.'); }, error: e => this.error.set(describeError(e)) }); }
  removeMember(id: string): void { this.api.removeMember(this.id, id).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: () => this.loadMembers(), error: e => this.error.set(describeError(e)) }); }
}
