import { Component, DestroyRef, ElementRef, effect, inject, input, output, signal, untracked } from '@angular/core';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CvDocument, RecruiterApi } from '../api/recruiter-api';
import { describeError } from '../core/http-errors';
import { trapDialogTab } from './recruiter-display';
import { DialogFocusDirective } from './dialog-focus';
import { Subscription } from 'rxjs';

@Component({ selector: 'cv-viewer', standalone: true, imports: [DialogFocusDirective], template: `
  <div class="panel-backdrop cv-backdrop" (click)="closed.emit()"></div>
  <section class="cv-dialog" recruiterDialog role="dialog" aria-modal="true" aria-labelledby="cv-title" tabindex="-1"
    (keydown.escape)="$event.stopPropagation(); closed.emit()" (keydown.tab)="trapTab($event)">
    <div class="card-title"><div><p class="eyebrow">Candidate CV</p><h2 id="cv-title">{{ document()?.filename || 'Loading CV' }}</h2></div><button class="secondary" (click)="closed.emit()">Close CV</button></div>
    @if (error()) { <p class="error-banner" role="alert">{{ error() }}</p> }
    @if (document(); as cv) {
      <div class="actions">@if (cv.original_available) { <button (click)="mode.set('original')" [class.secondary]="mode() !== 'original'">Original PDF</button> }
        <button (click)="mode.set('text')" [class.secondary]="mode() !== 'text'">Extracted text & evidence</button>
        @if (pdfUrl()) { <a class="button secondary" [href]="pdfUrl()" target="_blank" rel="noopener">Open PDF in a new tab</a> }
      </div>
      @if (!cv.original_available) { <p class="notice">The original PDF was not retained for this candidate. You can view available extracted text or restore the exact original file.</p>
        <label for="restore-cv">Restore original PDF</label><input id="restore-cv" type="file" accept="application/pdf,.pdf" (change)="restore($event)" [disabled]="restoring()">
      }
      @if (mode() === 'original') { @if (safePdf(); as src) { <iframe class="pdf-frame" title="Original candidate CV" [src]="src"></iframe> } @else { <p role="status">Loading original PDF…</p> } }
      @else {
        @if (quote()) { <p class="notice">Highlighted source evidence: {{ quote() }}</p> }
        @for (page of cv.pages; track page.page_number) { <article class="cv-page" [attr.id]="'cv-page-' + page.page_number"><h3>Page {{ page.page_number }}</h3>
          @for (block of page.blocks; track block.block_number) { <p class="cv-block" [class.evidence-highlight]="highlight(block.text, page.page_number)">{{ block.text }}</p> }
        </article> } @empty { <p class="cv-block">{{ cv.text || 'Extracted text is unavailable. ' + (cv.extraction_error || 'Extraction has not completed.') }}</p> }
      }
    } @else { <p role="status">Loading CV…</p> }
  </section>
` })
export class CvViewerComponent {
  readonly campaign = input.required<string>(); readonly candidate = input.required<string>();
  readonly page = input<number | null>(null); readonly quote = input(''); readonly closed = output<void>();
  readonly document = signal<CvDocument | null>(null); readonly error = signal(''); readonly mode = signal('text');
  readonly pdfUrl = signal(''); readonly safePdf = signal<SafeResourceUrl | null>(null); readonly restoring = signal(false);
  private readonly api = inject(RecruiterApi); private readonly sanitizer = inject(DomSanitizer);
  private readonly destroyRef = inject(DestroyRef); private readonly element = inject<ElementRef<HTMLElement>>(ElementRef);
  private trigger: HTMLElement | null = null;
  private cvRequest?: Subscription; private pdfRequest?: Subscription;
  readonly trapTab = trapDialogTab;
  constructor() {
    this.trigger = document.activeElement as HTMLElement;
    effect(() => { this.campaign(); this.candidate(); this.page(); this.quote(); untracked(() => this.load()); });
    this.destroyRef.onDestroy(() => { if (this.pdfUrl()) URL.revokeObjectURL(this.pdfUrl()); this.trigger?.focus(); });
    setTimeout(() => this.element.nativeElement.querySelector<HTMLElement>('button')?.focus(), 0);
  }
  private load(): void {
    this.cvRequest?.unsubscribe(); this.pdfRequest?.unsubscribe();
    this.document.set(null); this.safePdf.set(null); this.error.set('');
    this.cvRequest = this.api.cv(this.campaign(), this.candidate()).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: cv => {
      this.document.set(cv); this.mode.set(this.quote() ? 'text' : cv.original_available ? 'original' : 'text');
      if (cv.original_available) this.pdfRequest = this.api.pdf(this.campaign(), this.candidate()).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: blob => {
        if (this.pdfUrl()) URL.revokeObjectURL(this.pdfUrl());
        const url = URL.createObjectURL(blob); this.pdfUrl.set(url);
        this.safePdf.set(this.sanitizer.bypassSecurityTrustResourceUrl(url + (this.page() ? '#page=' + this.page() : '')));
      }, error: e => { this.error.set(describeError(e)); this.mode.set('text'); } });
      if (this.page()) setTimeout(() => this.element.nativeElement.querySelector('#cv-page-' + this.page())?.scrollIntoView(), 0);
    }, error: e => this.error.set(describeError(e)) });
  }
  highlight(text: string, page: number): boolean {
    const normalize = (v: string): string => v.toLowerCase().replace(/\s+/g, ' ').trim();
    const source = normalize(text), quote = normalize(this.quote());
    return !!quote && (!this.page() || this.page() === page) && (source.includes(quote) || source.length > 15 && quote.includes(source));
  }
  restore(event: Event): void {
    const file = (event.target as HTMLInputElement).files?.[0]; if (!file) return;
    this.restoring.set(true); this.error.set('');
    this.api.restore(this.campaign(), this.candidate(), file).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => { this.restoring.set(false); this.load(); }, error: e => { this.restoring.set(false); this.error.set(describeError(e)); }
    });
  }
}
