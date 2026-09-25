import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpEvent } from '@angular/common/http';
import { Observable } from 'rxjs';
import { publicConfig } from '../core/config';
import { CampaignCreate, CampaignCreated, CampaignListPage, CampaignStatus, JdDefinition, JdsResponse, OutcomeStatus, ResultsPage, UploadResponse } from './contracts';

@Injectable({ providedIn: 'root' })
export class CampaignApi {
  private readonly http = inject(HttpClient);
  private readonly base = `${publicConfig.apiBase}/campaigns`;

  create(body: CampaignCreate): Observable<CampaignCreated> { return this.http.post<CampaignCreated>(this.base, body); }
  list(limit: number, offset: number): Observable<CampaignListPage> {
    return this.http.get<CampaignListPage>(this.base, { params: { limit, offset } });
  }
  definition(id: string, jd: string): Observable<JdDefinition> {
    return this.http.get<JdDefinition>(`${this.base}/${encodeURIComponent(id)}/jds/${encodeURIComponent(jd)}/definition`);
  }
  upload(id: string, file: File): Observable<HttpEvent<UploadResponse>> {
    return this.http.post<UploadResponse>(`${this.base}/${encodeURIComponent(id)}/archive`, file,
      { headers: { 'Content-Type': 'application/zip' }, observe: 'events', reportProgress: true });
  }
  status(id: string): Observable<CampaignStatus> { return this.http.get<CampaignStatus>(`${this.base}/${encodeURIComponent(id)}`); }
  jds(id: string): Observable<JdsResponse> { return this.http.get<JdsResponse>(`${this.base}/${encodeURIComponent(id)}/jds`); }
  rankings(id: string, jd: string, limit: number, offset: number): Observable<ResultsPage> {
    return this.http.get<ResultsPage>(`${this.base}/${encodeURIComponent(id)}/jds/${encodeURIComponent(jd)}/rankings`, { params: { limit, offset } });
  }
  outcomes(id: string, jd: string, status: OutcomeStatus, limit: number, offset: number): Observable<ResultsPage> {
    return this.http.get<ResultsPage>(`${this.base}/${encodeURIComponent(id)}/jds/${encodeURIComponent(jd)}/outcomes`, { params: { status, limit, offset } });
  }
}
