import { Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';
import { AuthSession } from '../core/session';
import { describeError } from '../core/http-errors';

@Component({ standalone: true, imports: [ReactiveFormsModule], template: `
  <div class="page-heading"><div><p class="eyebrow">Recruiter access</p><h1>Sign in</h1><p>Use your assigned recruiter account.</p></div></div>
  <section class="card login-card"><form [formGroup]="form" (ngSubmit)="submit()">
    <label for="identifier">Email or username</label><input id="identifier" formControlName="identifier" autocomplete="username" required>
    <label for="password">Password</label><input id="password" type="password" formControlName="password" autocomplete="current-password" required>
    @if (error()) { <p class="error" role="alert">{{ error() }}</p> }
    <div class="actions"><button type="submit" [disabled]="busy()">{{ busy() ? 'Signing in…' : 'Sign in' }}</button></div>
  </form></section>
` })
export class LoginComponent {
  private readonly fb = inject(FormBuilder);
  private readonly session = inject(AuthSession);
  private readonly router = inject(Router);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly form = this.fb.nonNullable.group({ identifier: ['', Validators.required], password: ['', Validators.required] });
  submit(): void {
    if (this.form.invalid || this.busy()) { this.form.markAllAsTouched(); return; }
    this.busy.set(true); this.error.set('');
    const { identifier, password } = this.form.getRawValue();
    this.session.login(identifier, password).subscribe({
      next: () => { this.form.controls.password.setValue(''); void this.router.navigateByUrl('/'); },
      error: error => { this.form.controls.password.setValue(''); this.error.set(error?.status === 401 ? 'Incorrect credentials or account unavailable.' : describeError(error)); this.busy.set(false); }
    });
  }
}
