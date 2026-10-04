import { initialProtocol } from './protocol';
import type { AccessResolution, Benchmark, Contrast, DescriptiveAnalysis, Document, Experiment, Evidence, Invitation, Measurement, Member, Project, Protocol, Publication, Run, Session } from './types';

export const isStaticDemo = () => import.meta.env.VITE_STATIC_DEMO === 'true';
export const DEMO_BANNER = 'Static demo: sample data only, no backend connected.';
export const DEMO_UNAVAILABLE = 'Not available in demo mode. Connect a hosted backend for this feature.';
export const DEMO_DATA_LABEL = 'Synthetic demo data. Not real research results.';

export class DemoError extends Error {
  constructor(message: string, public status: number) { super(message); this.name = 'DemoError'; }
}

const NOW = '2025-01-15T09:00:00Z';
const hash = (n: number) => `${n.toString(16).padStart(2, '0')}`.repeat(32);
const session: Session = { authenticated: true, development_mode: false, user: { id: 'demo-user', login: 'demo-user', name: 'Demo Reviewer', is_owner: true } };

const demoProtocol = (name: string): Protocol => ({
  ...initialProtocol(name), analysis_mode: 'inferential', outcome: 'droplet_size', effect_measure: 'MD', unit: 'um',
  outcome_definition: 'Mean droplet diameter (synthetic)', measurement_method: 'laser diffraction', comparator: 'untreated nanocellulose',
  time_point: '1 day', concentration_basis: null,
});
const evidence = (doc: number, page: number, excerpt: string): Evidence => ({ document_hash: hash(doc), page, source_type: 'table', locator: `Table ${page}`, excerpt: `${excerpt} (synthetic)` });
const measurement = (id: string, experiment_id: string, value: number, sd: number, doc: number): Measurement => ({
  id, experiment_id, outcome: 'droplet_size', raw_value: `${value} ± ${sd}`, value, unit: 'um', qualifier: 'exact', measurement_method: 'laser diffraction',
  statistic: 'mean', time_value: 1, time_unit: 'day', uncertainty_type: 'SD', uncertainty: sd, n_independent: 3, n_technical: 3, origin: 'reported',
  status: 'accepted', validation_notes: ['Synthetic demo measurement.'], evidence: [evidence(doc, 2, `Mean droplet size ${value} um`)],
});
const buildExperiments = (): Experiment[] => {
  const rows: [string, string, number, number, number, number][] = [['e1', 'Sample A (untreated)', 12.4, 1.1, 1, 1], ['e2', 'Sample B (TEMPO-oxidized)', 8.2, 0.9, 1, 1], ['e3', 'Sample C (untreated)', 15.0, 1.6, 2, 2], ['e4', 'Sample D (TEMPO-oxidized)', 9.7, 1.2, 2, 2]];
  return rows.map(([id, sample_label, value, sd, doc, family]) => ({
    id, sample_label, doi: `10.0000/demo.${doc}`, study_family: `family-${family}`, attributes: [{ name: 'Type_of_Nanocellulose', text: 'Cellulose nanocrystals (synthetic)', status: 'accepted' as const }],
    measurements: [measurement(`m-${id}`, id, value, sd, doc)],
  }));
};
const buildDocuments = (): Document[] => [1, 2, 3].map(n => ({ id: `doc-${n}`, filename: `synthetic-paper-${n}.pdf`, sha256: hash(n), doi: `10.0000/demo.${n}`, status: n === 3 ? 'uploaded' : 'extracted', pages: 8 + n, error: null }));
const buildPublications = (): Publication[] => [
  { id: 'pub-1', state: 'pending_extraction', source: 'demo', source_id: 'demo-1', title: 'Synthetic study of cellulose nanocrystal emulsions', doi: '10.0000/demo.10', authors: ['A. Example', 'B. Sample'], year: 2024, url: null, abstract: 'Synthetic abstract for demonstration.', full_text_url: null, license: 'CC-BY-4.0', version: '1', is_preprint: false, status: 'active', related_dois: [], updated_at: NOW, discovered_at: NOW },
  { id: 'pub-2', state: 'extracted', source: 'demo', source_id: 'demo-2', title: 'Synthetic preprint on Pickering stabilization', doi: null, authors: ['C. Placeholder'], year: 2025, url: null, abstract: null, full_text_url: null, license: null, version: null, is_preprint: true, status: 'active', related_dois: [], updated_at: NOW, discovered_at: NOW },
];
const buildRuns = (): Run[] => [{ id: 'run-1', status: 'completed', budget_usd: 5, spent_usd: 0.42, reserved_usd: 0, progress: { completed: 2, total: 2 }, result: null, error: null, created_at: NOW }];
const buildMembers = (): Member[] => [{ id: 'mem-1', user_id: 'demo-user', login: 'demo-user', name: 'Demo Reviewer', role: 'owner' }, { id: 'mem-2', user_id: 'demo-editor', login: 'demo-editor', name: 'Sample Editor', role: 'editor' }];
const buildBenchmark = (): Benchmark => ({ label: 'Synthetic benchmark', counts: { reference_rows: 4, extracted_rows: 4 }, agreement: { exact_matches: 3, compared_fields: 4, proportion: 0.75 }, missing_source_dois: [], limitations: [DEMO_DATA_LABEL] });

interface State { projects: Project[]; documents: Record<string, Document[]>; runs: Record<string, Run[]>; experiments: Record<string, Experiment[]>; publications: Record<string, Publication[]>; members: Record<string, Member[]>; contrasts: Record<string, Contrast[]>; benchmark: Record<string, Benchmark | null>; result: Record<string, Record<string, unknown> | undefined> }
const freshState = (): State => {
  const projects: Project[] = [
    { id: 'demo-1', name: 'Demo review: nanocellulose emulsions (synthetic)', protocol: demoProtocol('Nanocellulose-stabilized Pickering emulsions'), role: 'owner', last_metadata_check: NOW, last_extraction: NOW, last_synthesis_update: NOW, pending_count: 1, synthesis_stale: false },
    { id: 'demo-2', name: 'Demo review: empty workspace (synthetic)', protocol: demoProtocol('Empty demo review'), role: 'owner', pending_count: 0 },
  ];
  return {
    projects, documents: { 'demo-1': buildDocuments(), 'demo-2': [] }, runs: { 'demo-1': buildRuns(), 'demo-2': [] }, experiments: { 'demo-1': buildExperiments(), 'demo-2': [] },
    publications: { 'demo-1': buildPublications(), 'demo-2': [] }, members: { 'demo-1': buildMembers(), 'demo-2': buildMembers().slice(0, 1) }, contrasts: {}, benchmark: { 'demo-1': buildBenchmark(), 'demo-2': null }, result: {},
  };
};
let state = freshState();
let projectCounter = 0;
const csvCell = (value: unknown) => { const text = String(value ?? ''); return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text; };
const escapeHtml = (text: string) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
export const resetDemoState = () => { state = freshState(); };

const projectOf = (id: string): Project => { const found = state.projects.find(item => item.id === id); if (!found) throw new DemoError('Review not found in the demo data.', 404); return found; };
const measurementsOf = (id: string) => state.experiments[id]?.flatMap(item => item.measurements) ?? [];
const unavailable = (): never => { throw new DemoError(DEMO_UNAVAILABLE, 501); };
const median = (values: number[]) => { const s = [...values].sort((a, b) => a - b); const m = Math.floor(s.length / 2); return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };

function analysisOf(id: string): DescriptiveAnalysis {
  const values = measurementsOf(id).filter(item => item.value != null).map(item => item.value!);
  const groups = values.length ? [{ outcome: 'droplet_size', unit: 'um', measurement_method: 'laser diffraction', statistic: 'mean', time_value: 1, time_unit: 'day', concentration_basis: null, n: values.length, value_min: Math.min(...values), value_max: Math.max(...values), mean: values.reduce((a, b) => a + b, 0) / values.length, median: median(values) }] : [];
  return { groups, warnings: [DEMO_DATA_LABEL], counts: { measurements: values.length }, contrasts: state.contrasts[id] ?? [], ...(state.result[id] ? { inferential_result: state.result[id] } : {}) };
}
function synthesize(id: string, body: { contrasts?: Contrast[] }): Record<string, unknown> {
  const contrasts = body.contrasts ?? []; state.contrasts[id] = contrasts;
  if (!contrasts.length) return { note: 'Descriptive mapping updated (demo).' };
  const effects = contrasts.map(c => ({ effect: c.treatment.mean - c.control.mean, variance: c.treatment.sd ** 2 / c.treatment.n_independent + c.control.sd ** 2 / c.control.n_independent }));
  const weights = effects.map(e => 1 / e.variance); const total = weights.reduce((a, b) => a + b, 0);
  const pooled = effects.reduce((sum, e, i) => sum + e.effect * weights[i], 0) / total; const se = Math.sqrt(1 / total);
  const result = { model: 'fixed-effect (demo, client-side)', effect_measure: 'MD', contrasts: contrasts.length, pooled_effect: pooled, standard_error: se, ci_lower: pooled - 1.96 * se, ci_upper: pooled + 1.96 * se, note: DEMO_DATA_LABEL };
  state.result[id] = result; return result;
}

export function demoRequest<T>(path: string, options: RequestInit = {}): T {
  const method = (options.method ?? 'GET').toUpperCase();
  let body: Record<string, unknown> = {};
  if (typeof options.body === 'string') { try { body = JSON.parse(options.body) as Record<string, unknown>; } catch { /* ignore malformed demo bodies */ } }
  const parts = path.split('?')[0].split('/').filter(Boolean).map(decodeURIComponent);
  const key = `${method} ${parts[0] ?? ''}`;
  const result = ((): unknown => {
    if (key === 'GET session') return session;
    if (key === 'POST development') return session;
    if (key === 'GET projects' && parts.length === 1) return state.projects;
    if (key === 'POST projects' && parts.length === 1) {
      const protocol = body.protocol as Protocol; const id = `demo-new-${++projectCounter}`;
      const created: Project = { id, name: `${String(body.name)} (synthetic)`, protocol, role: 'owner', pending_count: 0 };
      state.projects.push(created); state.documents[id] = []; state.runs[id] = []; state.experiments[id] = []; state.publications[id] = []; state.members[id] = buildMembers().slice(0, 1); state.benchmark[id] = null; return created;
    }
    if (parts[0] === 'projects') {
      const id = parts[1]; const project = projectOf(id); const sub = parts[2];
      if (!sub && method === 'GET') return project;
      if (sub === 'protocol') { if (method === 'PUT') { project.protocol = body as unknown as Protocol; return project.protocol; } return project.protocol; }
      if (sub === 'documents') { if (method === 'GET') return state.documents[id]; return unavailable(); }
      if (sub === 'runs') { if (method === 'GET') return state.runs[id]; return unavailable(); }
      if (sub === 'experiments') return state.experiments[id];
      if (sub === 'measurements') return measurementsOf(id);
      if (sub === 'publications') return state.publications[id];
      if (sub === 'discover') return unavailable();
      if (sub === 'analysis') return method === 'POST' ? synthesize(id, body) : analysisOf(id);
      if (sub === 'benchmark') { if (method === 'GET') return state.benchmark[id] ?? null; return unavailable(); }
      if (sub === 'members') return state.members[id];
      if (sub === 'invitations') return { token: 'demo-invitation-token', url: `${window.location.origin}${window.location.pathname}?invitation=demo-invitation-token`, expires_at: '2099-01-01T00:00:00Z' } satisfies Invitation;
    }
    if (parts[0] === 'measurements' && method === 'PATCH') {
      const target = Object.values(state.experiments).flat().flatMap(item => item.measurements).find(item => item.id === parts[1]);
      if (!target) throw new DemoError('Measurement not found in the demo data.', 404);
      target.status = body.status as Measurement['status']; target.validation_notes = [...target.validation_notes, String(body.notes ?? '')]; return target;
    }
    if (parts[0] === 'publications' && parts[2] === 'access') return { doi: null, pmcid: null, locations: [], status: 'demo', publication_status: 'active', limitations: [DEMO_UNAVAILABLE] } satisfies AccessResolution;
    if (parts[0] === 'publications' && parts[2] === 'acquire') {
      const publication = Object.values(state.publications).flat().find(item => item.id === parts[1]);
      const added: Document = { id: `doc-acquired-${parts[1]}`, filename: `${parts[1]}-synthetic.pdf`, sha256: hash(200), doi: publication?.doi ?? null, status: 'uploaded', pages: 6, error: null };
      Object.entries(state.publications).forEach(([projectId, items]) => { if (items.some(item => item.id === parts[1])) state.documents[projectId].push(added); });
      return added;
    }
    return unavailable();
  })();
  return structuredClone(result) as T;
}

export function demoExport(projectId: string, format: string): Blob {
  const rows = measurementsOf(projectId);
  if (format === 'csv') {
    const lines = ['id,experiment_id,outcome,value,unit,status', ...rows.map(item => [item.id, item.experiment_id, item.outcome, item.value, item.unit, item.status].map(csvCell).join(','))];
    return new Blob([`# ${DEMO_DATA_LABEL}\n${lines.join('\n')}\n`], { type: 'text/csv' });
  }
  if (format === 'html') return new Blob([`<!doctype html><title>Demo export</title><p>${DEMO_DATA_LABEL}</p><pre>${escapeHtml(JSON.stringify(rows, null, 2))}</pre>`], { type: 'text/html' });
  if (format === 'parquet') throw new DemoError(DEMO_UNAVAILABLE, 501);
  return new Blob([JSON.stringify({ notice: DEMO_DATA_LABEL, measurements: rows }, null, 2)], { type: 'application/json' });
}

export const demoDocumentUrl = (id: string) => `data:text/plain;charset=utf-8,${encodeURIComponent(`${DEMO_DATA_LABEL}\nNo PDF is available for ${id} in demo mode.`)}`;
export const demoArtifactUrl = () => `data:image/svg+xml;charset=utf-8,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="320" height="120"><rect width="100%" height="100%" fill="#1b2b40"/><text x="16" y="64" fill="#aebfd2" font-size="14">Synthetic figure placeholder</text></svg>')}`;
