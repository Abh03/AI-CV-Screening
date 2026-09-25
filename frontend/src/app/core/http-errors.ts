import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { catchError, throwError } from 'rxjs';

export interface SafeApiError { message: string; status: number; correlationId: string | null }

export function safeApiError(error: unknown): SafeApiError {
  if (!(error instanceof HttpErrorResponse)) return { message: 'The request could not be completed.', status: 0, correlationId: null };
  const messages: Record<number, string> = {
    0: 'The service could not be reached. Check your connection and retry.',
    401: 'Your session has ended. Sign in again.',
    403: 'You do not have access to this campaign.',
    404: 'This campaign or JD could not be found.',
    409: 'This request conflicts with the current campaign state. Refresh before retrying.',
    413: 'The upload or request is too large.',
    415: 'Only ZIP archives are accepted here.',
    422: 'The server rejected some input. Check the JD fields or ZIP file.',
    503: 'The service is temporarily unavailable. Please retry later.'
  };
  const id = error.headers?.get('X-Correlation-ID');
  return { message: messages[error.status] ?? 'The request could not be completed.', status: error.status, correlationId: id && /^[A-Za-z0-9_-]{1,64}$/.test(id) ? id : null };
}

export const httpErrorInterceptor: HttpInterceptorFn = (request, next) => next(request).pipe(catchError(error => throwError(() => safeApiError(error))));

export function describeError(error: unknown): string {
  const safe = error as Partial<SafeApiError>;
  return `${safe.message ?? 'The request could not be completed.'}${safe.correlationId ? ` Support ID: ${safe.correlationId}` : ''}`;
}
