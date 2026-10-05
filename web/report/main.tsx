import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import Plotly from 'plotly.js-dist-min';
import type { Data, Layout } from 'plotly.js';
import { BookOpen, CheckCheck, ChartScatter, Download, FileText, FlaskConical, FolderOpen, Layers3, LayoutDashboard, Menu, Search, Settings2, ShieldCheck, X } from 'lucide-react';
import { Badge, Empty, Field, JsonDetails, Modal, Notice, PageHeader } from '../src/components/UI';
import { useFocusBoundary } from '../src/components/useFocusBoundary';
import { comparableGroups, date, humanize, number } from '../src/utils';
import type { ReportEvidence as Evidence, ReportMeasurement as Measurement } from './core';
import { artifactHash, csvSnapshot, measurements, normalizeSnapshot, object, sha256, sourceLocation, stale, text } from './core';
import type { Snapshot } from './core';
import '../src/styles.css';
import './report.css';

type View = 'overview' | 'protocol' | 'evidence' | 'explorer' | 'synthesis' | 'benchmark' | 'publications' | 'provenance';
type LocalSource = { url: string; filename: string; kind: 'pdf' | 'image' | 'xml'; content?: string };
const views = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard }, { id: 'protocol', label: 'Review protocol', icon: Settings2 },
  { id: 'evidence', label: 'Evidence table', icon: CheckCheck }, { id: 'explorer', label: 'Outcome explorer', icon: ChartScatter },
  { id: 'synthesis', label: 'Synthesis snapshot', icon: FlaskConical }, { id: 'benchmark', label: 'Manual benchmark', icon: Layers3 },
  { id: 'publications', label: 'Literature snapshot', icon: BookOpen }, { id: 'provenance', label: 'Coverage & provenance', icon: FileText },
] as const;
const fileTypes = /\.(pdf|png|jpe?g|webp|n?xml)$/i;
function download(filename: string, data: string, type: string): void {
  const url = URL.createObjectURL(new Blob([data], { type }));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename; anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function ScientificChart({ data, layout, onPoint }: { data: Data[]; layout: Partial<Layout>; onPoint: (id: string) => void }) {
  const target = useRef<HTMLDivElement>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const element = target.current;
    if (!element) return;
    let disposed = false;
    let observer: ResizeObserver | undefined;
    setError('');
    void Plotly.newPlot(element, data, { paper_bgcolor: '#ffffff', plot_bgcolor: '#ffffff', height: 420,
      margin: { l: 68, r: 25, t: 30, b: 100 }, font: { family: 'system-ui, sans-serif', color: '#576574', size: 13 },
      ...layout, xaxis: { gridcolor: '#d8dee6', automargin: true, ...layout.xaxis }, yaxis: { gridcolor: '#d8dee6', automargin: true, ...layout.yaxis },
    }, { responsive: true, displaylogo: false, modeBarButtonsToRemove: ['lasso2d', 'select2d'], toImageButtonOptions: { format: 'png', filename: 'living-evidence', scale: 2 } }).then(chart => {
      if (disposed) { Plotly.purge(element); return; }
      chart.on('plotly_click', event => { const id = event.points[0]?.customdata; if (typeof id === 'string') onPoint(id); });
      if (typeof ResizeObserver !== 'undefined') { observer = new ResizeObserver(() => { void Plotly.Plots.resize(element); }); observer.observe(element); }
    }).catch(reason => { if (!disposed) setError(reason instanceof Error ? reason.message : 'The chart could not be rendered.'); });
    return () => { disposed = true; observer?.disconnect(); Plotly.purge(element); };
  }, [data, layout, onPoint]);
  return <>{error && <Notice kind="warning">{error}</Notice>}<div ref={target} className="plot-canvas" role="img" aria-label="Recorded experimental measurements; use the evidence table for accessible values" /></>;
}
function SourceInspector({ snapshot, measurement, sources, onClose }: { snapshot: Snapshot; measurement: Measurement; sources: Map<string, LocalSource>; onClose: () => void }) {
  const experiment = snapshot.experiments.find(item => item.id === measurement.experiment_id);
  const [index, setIndex] = useState(0);
  const evidence = measurement.evidence[index];
  const source = evidence && sources.get(evidence.document_hash.toLowerCase());
  const artifact = evidence?.artifact_key && (snapshot.artifacts.find(item => [item.artifact_key, item.relative_path, item.filename].includes(evidence.artifact_key ?? undefined))?.sha256 ?? artifactHash(evidence.artifact_key));
  const image = (artifact && sources.get(artifact.toLowerCase())) || (source?.kind === 'image' ? source : undefined);
  const evidenceBlock = (item: Evidence, key: number) => <div className="report-condition-source" key={key}><strong>{humanize(item.source_type)} · {sourceLocation(item)} · {item.locator || 'Locator not reported'}</strong>{item.excerpt && <blockquote>{item.excerpt}</blockquote>}<small className="hash">SHA-256 {item.document_hash}</small></div>;
  return <Modal title="Experimental source evidence" onClose={onClose}>
    <div className="measurement-hero"><div><span className="muted">{humanize(measurement.outcome)} · {experiment?.sample_label}</span><strong>{measurement.raw_value || number(measurement.value)} <small>{measurement.unit ?? ''}</small></strong></div><Badge value={measurement.status} /></div>
    <div className="metadata-grid">{[
      ['Study family', experiment?.study_family], ['DOI', experiment?.doi], ['Method', measurement.measurement_method], ['Statistic', measurement.statistic],
      ['Independent n', measurement.n_independent], ['Technical n', measurement.n_technical], ['Uncertainty', measurement.uncertainty_type === 'none' ? 'Not reported' : `${number(measurement.uncertainty)} ${measurement.uncertainty_type}`],
      ['CI bounds', measurement.ci_lower != null || measurement.ci_upper != null ? `${number(measurement.ci_lower)} to ${number(measurement.ci_upper)}` : undefined],
      ['Concentration basis', measurement.concentration_basis], ['Time point', measurement.time_value == null ? undefined : `${measurement.time_value} ${measurement.time_unit ?? '(unit not reported)'}`],
      ['Origin', humanize(measurement.origin)], ['Qualifier', humanize(measurement.qualifier)],
    ].map(([label, value]) => <div key={String(label)}><span>{text(label)}</span><strong>{text(value)}</strong></div>)}</div>
    {measurement.normalized_value != null && <Notice>Normalized value: {number(measurement.normalized_value)} {measurement.normalized_unit}. The original observation is retained above.</Notice>}
    {measurement.missing_reason && <Notice kind="warning">{measurement.missing_reason}</Notice>}
    {(measurement.validation_notes ?? []).map((note, key) => <Notice kind="warning" key={key}>{note}</Notice>)}
    {measurement.origin === 'curve_sample' && <Notice kind="warning">A sampled curve point is not an independent experimental replicate.</Notice>}
    <div className="source-tabs">{measurement.evidence.map((item, key) => <button key={key} className={index === key ? 'active' : ''} aria-pressed={index === key} onClick={() => setIndex(key)}>{humanize(item.source_type)} · {sourceLocation(item)}</button>)}</div>
    {evidence ? <section className="source-card">{evidenceBlock(evidence, index)}
      {source?.kind === 'pdf' && (evidence.source_format ?? 'pdf') === 'pdf' ? <><p><a className="text-link" href={`${source.url}#page=${evidence.page}`} target="_blank" rel="noreferrer">Open verified local PDF · p. {evidence.page}</a></p><iframe className="pdf-preview" src={`${source.url}#page=${evidence.page}`} title={`Verified source PDF page ${evidence.page}`} /></> : source?.kind === 'xml' ? <details className="json-details"><summary>Inspect verified local XML source</summary><pre>{source.content}</pre></details> : source?.kind !== 'image' && <Notice>The original source is not embedded. Select your local source folder above; only a matching SHA-256 file becomes available here.</Notice>}
      {image && <><p className="small">Verified local evidence image</p><img className="evidence-image" src={image.url} alt={`Source ${evidence.figure_id ?? evidence.locator}, panel ${evidence.panel ?? 'not reported'}`} /></>}
      <div className="source-facts">{evidence.figure_id && <span>Figure {evidence.figure_id}</span>}{evidence.panel && <span>Panel {evidence.panel}</span>}{evidence.series && <span>Series {evidence.series}</span>}{evidence.calibration && <span>Calibration: {evidence.calibration}</span>}{evidence.digitization_uncertainty != null && <span>Digitization uncertainty: {number(evidence.digitization_uncertainty)}</span>}</div>
      {evidence.source_type === 'microscopy' && !evidence.calibration && <Notice kind="warning">Spatial calibration is not recorded. This image does not establish an independent replicate count.</Notice>}
    </section> : <Notice kind="warning">Primary source locators are missing. The report does not mark this value as verified.</Notice>}
    {Boolean(experiment?.attributes?.length) && <section className="condition-evidence"><h3>Experimental conditions and design</h3>{experiment!.attributes.map((attribute, key) => <article className="source-card" key={key}><div className="source-heading"><strong>{humanize(attribute.name)}</strong><Badge value={attribute.status ?? 'candidate'} /></div><p>{attribute.text || `${number(attribute.value)} ${attribute.unit ?? ''}`}</p>{attribute.basis && <p className="small">Basis: {attribute.basis}</p>}{attribute.evidence?.length ? attribute.evidence.map(evidenceBlock) : <Notice kind="warning">Condition source evidence is not recorded.</Notice>}</article>)}</section>}
    <Notice>This is a read-only snapshot. Review decisions are made in the local extraction workflow and appear after you regenerate the report.</Notice>
  </Modal>;
}
function EvidenceTable({ snapshot, values, onSelect }: { snapshot: Snapshot; values: Measurement[]; onSelect: (measurement: Measurement) => void }) {
  const [query, setQuery] = useState(''); const [status, setStatus] = useState('all'); const [outcome, setOutcome] = useState('all'); const [origin, setOrigin] = useState('all');
  const [order, setOrder] = useState('sample'); const [reverse, setReverse] = useState(false); const [page, setPage] = useState(0);
  const map = useMemo(() => new Map(snapshot.experiments.map(item => [item.id, item])), [snapshot]);
  const rows = useMemo(() => values.filter(item => {
    const experiment = map.get(item.experiment_id);
    const haystack = [item.outcome, item.raw_value, item.unit, item.measurement_method, experiment?.sample_label, experiment?.doi, ...item.evidence.map(source => source.excerpt)].filter(Boolean).join(' ').toLowerCase();
    return haystack.includes(query.toLowerCase()) && (status === 'all' || status === item.status) && (outcome === 'all' || outcome === item.outcome) && (origin === 'all' || origin === item.origin);
  }).sort((left, right) => {
    const a = order === 'sample' ? map.get(left.experiment_id)?.sample_label ?? '' : order === 'value' ? left.value : left[order as 'outcome' | 'status'];
    const b = order === 'sample' ? map.get(right.experiment_id)?.sample_label ?? '' : order === 'value' ? right.value : right[order as 'outcome' | 'status'];
    const comparison = a == null ? b == null ? 0 : 1 : b == null ? -1 : typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b));
    return (reverse ? -1 : 1) * comparison;
  }), [values, map, query, status, outcome, origin, order, reverse]);
  useEffect(() => { setPage(0); }, [query, status, outcome, origin, order, reverse]);
  const current = Math.min(page, Math.max(0, Math.ceil(rows.length / 30) - 1));
  return <><PageHeader eyebrow="RECORDED OBSERVATIONS" title="Evidence table">Original values, uncertainties, conditions and source locations remain distinct.</PageHeader><section className="panel table-panel"><div className="table-toolbar"><div className="search-input"><Search size={17} /><input aria-label="Search evidence" placeholder="Sample, DOI, outcome, method or source text" value={query} onChange={event => setQuery(event.target.value)} /></div>
    <select aria-label="Filter evidence status" value={status} onChange={event => setStatus(event.target.value)}><option value="all">All decisions</option>{['accepted', 'candidate', 'uncertain', 'rejected', 'stale'].map(item => <option key={item}>{item}</option>)}</select>
    <select aria-label="Filter outcome" value={outcome} onChange={event => setOutcome(event.target.value)}><option value="all">All outcomes</option>{[...new Set(values.map(item => item.outcome))].sort().map(item => <option key={item}>{item}</option>)}</select>
    <select aria-label="Filter origin" value={origin} onChange={event => setOrigin(event.target.value)}><option value="all">All origins</option>{[...new Set(values.map(item => item.origin))].sort().map(item => <option key={item}>{item}</option>)}</select>
    <select aria-label="Sort evidence" value={order} onChange={event => setOrder(event.target.value)}><option value="sample">Sort: sample</option><option value="outcome">Sort: outcome</option><option value="value">Sort: numeric value</option><option value="status">Sort: decision</option></select><button className="button small" onClick={() => setReverse(value => !value)} aria-pressed={reverse}>{reverse ? 'Descending' : 'Ascending'}</button>
    </div>{rows.length ? <div className="table-scroll"><table><thead><tr><th>Sample / study</th><th>Outcome / definition</th><th>Original value</th><th>Uncertainty / n</th><th>Source</th><th>Decision</th><th>Inspect</th></tr></thead><tbody>{rows.slice(current * 30, current * 30 + 30).map(item => <tr key={item.id}><td><strong>{map.get(item.experiment_id)?.sample_label}</strong><small>{map.get(item.experiment_id)?.doi ?? map.get(item.experiment_id)?.study_family}</small></td><td>{humanize(item.outcome)}<small>{item.measurement_method ?? 'Method not reported'} · {item.statistic ?? 'Statistic not reported'}</small></td><td><strong>{item.raw_value || number(item.value)} {item.unit}</strong><small>{humanize(item.qualifier)} · {humanize(item.origin)}</small></td><td>{item.uncertainty_type === 'none' ? 'Not reported' : `${number(item.uncertainty)} ${item.uncertainty_type}`}<small>Independent n: {number(item.n_independent)}<br />Technical n: {number(item.n_technical)}</small></td><td>{item.evidence.map(sourceLocation).join(', ') || 'Locator missing'}</td><td><Badge value={item.status} /></td><td><button className="button small" onClick={() => onSelect(item)}>View source</button></td></tr>)}</tbody></table></div> : <Empty title={values.length ? 'No observations match these filters' : 'No extraction data in this snapshot'}>Missing observations are not fabricated. Run the local extraction workflow and regenerate the report.</Empty>}
    <div className="table-footer"><span>{rows.length ? `${current * 30 + 1}–${Math.min(current * 30 + 30, rows.length)} of ${rows.length}` : '0 measurements'}</span><div><button className="button small" disabled={current === 0} onClick={() => setPage(current - 1)}>Previous</button><button className="button small" disabled={(current + 1) * 30 >= rows.length} onClick={() => setPage(current + 1)}>Next</button></div></div></section></>;
}
function Explorer({ snapshot, values, onSelect }: { snapshot: Snapshot; values: Measurement[]; onSelect: (measurement: Measurement) => void }) {
  const [candidates, setCandidates] = useState(false); const [key, setKey] = useState(''); const [histogram, setHistogram] = useState(false);
  const groups = useMemo(() => comparableGroups(values.map(item => ({ ...item, evidence: [] })), candidates), [values, candidates]);
  const group = groups.find(item => item.key === key) ?? groups[0];
  const experimentMap = useMemo(() => new Map(snapshot.experiments.map(item => [item.id, item])), [snapshot]);
  const traces = useMemo<Data[]>(() => group ? histogram ? [{ type: 'histogram', x: group.values, marker: { color: '#0969da' } }] : ['reported', 'digitized', 'derived'].map((origin, index) => {
    const items = group.measurements.map((item, i) => ({ item, value: group.values[i] })).filter(entry => entry.item.origin === origin);
    return { type: 'scatter', mode: 'markers', name: humanize(origin), x: items.map(({ item }) => experimentMap.get(item.experiment_id)?.sample_label ?? item.experiment_id), y: items.map(entry => entry.value), customdata: items.map(entry => entry.item.id), marker: { color: ['#0969da', '#bf5b04', '#8250df'][index], size: 10 }, hovertemplate: '%{x}<br>%{y}<extra>%{fullData.name}</extra>' } as Data;
  }).filter(trace => (trace as { x: unknown[] }).x.length) : [], [group, histogram, experimentMap]);
  const layout = useMemo<Partial<Layout>>(() => ({ xaxis: { title: { text: histogram ? `${group?.outcome ?? ''} (${group?.unit ?? ''})` : 'Experimental sample' }, type: histogram ? 'linear' : 'category' }, yaxis: { title: { text: histogram ? 'Recorded measurements' : `${group?.outcome ?? ''} (${group?.unit ?? ''})` } }, legend: { orientation: 'h', y: 1.15 } }), [group, histogram]);
  const point = React.useCallback((id: string) => { const item = values.find(item => item.id === id); if (item) onSelect(item); }, [values, onSelect]);
  return <><PageHeader eyebrow="SCIENTIFIC COMPARABILITY" title="Outcome explorer">Groups preserve outcome, unit, method, statistic, time point and concentration basis.</PageHeader><section className="panel"><Field label="Comparability group"><select value={group?.key ?? ''} disabled={!groups.length} onChange={event => setKey(event.target.value)}>{groups.length ? groups.map(item => <option key={item.key} value={item.key}>{humanize(item.outcome)} · {item.unit} · {item.method} · {item.statistic} · {item.time} · {item.basis} ({item.values.length})</option>) : <option>No comparable data</option>}</select></Field><label className="checkbox-label"><input type="checkbox" checked={candidates} onChange={event => setCandidates(event.target.checked)} />Include unreviewed candidates</label></section>{candidates && <Notice kind="warning">Candidate measurements are unreviewed. Their display does not constitute validated inference.</Notice>}<Notice>Unknown units, methods or statistics, non-exact values and sampled curve points are excluded. Counts represent observations, not independent studies.</Notice>{group ? <section className="panel"><div className="section-heading"><div><h2>{humanize(group.outcome)}</h2><p className="section-copy">{group.values.length} measurements · {group.method} · {group.statistic}</p></div><div className="segmented"><button className={!histogram ? 'active' : ''} aria-pressed={!histogram} onClick={() => setHistogram(false)}>Samples</button><button className={histogram ? 'active' : ''} aria-pressed={histogram} onClick={() => setHistogram(true)}>Distribution</button></div></div><ScientificChart data={traces} layout={layout} onPoint={point} /><p className="chart-caption">Select a sample marker to inspect its primary source. The evidence table provides accessible values and locators.</p></section> : <Empty title="Source-supported comparable observations are needed">Recorded values require a unit, method and statistic before entering these charts.</Empty>}</>;
}

export function ReportApp({ initialSnapshot }: { initialSnapshot: Snapshot }) {
  const [snapshot, setSnapshot] = useState(initialSnapshot); const [view, setView] = useState<View>('overview'); const [selected, setSelected] = useState<Measurement | null>(null);
  const [sources, setSources] = useState(new Map<string, LocalSource>()); const urls = useRef(new Set<string>());
  const [error, setError] = useState(''); const [info, setInfo] = useState(''); const [busy, setBusy] = useState(false);
  const [mobile, setMobile] = useState(() => window.matchMedia?.('(max-width: 760px)').matches ?? false); const [menu, setMenu] = useState(false);
  const sidebar = useRef<HTMLElement>(null); const datasetInput = useRef<HTMLInputElement>(null); const sourceInput = useRef<HTMLInputElement>(null); const folderInput = useRef<HTMLInputElement>(null);
  useFocusBoundary(sidebar, { active: mobile && menu, onClose: () => setMenu(false) });
  useEffect(() => { const media = window.matchMedia?.('(max-width: 760px)'); if (!media) return; const listener = () => { setMobile(media.matches); if (!media.matches) setMenu(false); }; media.addEventListener('change', listener); return () => media.removeEventListener('change', listener); }, []);
  useEffect(() => () => { urls.current.forEach(url => URL.revokeObjectURL(url)); }, []);
  const values = useMemo(() => measurements(snapshot), [snapshot]);
  const allHashes = useMemo(() => new Set([...snapshot.documents.map(item => item.sha256), ...snapshot.artifacts.map(item => item.sha256), ...values.flatMap(item => item.evidence.flatMap(source => [source.document_hash, artifactHash(source.artifact_key)]).filter(Boolean))].filter(item => typeof item === 'string').map(item => item!.toLowerCase())), [snapshot, values]);
  const navigate = (next: View) => { setView(next); setMenu(false); setError(''); };
  const select = React.useCallback((item: Measurement) => setSelected(item), []);
  const loadDataset = async (file?: File) => {
    if (!file) return; setError(''); setBusy(true);
    try {
      if (file.size > 50 * 1024 * 1024) throw new Error('Dataset JSON exceeds the 50 MB reader limit.');
      const next = normalizeSnapshot(JSON.parse(await file.text()));
      urls.current.forEach(url => URL.revokeObjectURL(url)); urls.current.clear(); setSources(new Map()); setSelected(null); setSnapshot(next); setInfo('Local dataset loaded. Snapshot dates and review decisions were preserved.');
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'The dataset could not be loaded.'); }
    finally { setBusy(false); if (datasetInput.current) datasetInput.current.value = ''; }
  };
  const register = (hash: string, file: Blob, filename: string, kind: LocalSource['kind'], content?: string) => {
    const url = URL.createObjectURL(file); urls.current.add(url); setSources(current => { const next = new Map(current); const old = next.get(hash); if (old) { URL.revokeObjectURL(old.url); urls.current.delete(old.url); } next.set(hash, { url, filename, kind, content }); return next; });
  };
  const loadSources = async (list: FileList | null) => {
    if (!list) return; setBusy(true); setError(''); let matched = 0; let skipped = 0;
    try {
      for (const file of [...list]) {
        if (!fileTypes.test(file.name) || file.size > 100 * 1024 * 1024) { skipped++; continue; }
        const bytes = await file.arrayBuffer(); const hash = await sha256(bytes);
        if (!allHashes.has(hash)) { skipped++; continue; }
        const head = new Uint8Array(bytes.slice(0, 12)); const pdf = /^%PDF-/.test(new TextDecoder().decode(head));
        const image = (head[0] === 0x89 && head[1] === 0x50 && head[2] === 0x4e && head[3] === 0x47) || (head[0] === 0xff && head[1] === 0xd8) || (new TextDecoder().decode(head.slice(0, 4)) === 'RIFF' && new TextDecoder().decode(head.slice(8)) === 'WEBP');
        const xml = /\.n?xml$/i.test(file.name);
        if (!pdf && !image && !xml) { skipped++; continue; }
        const xmlText = xml ? new TextDecoder().decode(bytes) : undefined;
        register(hash, xml ? new Blob([xmlText!], { type: 'text/plain' }) : file, file.name, pdf ? 'pdf' : xml ? 'xml' : 'image', xmlText); matched++;
      }
      setInfo(`${matched} local source file${matched === 1 ? '' : 's'} matched by SHA-256. ${skipped} unsupported or unmatched file${skipped === 1 ? '' : 's'} skipped. No files were transmitted.`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Local source verification failed.'); }
    finally { setBusy(false); if (sourceInput.current) sourceInput.current.value = ''; if (folderInput.current) folderInput.current.value = ''; }
  };
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      for (const artifact of snapshot.embedded_artifacts) {
        if (!['image/png', 'image/jpeg', 'image/webp'].includes(artifact.mime_type) || artifact.base64.length > 36 * 1024 * 1024) continue;
        try {
          const binary = atob(artifact.base64); const bytes = Uint8Array.from(binary, value => value.charCodeAt(0));
          const digest = await sha256(bytes.buffer); if (!cancelled && digest === artifact.sha256.toLowerCase()) register(digest, new Blob([bytes], { type: artifact.mime_type }), 'Embedded evidence image', 'image');
        } catch { if (!cancelled) setError('An embedded image failed local integrity validation and was not displayed.'); }
      }
    })();
    return () => { cancelled = true; };
  }, [snapshot]);
  const pending = snapshot.publications.filter(item => item.state === 'pending_extraction').length;
  const protocol = Object.keys(snapshot.protocol).length ? snapshot.protocol : object(snapshot.project.protocol);
  const synthesisStale = stale(snapshot); const extractionDate = snapshot.freshness.last_extraction;
  const name = text(snapshot.project.name, 'Living evidence review');
  const synopsis = <><div className="stat-grid">{[
    ['Source documents', snapshot.documents.length, `${snapshot.documents.reduce((sum, item) => sum + (item.pages ?? 0), 0)} indexed pages`],
    ['Experimental observations', values.length, `${snapshot.experiments.length} experimental records`],
    ['Accepted evidence', values.filter(item => item.status === 'accepted').length, `${values.filter(item => ['candidate', 'uncertain', 'stale'].includes(item.status)).length} require review`],
    ['Pending literature', pending, 'Snapshot count; no automatic monitoring'],
  ].map(([label, count, detail]) => <div className="stat-card" key={String(label)}><span>{text(label)}</span><strong>{count}</strong><small>{detail}</small></div>)}</div><div className="two-column"><section className="panel"><h2>Evidence freshness</h2>{[['Last metadata check', snapshot.freshness.last_metadata_check], ['Last extraction', extractionDate], ['Last synthesis update', snapshot.freshness.last_synthesis_update]].map(([label, value]) => <div className="schedule-row" key={String(label)}><span>{text(label)}</span><strong>{date(typeof value === 'string' ? value : null)}</strong></div>)}<Notice kind={synthesisStale ? 'warning' : 'info'}>{!extractionDate ? 'No extraction has been recorded. Source inventory is not extracted experimental data.' : synthesisStale ? 'The synthesis has unresolved or unprocessed evidence and must not be labelled current.' : 'Dates describe the exported snapshot. Opening this file does not check for new literature.'}</Notice></section><section className="panel"><h2>Local source evidence</h2><p className="section-copy">Original PDFs, XML and figure files stay in your bibliography folder. Select them locally to inspect matching evidence pages.</p><p className="report-source-count">{sources.size} source files available in this browser session</p><button className="button" disabled={busy} onClick={() => folderInput.current?.click()}><FolderOpen size={16} />Select source folder</button><p className="small muted">Verification uses SHA-256; filenames alone do not establish a match.</p></section></div><section className="panel table-panel"><div className="section-heading"><h2>Bibliography snapshot</h2><span className="count-pill">{snapshot.documents.length}</span></div>{snapshot.documents.length ? <div className="table-scroll"><table><thead><tr><th>Document</th><th>DOI</th><th>Pages</th><th>Status</th><th>Local file</th></tr></thead><tbody>{snapshot.documents.slice(0, 20).map(item => <tr key={item.id}><td><strong>{item.filename}</strong><small className="hash">{item.sha256}</small></td><td>{item.doi ?? 'Not linked'}</td><td>{item.pages ?? 'Not indexed'}</td><td><Badge value={item.status} /></td><td>{sources.get(item.sha256.toLowerCase())?.kind === 'pdf' ? <a className="text-link" href={sources.get(item.sha256.toLowerCase())!.url} target="_blank" rel="noreferrer">Verified PDF</a> : sources.get(item.sha256.toLowerCase()) ? `Verified ${sources.get(item.sha256.toLowerCase())!.kind === 'xml' ? 'XML' : 'image'}` : 'Not selected'}</td></tr>)}</tbody></table></div> : <Empty title="No bibliography in this snapshot">The local workflow can inventory your original PDF folder before extraction.</Empty>}</section></>;
  const analysisStatus = text(snapshot.synthesis.status, 'not_run');
  const benchmarkStatus = text(snapshot.benchmark.status, Object.keys(snapshot.benchmark).length ? 'agreement_only' : 'not_run');
  return <div className="app-shell report-app"><a className="report-skip" href="#report-main">Skip to report content</a><aside ref={sidebar} tabIndex={-1} inert={mobile && !menu ? true : undefined} className={`sidebar ${menu ? 'is-open' : ''}`} aria-label="Report navigation" aria-hidden={mobile && !menu ? true : undefined}><div className="sidebar-top"><div className="brand"><span className="brand-mark"><FlaskConical size={21} /></span><span>Living evidence<span className="brand-second">Offline research report</span></span></div><button className="icon-button mobile-only" aria-label="Close navigation" onClick={() => setMenu(false)}><X size={20} /></button></div><div className="project-switcher"><div className="eyebrow">LOCAL SNAPSHOT</div><strong>{name}</strong></div><nav>{views.map(item => <button key={item.id} className={`nav-link ${view === item.id ? 'active' : ''}`} aria-current={view === item.id ? 'page' : undefined} onClick={() => navigate(item.id)}><item.icon size={17} />{item.label}</button>)}</nav><div className="sidebar-bottom"><div className="private-status"><ShieldCheck size={15} />Read-only · No network services</div><p className="small muted">Files remain on this computer. Dataset exports may contain private source excerpts.</p></div></aside>{mobile && menu && <div className="nav-backdrop" onClick={() => setMenu(false)} />}<div className="main-shell"><header className="topbar"><div className="topbar-left"><button className="icon-button mobile-only" aria-label="Open navigation" aria-expanded={menu} onClick={() => setMenu(true)}><Menu size={22} /></button><span className="breadcrumb">Local report <span>/</span> {views.find(item => item.id === view)?.label}</span></div><div className="topbar-actions"><button className="button small" disabled={busy} onClick={() => datasetInput.current?.click()}>Load JSON</button><button className="button small" disabled={busy} onClick={() => sourceInput.current?.click()}>Select local files</button><details className="export-menu"><summary className="button small"><Download size={14} />Export</summary><div><button onClick={() => download('dataset.json', JSON.stringify(snapshot, null, 2), 'application/json')}>Dataset JSON</button><button onClick={() => download('measurements.csv', csvSnapshot(snapshot), 'text/csv;charset=utf-8')}>Measurements CSV</button></div></details></div></header><main id="report-main" tabIndex={-1} className="content">
    <input ref={datasetInput} type="file" hidden accept=".json,application/json" onChange={event => void loadDataset(event.target.files?.[0])} aria-label="Load offline dataset JSON" />
    <input ref={sourceInput} type="file" hidden multiple accept=".pdf,.xml,.nxml,.png,.jpg,.jpeg,.webp" onChange={event => void loadSources(event.target.files)} aria-label="Select local source PDFs, XML and images" />
    <input ref={folderInput} type="file" hidden multiple {...{ webkitdirectory: '', directory: '' }} onChange={event => void loadSources(event.target.files)} aria-label="Select local bibliography folder" />
    {busy && <Notice>Reading and verifying local files…</Notice>}{error && <Notice kind="warning">{error}</Notice>}{info && <Notice kind="success">{info}</Notice>}
    {view === 'overview' && <><PageHeader eyebrow="OFFLINE RESEARCH SNAPSHOT" title={name}>{text(protocol.topic, 'Source-preserving experimental evidence and synthesis.')}</PageHeader><div className="project-summary"><span>Schema version 2</span><span>{text(protocol.name, 'Protocol not registered')}</span><span className="workspace-state">{!extractionDate ? 'Awaiting extraction' : synthesisStale ? 'Review required' : 'Snapshot exported'}</span></div>{synopsis}</>}
    {view === 'protocol' && <><PageHeader eyebrow="REGISTERED SCIENTIFIC QUESTION" title="Review protocol">The reader preserves the protocol and does not edit experimental decisions.</PageHeader><section className="panel"><h2>{text(protocol.name, 'No registered protocol')}</h2><p>{text(protocol.topic)}</p>{['inclusion', 'exclusion', 'variables'].map(key => <div className="report-protocol-section" key={key}><h3>{humanize(key)}</h3>{Array.isArray(protocol[key]) ? <ul>{(protocol[key] as unknown[]).map((item, i) => <li key={i}>{text(item)}</li>)}</ul> : <p className="muted">Not registered</p>}</div>)}<JsonDetails value={protocol} label="Complete protocol and analysis constraints" /></section></>}
    {view === 'evidence' && <EvidenceTable snapshot={snapshot} values={values} onSelect={select} />}
    {view === 'explorer' && <Explorer snapshot={snapshot} values={values} onSelect={select} />}
    {view === 'synthesis' && <><PageHeader eyebrow="PRECOMPUTED ANALYSIS" title="Synthesis snapshot">Statistical results are calculated by the local scientific engine and displayed here without rerunning or substituting estimators.</PageHeader>{(!extractionDate || synthesisStale) && <Notice kind="warning">This synthesis is historical or incomplete. Unprocessed, uncertain or stale evidence prevents a claim that it is current.</Notice>}<section className="panel"><div className="section-heading"><h2>Recorded analysis</h2><Badge value={analysisStatus} /></div>{analysisStatus === 'not_run' && <Notice>No synthesis has been executed. Missing SD or independent sample size remains an explicit limitation.</Notice>}<JsonDetails value={snapshot.synthesis} label="Estimates, uncertainty, engine versions and limitations" /><Notice>Inferential results require compatible protocol-defined outcomes, comparators, methods and time points, valid sampling variances and appropriate dependence handling.</Notice></section></>}
    {view === 'benchmark' && <><PageHeader eyebrow="MANUAL REFERENCE COMPARISON" title="Benchmark snapshot">Agreement with manual extraction is distinct from independently adjudicated accuracy.</PageHeader>{benchmarkStatus === 'not_run' && <Notice>Reference scope is available. No extraction comparison has been run; accuracy metrics are unavailable.</Notice>}<section className="panel"><Badge value={benchmarkStatus} /><JsonDetails value={snapshot.benchmark} label="Reference scope, agreement, ambiguity and adjudication" /><Notice>Precision and recoverable-data recall require primary-source adjudication. Validation targets are not achieved results.</Notice></section></>}
    {view === 'publications' && <><PageHeader eyebrow="LITERATURE AT EXPORT" title="Literature snapshot">No automatic searches run in this file. Update the local workflow and regenerate the report to incorporate new evidence.</PageHeader><Notice>Last metadata check: {date(typeof snapshot.freshness.last_metadata_check === 'string' ? snapshot.freshness.last_metadata_check : null)}. Pending publications remain unprocessed.</Notice><section className="panel">{snapshot.publications.length ? snapshot.publications.map((item, i) => <article className="publication-card" key={item.id ?? i}><div className="publication-badges"><Badge value={item.status ?? 'unknown'} /><Badge value={item.state ?? 'pending_extraction'} /></div><h3>{item.title}</h3><p className="small muted">{(item.authors ?? []).join(', ')} {item.year ?? ''}</p><p className="doi">{item.doi ?? 'DOI not reported'}</p>{item.abstract && <details className="publication-abstract"><summary>Read abstract</summary><p>{item.abstract}</p></details>}</article>) : <Empty title="No literature metadata in this snapshot">This file does not imply that the bibliography has been checked recently.</Empty>}</section></>}
    {view === 'provenance' && <><PageHeader eyebrow="REPRODUCIBILITY" title="Coverage and provenance">Document hashes, extraction status, page coverage, transformations, agent records and checkpoints remain inspectable.</PageHeader><section className="panel"><JsonDetails value={snapshot.coverage} label="Page, table and figure coverage" /><JsonDetails value={snapshot.run} label="Local extraction run records" /><JsonDetails value={snapshot.provenance} label="Software, agents, prompts, transformations and provenance" /><JsonDetails value={snapshot.artifacts} label="Private evidence artifact hash manifest" /></section></>}
    <footer className="content-footer"><span>Living Meta-Analysis · Offline reader</span><span>Read-only snapshot · Source evidence stays local</span></footer></main></div>{selected && <SourceInspector snapshot={snapshot} measurement={selected} sources={sources} onClose={() => setSelected(null)} />}</div>;
}

const mount = document.getElementById('root');
const embedded = document.getElementById('report-data');
if (mount && embedded) {
  try { createRoot(mount).render(<ReportApp initialSnapshot={normalizeSnapshot(JSON.parse(embedded.textContent ?? '{}'))} />); }
  catch (reason) { const message = document.createElement('p'); message.className = 'report-load-error'; message.textContent = reason instanceof Error ? reason.message : 'The offline report dataset is invalid.'; mount.replaceChildren(message); }
}
