import type { Attribute, Document, Evidence, Experiment, Measurement, Publication } from '../src/types';

export type JsonRecord = Record<string, unknown>;
export interface ReportDocument extends Document { created_at?: string; coverage?: unknown; inventory?: unknown; source_format?: 'pdf' | 'xml' | 'media' }
export interface ReportEvidence extends Omit<Evidence, 'page'> { page: number | null; source_format?: 'pdf' | 'xml' | 'media'; xml_element?: string | null; asset_path?: string | null }
export interface ReportMeasurement extends Omit<Measurement, 'evidence'> { evidence: ReportEvidence[] }
export interface ReportAttribute extends Omit<Attribute, 'evidence'> { evidence?: ReportEvidence[] }
export interface ReportExperiment extends Omit<Experiment, 'measurements' | 'attributes'> { document_id?: string; source_status?: string; measurements: ReportMeasurement[]; attributes: ReportAttribute[] }
export interface Artifact { sha256: string; filename?: string; relative_path?: string; artifact_key?: string }
export interface Snapshot {
  schema_version: 2; project: JsonRecord; protocol: JsonRecord; freshness: JsonRecord;
  documents: ReportDocument[]; experiments: ReportExperiment[]; publications: Publication[];
  coverage: unknown; synthesis: JsonRecord; benchmark: JsonRecord; run: unknown; provenance: unknown;
  artifacts: Artifact[]; embedded_artifacts: { sha256: string; mime_type: string; base64: string }[];
}
const record = (value: unknown): JsonRecord => value && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {};
export const text = (value: unknown, fallback = 'Not reported') => typeof value === 'string' ? value || fallback : typeof value === 'number' || typeof value === 'boolean' ? String(value) : fallback;
export const object = record;
const statuses = new Set(['candidate', 'accepted', 'uncertain', 'rejected', 'stale']);
const qualifiers = new Set(['exact', 'lt', 'le', 'gt', 'ge', 'range', 'missing']);
function finite(value: unknown, depth = 0): void {
  if (depth > 100) throw new Error('The dataset exceeds the supported nesting depth.');
  if (typeof value === 'number' && !Number.isFinite(value)) throw new Error('Non-finite scientific values are not supported.');
  if (value && typeof value === 'object') Object.values(value).forEach(item => finite(item, depth + 1));
}
export function normalizeSnapshot(value: unknown): Snapshot {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Expected an offline dataset object.');
  const raw = value as JsonRecord;
  if ((raw.schema_version ?? 2) !== 2) throw new Error('Expected schema_version 2.');
  finite(raw);
  const arrays = ['documents', 'experiments', 'publications', 'artifacts', 'embedded_artifacts'];
  arrays.forEach(key => { if (raw[key] != null && (!Array.isArray(raw[key]) || (raw[key] as unknown[]).some(item => !item || typeof item !== 'object' || Array.isArray(item)))) throw new Error(`${key} must be an array of objects.`); });
  const experiments = (raw.experiments ?? []) as ReportExperiment[];
  for (const document of (raw.documents ?? []) as ReportDocument[]) {
    if (![document.id, document.filename, document.sha256, document.status].every(value => typeof value === 'string')) throw new Error('Documents need a text ID, filename, SHA-256, and status.');
  }
  const ids = new Set<string>();
  for (const experiment of experiments) {
    if (![experiment.id, experiment.sample_label, experiment.study_family].every(item => typeof item === 'string' && item.length)) throw new Error('Each experiment needs an ID, sample label, and study family.');
    if (!Array.isArray(experiment.measurements ?? []) || !Array.isArray(experiment.attributes ?? [])) throw new Error('Experiment measurements and attributes must be arrays.');
    for (const item of experiment.measurements ?? []) {
      if (typeof item.id !== 'string' || !item.id || ids.has(item.id)) throw new Error('Measurement IDs must be unique.');
      ids.add(item.id);
      if (item.experiment_id !== experiment.id || typeof item.outcome !== 'string' || typeof item.raw_value !== 'string') throw new Error('Measurement identity or original value is invalid.');
      if (!statuses.has(item.status) || !qualifiers.has(item.qualifier)) throw new Error('Measurement review status or qualifier is invalid.');
      if (item.qualifier === 'missing' && item.value != null) throw new Error('A missing measurement cannot contain an observed value.');
      for (const field of ['value', 'normalized_value', 'uncertainty', 'time_value', 'ci_lower', 'ci_upper', 'lower_bound', 'upper_bound'] as const) {
        if (item[field] != null && typeof item[field] !== 'number') throw new Error(`${field} must be numeric or null.`);
      }
      for (const field of ['n_independent', 'n_technical'] as const) {
        if (item[field] != null && (!Number.isInteger(item[field]) || item[field]! < 1)) throw new Error(`${field} must be a positive integer or null.`);
      }
      if (item.evidence != null && !Array.isArray(item.evidence)) throw new Error('Measurement evidence must be an array.');
      for (const evidence of item.evidence ?? []) {
        const format = evidence.source_format ?? 'pdf';
        if (typeof evidence.document_hash !== 'string' || !['pdf', 'xml', 'media'].includes(format)) throw new Error('Source evidence needs a hash and a supported format.');
        if (format === 'pdf' && (!Number.isInteger(evidence.page) || evidence.page == null || evidence.page < 1)) throw new Error('PDF evidence needs a positive page locator.');
        if (format !== 'pdf' && evidence.page != null) throw new Error('XML and media evidence must not invent PDF page numbers.');
        if (format === 'xml' && !(typeof evidence.xml_element === 'string' && evidence.xml_element.trim())) throw new Error('XML evidence needs an element ID or structural path.');
        if (format === 'media' && !(typeof evidence.asset_path === 'string' && evidence.asset_path && !evidence.asset_path.startsWith('/') && !evidence.asset_path.includes('\\') && !evidence.asset_path.split('/').includes('..'))) throw new Error('Media evidence needs a safe workspace-relative asset path.');
      }
    }
  }
  const project = record(raw.project);
  return { schema_version: 2, project, protocol: record(raw.protocol ?? project.protocol), freshness: { ...project, ...record(raw.freshness) },
    documents: (raw.documents ?? []) as ReportDocument[], experiments, publications: (raw.publications ?? []) as Publication[],
    coverage: raw.coverage ?? {}, synthesis: record(raw.synthesis), benchmark: record(raw.benchmark), run: raw.run ?? {}, provenance: raw.provenance ?? {},
    artifacts: (raw.artifacts ?? []) as Artifact[], embedded_artifacts: (raw.embedded_artifacts ?? []) as Snapshot['embedded_artifacts'] };
}
const inactive = new Set(['retracted', 'quarantined', 'stale', 'superseded', 'corrected', 'excluded']);
export function measurements(snapshot: Snapshot): ReportMeasurement[] {
  const documents = new Map(snapshot.documents.map(item => [item.sha256, item]));
  return snapshot.experiments.flatMap(experiment => (experiment.measurements ?? []).map(item => ({ ...item,
    evidence: item.evidence ?? [], validation_notes: item.validation_notes ?? [],
    status: inactive.has(experiment.source_status ?? '') || (item.evidence ?? []).some(evidence => inactive.has(documents.get(evidence.document_hash)?.status ?? '')) ? 'stale' as const : item.status,
  })));
}
export function stale(snapshot: Snapshot): boolean {
  return Boolean(snapshot.freshness.synthesis_stale) || snapshot.publications.some(item => item.state === 'pending_extraction') || snapshot.documents.some(item => inactive.has(item.status)) || measurements(snapshot).some(item => ['candidate', 'uncertain', 'stale'].includes(item.status));
}
export function csvSnapshot(snapshot: Snapshot): string {
  const columns = ['doi', 'sample_label', 'study_family', 'measurement_id', 'outcome', 'raw_value', 'value', 'unit', 'qualifier', 'normalized_value', 'normalized_unit', 'measurement_method', 'statistic', 'time_value', 'time_unit', 'concentration_basis', 'uncertainty_type', 'uncertainty', 'ci_lower', 'ci_upper', 'n_independent', 'n_technical', 'origin', 'status', 'evidence'];
  const experiments = new Map(snapshot.experiments.map(item => [item.id, item]));
  const quote = (value: unknown) => {
    let string = value == null ? '' : typeof value === 'string' ? value : String(value);
    if (typeof value === 'string' && /^[=+\-@]/.test(string)) string = "'" + string;
    return '"' + string.replaceAll('"', '""') + '"';
  };
  return [columns.map(quote).join(','), ...measurements(snapshot).map(item => {
    const experiment = experiments.get(item.experiment_id);
    const row: JsonRecord = { ...item, doi: experiment?.doi, sample_label: experiment?.sample_label, study_family: experiment?.study_family, measurement_id: item.id, evidence: JSON.stringify(item.evidence) };
    return columns.map(key => quote(row[key])).join(',');
  })].join('\r\n');
}
export async function sha256(content: ArrayBuffer): Promise<string> {
  if (!globalThis.crypto?.subtle) throw new Error('Local SHA-256 verification is unavailable in this browser. Use a recent Chrome, Firefox, or Safari.');
  const bytes = new Uint8Array(await crypto.subtle.digest('SHA-256', content));
  return [...bytes].map(value => value.toString(16).padStart(2, '0')).join('');
}
export function artifactHash(key?: string | null): string | undefined {
  return key?.match(/(?:^|\/)([a-f0-9]{64})(?:-[^/]*)?\.(?:png|jpe?g|webp)$/i)?.[1].toLowerCase();
}
export function sourceLocation(evidence: ReportEvidence): string {
  if (evidence.source_format === 'xml') return `XML ${evidence.xml_element ?? 'locator not reported'}`;
  if (evidence.source_format === 'media') return `Media ${evidence.asset_path ?? 'asset not reported'}`;
  return `p. ${evidence.page}`;
}
