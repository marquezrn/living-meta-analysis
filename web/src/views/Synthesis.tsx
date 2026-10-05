import { FlaskConical, Plus, Sigma, Trash2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import type { AnalysisSpec, Contrast, DescriptiveAnalysis, Experiment, Measurement, Project } from '../types';
import { humanize, number } from '../utils';
import { Empty, Field, JsonDetails, Notice, PageHeader, Spinner } from '../components/UI';

interface ContrastDraft { id: string; treatment_id: string; control_id: string }
interface LoadedSynthesis { context: string; analysis: DescriptiveAnalysis; result: Record<string, unknown> | null; historical: boolean }
const newContrast = (): ContrastDraft => ({ id: crypto.randomUUID(), treatment_id: '', control_id: '' });
function storedInference(analysis: DescriptiveAnalysis) {
  const historical = analysis.historical_inferential_result ?? null;
  const result = analysis.inferential_result ?? historical;
  return {
    result: result && typeof result === 'object' && !Array.isArray(result) ? result as Record<string, unknown> : null,
    historical: Boolean(historical || analysis.synthesis_stale || analysis.inferential_stale),
  };
}
export default function Synthesis({ project, canEdit, refreshKey, measurements, experiments }: { project: Project; canEdit: boolean; refreshKey: number; measurements: Measurement[]; experiments: Experiment[] }) {
  const [loaded, setLoaded] = useState<LoadedSynthesis | null>(null);
  const [loading, setLoading] = useState(true); const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const [unit, setUnit] = useState(project.protocol.unit ?? ''); const [contrasts, setContrasts] = useState<ContrastDraft[]>([]);
  const requestVersion = useRef(0);
  const context = JSON.stringify([project.id, refreshKey, project.protocol]);
  const current = loaded?.context === context ? loaded : null;
  const analysis = current?.analysis ?? null;
  const result = !loading && !busy ? current?.result ?? null : null;
  const historical = Boolean(result?.status === 'completed' && (current?.historical || project.synthesis_stale));
  useEffect(() => {
    const version = ++requestVersion.current;
    setLoading(true); setBusy(false); setError(''); setLoaded(null);
    void api.analysis(project.id).then(value => {
      if (version === requestVersion.current) setLoaded({ context, analysis: value, ...storedInference(value) });
    }).catch(reason => {
      if (version === requestVersion.current) { setLoaded(null); setError(reason instanceof Error ? reason.message : 'Scientific summary could not be loaded.'); }
    }).finally(() => { if (version === requestVersion.current) setLoading(false); });
    return () => { requestVersion.current += 1; };
  }, [project.id, context]);
  useEffect(() => { setContrasts([]); }, [project.id]);
  const protocol = project.protocol;
  useEffect(() => { setUnit(protocol.unit ?? ''); }, [protocol.unit]);
  const spec: AnalysisSpec = { outcome: protocol.outcome, outcome_definition: protocol.outcome_definition ?? '', measurement_method: protocol.measurement_method ?? '', comparator: protocol.comparator ?? '', time_point: protocol.time_point ?? '', concentration_basis: protocol.concentration_basis ?? null, unit, effect_measure: protocol.effect_measure };
  const eligible = measurements.filter(item => item.status === 'accepted' && item.qualifier === 'exact' && item.origin !== 'curve_sample' && item.value != null && Number.isFinite(item.value) && item.uncertainty_type === 'SD' && item.uncertainty != null && item.uncertainty >= 0 && item.n_independent != null && Number.isInteger(item.n_independent) && item.n_independent >= 2 && item.unit);
  const label = (item: Measurement) => { const exp = experiments.find(experiment => experiment.id === item.experiment_id); return `${exp?.sample_label ?? item.experiment_id} · ${humanize(item.outcome)} ${number(item.value)} ${item.unit} · ${item.id.slice(0, 8)}`; };
  const get = (id: string) => eligible.find(item => item.id === id);
  const validSpec = protocol.analysis_mode === 'inferential' && Object.entries(spec).filter(([key]) => key !== 'concentration_basis').every(([,value]) => typeof value === 'string' && value.trim());
  const valid = validSpec && contrasts.length > 0 && contrasts.every(row => { const treatment = get(row.treatment_id); const control = get(row.control_id); return treatment && control && treatment.id !== control.id && treatment.unit === unit && control.unit === unit && treatment.outcome === spec.outcome && control.outcome === spec.outcome && treatment.measurement_method === spec.measurement_method && control.measurement_method === spec.measurement_method && (spec.effect_measure !== 'ROM' || (treatment.value! > 0 && control.value! > 0)); });
  const select = (id: string, arm: 'treatment' | 'control', measurementId: string) => { setContrasts(items => items.map(item => item.id === id ? { ...item, [`${arm}_id`]: measurementId } : item)); if (!unit && protocol.unit) setUnit(protocol.unit); };
  const synthesize = async () => {
    const version = ++requestVersion.current;
    setBusy(true); setLoading(true); setError(''); setLoaded(null);
    const { effect_measure: _effectMeasure, ...metadata } = spec;
    const rows: Contrast[] = contrasts.map(row => { const treatment = get(row.treatment_id)!; const control = get(row.control_id)!; const experiment = experiments.find(item => item.id === treatment.experiment_id); return { ...metadata, id: row.id, study_family: experiment?.study_family ?? '', treatment_measurement_id: treatment.id, control_measurement_id: control.id, source_measurement_ids: [treatment.id, control.id], treatment: { mean: treatment.value!, sd: treatment.uncertainty!, n_independent: treatment.n_independent! }, control: { mean: control.value!, sd: control.uncertainty!, n_independent: control.n_independent! } }; });
    try {
      const returned = await api.synthesize(project.id, spec, rows);
      const summary = await api.analysis(project.id);
      if (version === requestVersion.current) setLoaded({ context, analysis: summary, result: returned, historical: returned.status === 'completed' && Boolean(summary.synthesis_stale || summary.inferential_stale) });
    } catch (reason) {
      if (version === requestVersion.current) { setLoaded(null); setError(reason instanceof Error ? reason.message : 'Synthesis failed.'); }
    } finally { if (version === requestVersion.current) { setBusy(false); setLoading(false); } }
  };
  const updateMapping = async () => {
    const version = ++requestVersion.current;
    setBusy(true); setLoading(true); setError(''); setLoaded(null);
    try {
      await api.synthesize(project.id, spec, []);
      const summary = await api.analysis(project.id);
      if (version === requestVersion.current) setLoaded({ context, analysis: summary, result: null, historical: false });
    } catch (reason) {
      if (version === requestVersion.current) { setLoaded(null); setError(reason instanceof Error ? reason.message : 'Descriptive update failed.'); }
    } finally { if (version === requestVersion.current) { setBusy(false); setLoading(false); } }
  };
  return <><PageHeader eyebrow="SCIENTIFIC SYNTHESIS" title="Map evidence. Pool with care.">Descriptive summaries keep experimental conditions distinct. Inferential synthesis requires accepted source measurements and a registered estimand.</PageHeader>{error && <Notice kind="warning">{error}</Notice>}{analysis?.synthesis_stale === true && <Notice kind="warning">This synthesis is stale. Evidence or the research protocol has changed, or sources remain unprocessed. Recompute the synthesis before treating it as current.</Notice>}<section className="panel"><div className="section-heading"><div><div className="eyebrow">DESCRIPTIVE MAPPING</div><h2>Recorded outcome groups</h2></div><FlaskConical className="teal" size={25} /></div>{loading ? <Spinner label="Loading scientific summary" /> : analysis?.groups?.length ? <div className="table-scroll"><table><thead><tr><th>Outcome / unit</th><th>Method / statistic</th><th>Time / basis</th><th>Measurements</th><th>Range</th><th>Median</th></tr></thead><tbody>{analysis.groups.map((group, index) => <tr key={index}><td><strong>{humanize(group.outcome)}</strong><small>{group.unit ?? 'Unit not reported'}</small></td><td>{group.measurement_method ?? 'Not reported'}<small>{group.statistic ?? 'Statistic not reported'}</small></td><td>{group.time_value == null ? 'Time not reported' : `${number(group.time_value)} ${group.time_unit ?? ''}`}<small>{group.concentration_basis ?? 'Basis not reported'}</small></td><td>{group.n}</td><td>{number(group.value_min)} – {number(group.value_max)}</td><td>{number(group.median)}</td></tr>)}</tbody></table></div> : !error && <Empty title="No outcome groups have been returned">Extract and review experimental measurements first. Descriptive results will be computed from available evidence.</Empty>}{analysis?.warnings?.map((warning, index) => <Notice kind="warning" key={index}>{warning}</Notice>)}<JsonDetails value={analysis?.constraints} label="Comparability constraints" /><JsonDetails value={analysis?.excluded} label="Exclusions from descriptive summaries" /><div className="panel-footer"><small>The update records the current scientific summary and checks unresolved sources.</small><button className="button" disabled={!canEdit || busy} onClick={() => void updateMapping()}>Update descriptive synthesis</button></div></section><section className="panel"><div className="section-heading"><div><div className="eyebrow">INFERENTIAL ANALYSIS</div><h2>The registered estimand</h2></div><Sigma className="coral" size={26} /></div>{protocol.analysis_mode !== 'inferential' && <Notice kind="warning">Set the protocol to inferential analysis and complete the outcome definition, method, comparator, time point and unit before pooling.</Notice>}<div className="metadata-grid">{(['outcome', 'outcome_definition', 'measurement_method', 'comparator', 'time_point', 'concentration_basis', 'effect_measure'] as const).map(key => <div key={key}><span>{humanize(key)}</span><strong>{spec[key] || 'Not registered'}</strong></div>)}</div><Field label="Common source unit" hint="Choose source arms with this same unit; the server verifies their scientific context."><input value={protocol.unit ?? ''} readOnly /></Field><Notice>Choose accepted experimental mean measurements with reported SD and independent n ≥ 2. The server derives the arms from those source IDs and checks their experimental attributes. Technical repeats and curve points do not increase independent n. Shared study data require an explicit covariance model; this form submits independent contrasts.</Notice><div className="section-heading"><h3>Source-linked contrasts</h3><button className="button small" disabled={!canEdit} onClick={() => setContrasts(items => [...items, newContrast()])}><Plus size={15} />Add contrast</button></div>{!contrasts.length ? <p className="muted">No comparisons have been selected. {eligible.length} accepted measurements have a numeric value, SD and independent sample size available for source validation.</p> : <div className="contrast-list">{contrasts.map((row, index) => <div className="contrast-card" key={row.id}><div className="section-heading"><strong>Contrast {index + 1}</strong><button className="icon-button" aria-label={`Remove contrast ${index + 1}`} disabled={!canEdit} onClick={() => setContrasts(items => items.filter(item => item.id !== row.id))}><Trash2 size={16} /></button></div><div className="contrast-arms">{(['treatment', 'control'] as const).map(arm => { const id = row[`${arm}_id`]; const item = get(id); return <div key={arm}><Field label={`${humanize(arm)} source measurement`}><select aria-label={`Contrast ${index + 1} ${arm} measurement`} value={id} disabled={!canEdit} onChange={e => select(row.id, arm, e.target.value)}><option value="">Select accepted source evidence</option>{eligible.map(value => <option key={value.id} value={value.id}>{label(value)}</option>)}</select></Field>{item && <><div className="arm-values"><span>Mean <strong>{number(item.value)} {item.unit}</strong></span><span>SD <strong>{number(item.uncertainty)}</strong></span><span>Independent n <strong>{item.n_independent}</strong></span></div><small className="hash">{item.id}</small></>}</div>; })}</div></div>)}</div>}<div className="panel-footer"><small>Source means, SD and n remain read-only. Unsupported comparisons return a scientific validation error.</small><button className="button primary" disabled={!canEdit || !valid || busy} onClick={() => void synthesize()}><Sigma size={16} />{busy ? 'Checking & synthesizing…' : 'Validate and synthesize'}</button></div></section>{result && <section className="panel"><div className="section-heading"><h2>{historical ? "Historical inferential synthesis" : result.status === "completed" ? "Returned synthesis" : "Analysis response"}</h2><span className="count-pill">{historical ? "Historical" : spec.effect_measure}</span></div>{historical && <Notice kind="warning">Historical results are retained for review. They are not a current pooled analysis; validate the latest evidence and registered estimand before reusing these estimates.</Notice>}<JsonDetails value={result} label="Effect estimates, heterogeneity and uncertainty" /></section>}</>;
}
