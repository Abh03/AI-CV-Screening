import { Injectable, signal } from '@angular/core';
import { IntakeReport } from '../api/contracts';

/** Session-only display hints. Authorization always comes from the API. */
@Injectable({ providedIn: 'root' })
export class LocalCampaigns {
  readonly ids = signal<string[]>([]);
  private readonly names = new Map<string, Map<string, string>>();
  remember(id: string): void {
    if (!/^[a-zA-Z0-9_-]{1,128}$/.test(id)) return;
    this.ids.set([id, ...this.ids().filter(value => value !== id)].slice(0, 20));
  }
  saveNames(id: string, report: IntakeReport): void {
    this.names.set(id, new Map(report.accepted.map(item => [item.candidate_id, item.name])));
  }
  name(id: string, candidate: string): string | null {
    return this.names.get(id)?.get(candidate) ?? null;
  }
  clear(): void { this.ids.set([]); this.names.clear(); }
}
