import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors, withXhr } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { provideRouter, Router, UrlTree } from '@angular/router';
import { firstValueFrom, Observable } from 'rxjs';
import { AuthSession, csrfInterceptor, requireGuest } from './session';

describe('recruiter session', () => {
  let session: AuthSession;
  let http: HttpTestingController;
  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(withXhr(), withInterceptors([csrfInterceptor])), provideHttpClientTesting(), provideRouter([])] });
    session = TestBed.inject(AuthSession);
    http = TestBed.inject(HttpTestingController);
    document.cookie = 'cv_csrf=test-csrf; path=/';
  });
  afterEach(() => { http.verify(); document.cookie = 'cv_csrf=; max-age=0; path=/'; });
  it('redirects a signed-in visitor away from the login route after a reload or Back', async () => {
    const guard = TestBed.runInInjectionContext(() => requireGuest({} as never, {} as never)) as Observable<UrlTree>;
    const result = firstValueFrom(guard);
    http.expectOne('/api/v1/auth/me').flush({ user: { id: 'alice', username: 'alice', role: 'recruiter', email: 'alice@example.test' } });
    expect(TestBed.inject(Router).serializeUrl(await result)).toBe('/');
  });
  it('allows the sign-in screen when the cookie session has expired', async () => {
    const guard = TestBed.runInInjectionContext(() => requireGuest({} as never, {} as never)) as Observable<boolean>;
    const result = firstValueFrom(guard);
    http.expectOne('/api/v1/auth/me').flush({}, { status: 401, statusText: 'Unauthorized' });
    expect(await result).toBe(true);
  });
  it('keeps login credentials in the request and sends CSRF on later writes', () => {
    session.login('alice@example.test', 'sample-password').subscribe();
    const login = http.expectOne('/api/v1/auth/login');
    expect(login.request.body).toEqual({ identifier: 'alice@example.test', password: 'sample-password' });
    expect(login.request.headers.get('X-CSRF-Token')).toBe('test-csrf');
    login.flush({ user: { id: 'alice', email: 'alice@example.test', username: 'alice', role: 'recruiter' } });
    expect(session.user()?.id).toBe('alice');
    session.logout().subscribe();
    const logout = http.expectOne('/api/v1/auth/logout');
    expect(logout.request.headers.get('X-CSRF-Token')).toBe('test-csrf');
    logout.flush({ status: 'signed_out' });
    expect(session.user()).toBeNull();
  });
});
