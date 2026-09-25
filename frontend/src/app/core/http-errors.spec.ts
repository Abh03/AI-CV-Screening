import { HttpErrorResponse, HttpHeaders } from '@angular/common/http';
import { describe, expect, it } from 'vitest';
import { safeApiError } from './http-errors';

describe('safeApiError', () => {
  it('does not expose backend input or PII and keeps a safe correlation ID', () => {
    const error = new HttpErrorResponse({ status: 422, error: { detail: 'CV text: private data' }, headers: new HttpHeaders({ 'X-Correlation-ID': 'request_42' }) });
    expect(safeApiError(error)).toEqual({ status: 422, message: 'The server rejected some input. Check the JD fields or ZIP file.', correlationId: 'request_42' });
  });
  it('drops malformed correlation IDs', () => {
    const error = new HttpErrorResponse({ status: 503, headers: new HttpHeaders({ 'X-Correlation-ID': '<script>' }) });
    expect(safeApiError(error).correlationId).toBeNull();
  });
});
