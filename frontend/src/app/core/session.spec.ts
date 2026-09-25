import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors, withXhr } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { AuthSession, csrfInterceptor } from './session';

describe('recruiter session', () => {
  let session: AuthSession;
  let http: HttpTestingController;
  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(withXhr(), withInterceptors([csrfInterceptor])), provideHttpClientTesting()] });
    session = TestBed.inject(AuthSession);
    http = TestBed.inject(HttpTestingController);
    document.cookie = 'cv_csrf=test-csrf; path=/';
  });
  afterEach(() => { http.verify(); document.cookie = 'cv_csrf=; max-age=0; path=/'; });
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
