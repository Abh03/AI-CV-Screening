import { CampaignCounts } from '../api/contracts';

export function pairProgress(counts: CampaignCounts): { complete: number; total: number; percent: number } {
  const total = counts.cvs * counts.jds;
  const complete = Math.min(Math.max(counts.terminal_pairs, 0), total);
  return { complete, total, percent: total ? Math.round(complete / total * 100) : 0 };
}

export function countEntries(counts: Record<string, number>): { label: string; count: number }[] {
  return Object.entries(counts).filter(([, count]) => count > 0).map(([label, count]) => ({ label: label.replaceAll('_', ' '), count }));
}
