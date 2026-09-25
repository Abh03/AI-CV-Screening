import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpInterceptorFn } from '@angular/common/http';
import { CanActivateFn, Router } from '@angular/router';
import { catchError, map, Observable, of, tap } from 'rxjs';
import { LocalCampaigns } from './local-campaigns';

export interface RecruiterUser { id: string; email: string; username: string; role: 'recruiter' | 'admin' }

@Injectable({ providedIn: 'root' })
export class AuthSession {
  private readonly http = inject(HttpClient);
  private readonly local = inject(LocalCampaigns);
  readonly user = signal<RecruiterUser | null>(null);
  me(): Observable<RecruiterUser> {
    return this.http.get<{ user: RecruiterUser }>('/api/v1/auth/me').pipe(map(result => result.user), tap(user => this.user.set(user)),
      catchError(error => { this.clear(); throw error; }));
  }
  login(identifier: string, password: string): Observable<RecruiterUser> {
    return this.http.post<{ user: RecruiterUser }>('/api/v1/auth/login', { identifier, password }).pipe(map(result => result.user), tap(user => this.user.set(user)));
  }
  logout(): Observable<unknown> {
    return this.http.post('/api/v1/auth/logout', {}).pipe(tap(() => this.clear()));
  }
  clear(): void { this.user.set(null); this.local.clear(); }
}

export const requireSession: CanActivateFn = () => {
  const session = inject(AuthSession);
  const router = inject(Router);
  return session.me().pipe(map(() => true), catchError(() => of(router.createUrlTree(['/login']))));
};

export const csrfInterceptor: HttpInterceptorFn = (request, next) => {
  if (!request.url.startsWith('/api/v1/') || ['GET', 'HEAD', 'OPTIONS'].includes(request.method)) return next(request);
  const csrf = document.cookie.split('; ').find(part => part.startsWith('cv_csrf='))?.slice('cv_csrf='.length);
  return next(csrf ? request.clone({ setHeaders: { 'X-CSRF-Token': decodeURIComponent(csrf) } }) : request);
};
