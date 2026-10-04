import type { Measurement } from './types';
export const humanize = (value: string) => value.replaceAll('_', ' ').replace(/\b\w/g, char => char.toUpperCase());
export const money = (value?: number | null) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(value ?? 0);
export const number = (value?: number | null) => value == null || !Number.isFinite(value) ? 'Not reported' : new Intl.NumberFormat('en-US', { maximumSignificantDigits: 6 }).format(value);
export function date(value?: string | null, timeZone?: string) {
  if (!value) return 'Not yet';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone, timeZoneName: 'short' });
}
export function safeExternalUrl(value?: string | null): string | undefined {
  if (!value) return undefined;
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined; }
  catch { return undefined; }
}
export interface ComparableGroup { key: string; outcome: string; unit: string; method: string; statistic: string; time: string; basis: string; measurements: Measurement[]; values: number[] }
export function comparableGroups(measurements: Measurement[], includeCandidates = false): ComparableGroup[] {
  const groups = new Map<string, ComparableGroup>();
  for (const item of measurements) {
    if (item.status !== 'accepted' && !(includeCandidates && item.status === 'candidate')) continue;
    if (item.qualifier !== 'exact' || item.origin === 'curve_sample') continue;
    const useNormalized = item.normalized_value != null && Boolean(item.normalized_unit);
    const value = useNormalized ? item.normalized_value : item.value;
    const unit = useNormalized ? item.normalized_unit : item.unit;
    if (value == null || !Number.isFinite(value) || !unit || !item.measurement_method || !item.statistic) continue;
    const time = item.time_value == null ? 'Time not reported' : `${item.time_value} ${item.time_unit ?? '(unit not reported)'}`;
    const basis = item.concentration_basis || 'Basis not reported / not applicable';
    const key = JSON.stringify([item.outcome, unit, item.measurement_method, item.statistic, item.time_value ?? null, item.time_unit ?? null, item.concentration_basis ?? null]);
    if (!groups.has(key)) groups.set(key, { key, outcome: item.outcome, unit, method: item.measurement_method, statistic: item.statistic, time, basis, measurements: [], values: [] });
    groups.get(key)!.measurements.push(item); groups.get(key)!.values.push(value);
  }
  return [...groups.values()].sort((a, b) => a.outcome.localeCompare(b.outcome) || a.method.localeCompare(b.method));
}
export function progressInfo(progress: unknown): { label: string; percent: number | null } {
  if (typeof progress === 'number' && Number.isFinite(progress)) return { label: 'Extraction in progress', percent: Math.max(0, Math.min(100, progress <= 1 ? progress * 100 : progress)) };
  if (progress && typeof progress === 'object') {
    const item = progress as Record<string, unknown>;
    const label = String(item.phase ?? item.stage ?? item.message ?? 'Working through the evidence');
    const explicit = item.percent ?? item.percentage;
    if (typeof explicit === 'number') return { label: humanize(label), percent: Math.max(0, Math.min(100, explicit)) };
    if (typeof item.completed === 'number' && typeof item.total === 'number' && item.total > 0) return { label: humanize(label), percent: Math.max(0, Math.min(100, item.completed / item.total * 100)) };
    return { label: humanize(label), percent: null };
  }
  return { label: typeof progress === 'string' ? progress : 'Waiting for a progress update', percent: null };
}
