const labels: Record<string, string> = {
  SUCCESS: 'Assessed', REVIEW_REQUIRED: 'Needs assessment review', FILTER_REJECTED: 'Eligibility requirement not met',
  CUTOFF_EXCLUDED: 'Not selected for detailed assessment', EVALUATION_FAILED: 'Assessment failed',
  EXTRACTION_FAILED: 'CV extraction failed', PROCESSING_FAILED: 'Requirement matching failed',
  PENDING: 'Waiting to process', RUNNING: 'Checking eligibility and requirements', STAGE2_READY: 'Requirements matched',
  SHORTLISTED: 'Queued for detailed assessment', STAGE3_RUNNING: 'Detailed assessment in progress',
  UNREVIEWED: 'Not reviewed', SHORTLIST: 'Shortlisted by recruiter', HOLD: 'On hold', NOT_PROCEEDING: 'Not proceeding',
  DIRECT: 'Supported evidence', PARTIAL: 'Partial evidence', MISSING: 'No evidence found', UNASSESSED: 'Not assessed',
  PASS: 'Meets requirement', REVIEW: 'Needs verification', FAIL: 'Does not meet requirement',
  NOT_REACHED: 'Not reached', NOT_SELECTED: 'Not selected', SUCCEEDED: 'Complete', FAILED: 'Failed',
  TIER_1: 'Higher score band (75–100)', TIER_2: 'Middle score band (55–74.99)', TIER_3: 'Lower score band (0–54.99)',
  NOT_PDF: 'The uploaded file is not a PDF.', PDF_TOO_LARGE: 'The CV exceeds the supported file size.',
  DUPLICATE_NAME: 'Another file in the archive has the same name.', CORRUPT_MEMBER: 'The archive entry could not be read.',
  UNSAFE_PATH: 'The archive entry has an unsupported file path.', UNSUPPORTED_COMPRESSION: 'This archive entry uses an unsupported compression format.',
};
export function recruiterLabel(value: string): string {
  return labels[value] ?? value.replaceAll('_', ' ').toLowerCase().replace(/^./, c => c.toUpperCase());
}
export function displayReason(value: unknown): string {
  if (typeof value === 'string') return recruiterLabel(value);
  if (value && typeof value === 'object') {
    const item = value as Record<string, unknown>;
    return String(item['message'] ?? item['description'] ?? item['code'] ?? 'Verification required');
  }
  return 'Verification required';
}
export function score(value: number | null | undefined): string { return value == null ? 'Not assessed' : value.toFixed(1); }
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob); const anchor = document.createElement('a');
  anchor.href = url; anchor.download = filename; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function trapDialogTab(event: Event): void {
  const keyboard = event as KeyboardEvent;
  const dialog = event.currentTarget as HTMLElement;
  const nodes = Array.from(dialog.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), iframe, [tabindex="0"]')).filter(n => n.getClientRects().length);
  const first = nodes[0], last = nodes[nodes.length - 1];
  if (!first) { event.preventDefault(); dialog.focus(); }
  else if (keyboard.shiftKey && (document.activeElement === first || document.activeElement === dialog)) { event.preventDefault(); last.focus(); }
  else if (!keyboard.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}
