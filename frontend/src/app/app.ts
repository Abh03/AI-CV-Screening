import { Component, inject, signal } from '@angular/core';
import { RouterLink, RouterOutlet, Routes } from '@angular/router';
import { Router } from '@angular/router';
import { AuthSession, requireSession } from './core/session';

export const routes: Routes = [
  { path: 'login', title: 'Sign in', loadComponent: () => import('./features/login').then(m => m.LoginComponent) },
  { path: '', title: 'Campaigns', canActivate: [requireSession], loadComponent: () => import('./features/home').then(m => m.HomeComponent) },
  { path: 'campaigns/new', title: 'New campaign', canActivate: [requireSession], loadComponent: () => import('./features/create').then(m => m.CreateComponent) },
  { path: 'campaigns/:id', title: 'Campaign', canActivate: [requireSession], loadComponent: () => import('./features/campaign-detail').then(m => m.CampaignDetailComponent) },
  { path: 'campaigns/:id/jds/:jd', title: 'JD results', canActivate: [requireSession], loadComponent: () => import('./features/results').then(m => m.ResultsComponent) },
  { path: '**', redirectTo: '' }
];

@Component({ selector: 'app-root', standalone: true, imports: [RouterOutlet, RouterLink], template: `
  <a class="skip" href="#main">Skip to content</a>
  <header class="site-header"><div class="wrap header-inner"><a class="brand" routerLink="/">CV Screening</a><nav aria-label="Primary"><a routerLink="/">Campaigns</a><a routerLink="/campaigns/new">New campaign</a>@if (session.user(); as user) { <span>{{ user.username }} ({{ user.role }})</span><button type="button" class="secondary" (click)="signOut()">Sign out</button> }</nav></div></header>
  <main id="main" class="wrap">@if (logoutError()) { <p class="error-banner" role="alert">{{ logoutError() }}</p> }<router-outlet /></main>
  <footer class="wrap footer">Screening results support recruiter review. A score is not a hiring decision.</footer>
` })
export class AppComponent {
  readonly session = inject(AuthSession);
  readonly logoutError = signal('');
  private readonly router = inject(Router);
  signOut(): void { this.session.logout().subscribe({
    next: () => void this.router.navigateByUrl('/login'),
    error: () => this.logoutError.set('Sign-out could not be confirmed. Please retry.')
  }); }
}
