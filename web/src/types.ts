export interface User { id: string; login: string; name?: string | null; is_owner?: boolean }
export interface Session { authenticated: boolean; user: User | null; development_mode: boolean }
export type Role = 'owner' | 'editor' | 'reader';
export type ReviewStatus = 'candidate' | 'accepted' | 'uncertain' | 'rejected' | 'stale';
export interface Protocol {
  name: string; version: string; topic: string; inclusion: string[]; exclusion: string[];
  variables: string[]; queries: Record<string, string>; enabled_sources: string[];
  analysis_mode: 'descriptive' | 'inferential'; outcome: string; effect_measure: 'MD' | 'ROM';
  unit?: string | null; outcome_definition?: string | null; measurement_method?: string | null; comparator?: string | null;
  time_point?: string | null; concentration_basis?: string | null;
}
export interface Project {
  id: string; name: string; protocol: Protocol; role?: Role;
  last_metadata_check?: string | null; last_extraction?: string | null;
  last_synthesis_update?: string | null; pending_count?: number;
  next_metadata_check?: string | null; synthesis_stale?: boolean; discovery_report?: unknown;
}
export interface Document {
  id: string; filename: string; sha256: string; doi?: string | null; status: string;
  pages?: number | null; error?: string | null;
}
export interface Evidence {
  document_hash: string; page: number; source_type: 'text' | 'table' | 'figure' | 'microscopy';
  locator: string; excerpt: string; bounding_box?: number[] | null; figure_id?: string | null;
  panel?: string | null; series?: string | null; artifact_key?: string | null;
  calibration?: string | null; digitization_uncertainty?: number | null;
}
export interface Measurement {
  id: string; experiment_id: string; outcome: string; raw_value: string; value?: number | null;
  unit?: string | null; normalized_value?: number | null; normalized_unit?: string | null;
  qualifier: 'exact' | 'lt' | 'le' | 'gt' | 'ge' | 'range' | 'missing';
  lower_bound?: number | null; upper_bound?: number | null; concentration_basis?: string | null;
  statistic?: string | null; measurement_method?: string | null; time_value?: number | null;
  time_unit?: string | null; uncertainty_type: 'SD' | 'SE' | 'CI' | 'unknown' | 'none';
  uncertainty?: number | null; ci_lower?: number | null; ci_upper?: number | null;
  n_independent?: number | null; n_technical?: number | null;
  origin: 'reported' | 'digitized' | 'derived' | 'curve_sample'; status: ReviewStatus;
  missing_reason?: string | null; validation_notes: string[]; evidence: Evidence[];
}
export interface Attribute { name: string; text: string; value?: number | null; unit?: string | null; basis?: string | null; status?: ReviewStatus; evidence?: Evidence[]; validation_notes?: string[] }
export interface Experiment {
  id: string; sample_label: string; doi?: string | null; study_family: string;
  attributes: Attribute[]; measurements: Measurement[];
}
export interface Run {
  id: string; status: string; budget_usd: number; spent_usd: number; reserved_usd: number;
  progress?: unknown; result?: unknown; error?: string | null; created_at: string;
}
export interface Publication {
  id: string; state: string; source: string; source_id: string; title: string;
  doi?: string | null; authors: string[]; year?: number | null; url?: string | null;
  abstract?: string | null; full_text_url?: string | null; license?: string | null;
  version?: string | null; is_preprint: boolean; status: string; related_dois: string[];
  updated_at?: string | null; discovered_at?: string | null;
}
export interface Member { id?: string; user_id?: string; login?: string; name?: string; role: Role }
export interface AccessLocation {
  url: string; kind: string; provider: string; license?: string | null; version?: string | null;
  manuscript: boolean; md5?: string | null; redistribution_requires_license_review: boolean;
}
export interface AccessResolution {
  doi?: string | null; pmcid?: string | null; locations: AccessLocation[];
  status: string; publication_status: string; limitations: string[];
}
export interface Invitation { token: string; url: string; expires_at: string }
export interface DescriptiveGroup {
  outcome: string; unit?: string | null; measurement_method?: string | null; statistic?: string | null;
  time_value?: number | null; time_unit?: string | null; concentration_basis?: string | null;
  n: number; value_min: number; value_max: number; mean: number; median: number;
}
export interface DescriptiveAnalysis {
  groups?: DescriptiveGroup[]; warnings?: string[]; constraints?: unknown; excluded?: unknown;
  counts?: Record<string, number>; [key: string]: unknown;
}
export interface AnalysisSpec {
  outcome: string; concentration_basis: string | null;
  outcome_definition: string; measurement_method: string; comparator: string;
  time_point: string; unit: string; effect_measure: 'MD' | 'ROM';
}
export interface Contrast extends Omit<AnalysisSpec, 'effect_measure'> {
  id: string; study_family: string; source_measurement_ids: string[];
  treatment_measurement_id: string; control_measurement_id: string;
  treatment: { mean: number; sd: number; n_independent: number };
  control: { mean: number; sd: number; n_independent: number };
}
export interface Benchmark {
  label?: string; counts?: Record<string, number>;
  agreement?: { exact_matches?: number; compared_fields?: number; proportion?: number | null; numerical_mae?: unknown };
  coverage?: unknown; ambiguous_matches?: unknown; field_comparisons?: unknown;
  missing_source_dois?: string[]; limitations?: string[]; [key: string]: unknown;
}
