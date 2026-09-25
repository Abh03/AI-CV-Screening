import { Component, DestroyRef, inject, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { DatePipe } from '@angular/common';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { catchError, EMPTY, map, switchMap } from 'rxjs';
import { CampaignApi } from '../api/campaign-api';
import { CampaignListPage } from '../api/contracts';
import { describeError } from '../core/http-errors';
import { LocalCampaigns } from '../core/local-campaigns';

@Component({ standalone: true, imports: [RouterLink, FormsModule, DatePipe], template: `
  <div class="page-heading"><div><p class="eyebrow">Campaign workspace</p><h1>Campaigns</h1><p>Track CV screening for each job description.</p><p role="status">Service: {{ service() }}</p></div><a class="button" routerLink="/campaigns/new">New campaign</a></div>
  <section class="card"><h2>Open a campaign</h2><p>Enter a campaign ID to reopen it directly.</p>
    <form (ngSubmit)="open()" class="inline-form"><label for="campaign-id">Campaign ID</label><input id="campaign-id" name="id" [(ngModel)]="id" autocomplete="off" required><button type="submit">Open</button></form>
    @if (error()) { <p class="error" role="alert">{{ error() }}</p> }
  </section>
  <section class="card"><h2>Your campaigns</h2>
    @if (listLoading()) { <p role="status">Loading campaigns…</p> }
    @if (listError()) { <p class="error" role="alert">{{ listError() }}</p> }
    @if (campaigns(); as listPage) {
      @if (listPage.campaigns.length) { <div class="table-scroll"><table><thead><tr><th scope="col">Campaign</th><th scope="col">Status</th><th scope="col">Created</th><th scope="col">JDs</th><th scope="col">Accepted CVs</th></tr></thead><tbody>
        @for (item of listPage.campaigns; track item.campaign_id) { <tr><td><a [routerLink]="['/campaigns', item.campaign_id]">{{ item.campaign_id }}</a></td><td>{{ item.status }}</td><td>{{ item.created_at | date:'medium' }}</td><td>{{ item.jd_count }}</td><td>{{ item.accepted_count }}</td></tr> }
      </tbody></table></div><div class="pagination"><button type="button" class="secondary" (click)="goPage(-1)" [disabled]="page() === 1">Previous</button><span>Page {{ page() }}</span><button type="button" class="secondary" (click)="goPage(1)" [disabled]="page() * pageSize >= listPage.total">Next</button></div> }
      @else { <p>No campaigns in your account yet.</p> }
    }
  </section>
  <section class="card"><h2>Opened this session</h2><p class="muted">Access is checked by the server.</p>
    @if (local.ids().length) { <ul class="link-list">@for (id of local.ids(); track id) { <li><a [routerLink]="['/campaigns', id]">{{ id }}</a></li> }</ul> }
    @else { <p>No campaigns opened in this session yet.</p> }
  </section>
` })
export class HomeComponent {
  readonly local = inject(LocalCampaigns);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  private readonly destroyRef = inject(DestroyRef);
  private readonly api = inject(CampaignApi);
  private readonly http = inject(HttpClient);
  readonly error = signal('');
  readonly service = signal('Checking availability…');
  readonly campaigns = signal<CampaignListPage | null>(null);
  readonly listLoading = signal(false);
  readonly listError = signal('');
  readonly page = signal(1);
  readonly pageSize = 20;
  id = '';
  constructor() {
    this.http.get<{ status: string }>('/health').subscribe({ next: response => this.service.set(response.status === 'healthy' ? 'Available' : 'Degraded'), error: () => this.service.set('Unavailable') });
    this.route.queryParamMap.pipe(map(params => {
      const value = Number(params.get('page'));
      return Number.isSafeInteger(value) && value > 0 ? value : 1;
    }), switchMap(page => {
      this.page.set(page); this.listLoading.set(true); this.listError.set(''); this.campaigns.set(null);
      return this.api.list(this.pageSize, (page - 1) * this.pageSize).pipe(catchError(error => {
        this.listError.set(describeError(error)); this.listLoading.set(false); return EMPTY;
      }));
    }), takeUntilDestroyed(this.destroyRef)).subscribe(response => { this.campaigns.set(response); this.listLoading.set(false); });
  }
  goPage(delta: number): void { void this.router.navigate([], { relativeTo: this.route, queryParams: { page: this.page() + delta } }); }
  open(): void {
    const id = this.id.trim();
    if (!/^[a-zA-Z0-9_-]{1,128}$/.test(id)) { this.error.set('Enter a valid campaign ID.'); return; }
    void this.router.navigate(['/campaigns', id]);
  }
}
