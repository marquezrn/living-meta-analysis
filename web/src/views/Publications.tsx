import { BookOpen, Download, ExternalLink, RefreshCw, Search } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api, documentUrl } from '../api';
import type { AccessResolution, Document, Project, Publication } from '../types';
import { date, humanize, safeExternalUrl } from '../utils';
import { Badge, Empty, Field, Modal, Notice, PageHeader, Spinner } from '../components/UI';

type Acquire = (publicationId: string, version?: string) => Promise<Document>;
const versionLabel = (version: string) => ({ publishedVersion: 'Published version', acceptedVersion: 'Accepted manuscript', submittedVersion: 'Submitted manuscript' }[version] ?? version);

export function AccessDialog({ publication, canAcquire, onAcquire, onClose }: {
  publication: Publication; canAcquire: boolean; onAcquire: Acquire; onClose: () => void;
}) {
  const [resolution, setResolution] = useState<AccessResolution | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [version, setVersion] = useState('');
  const [acquiring, setAcquiring] = useState(false);
  const [document, setDocument] = useState<Document | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let current = true;
    setLoading(true); setError(''); setResolution(null); setDocument(null); setVersion('');
    void api.accessLocations(publication.id).then(result => {
      if (!current) return;
      setResolution(result);
      const versions = [...new Set(result.locations.filter(item => item.kind === 'pdf').map(item => item.version ?? ''))];
      setVersion(versions.includes('publishedVersion') ? 'publishedVersion' : versions.length === 1 ? versions[0] : '');
    }).catch(reason => {
      if (current) setError(reason instanceof Error ? reason.message : 'PDF access locations could not be resolved.');
    }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [publication.id, retry]);
  const pdfs = resolution?.locations.filter(item => item.kind === 'pdf') ?? [];
  const versions = [...new Set(pdfs.map(item => item.version ?? ''))];
  const explicitVersionNeeded = versions.length > 1 && !version;
  const acquire = async () => {
    setAcquiring(true); setError('');
    try { setDocument(await onAcquire(publication.id, version || undefined)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'The PDF could not be added. Upload an authorized source copy.'); }
    finally { setAcquiring(false); }
  };
  return <Modal title="Inspect open PDF access" onClose={() => { if (!acquiring) onClose(); }}>
    <p className="muted">{publication.title}</p>
    <Notice>Adding a permitted PDF stores it in this private bibliography. Extraction remains pending until the owner starts a run; this action makes no OpenAI calls.</Notice>
    {loading && <Spinner label="Resolving source locations" />}
    {error && <Notice kind="warning">{error}</Notice>}
    {!loading && !resolution && <button className="button" onClick={() => setRetry(value => value + 1)}>Retry access check</button>}
    {resolution && <>
      {resolution.limitations.length > 0 && <Notice kind="warning">{resolution.limitations.map((item, index) => <p key={index}>{item}</p>)}</Notice>}
      {resolution.publication_status !== 'active' && <Notice kind="warning">The publication is flagged as {humanize(resolution.publication_status)}. Scientific reassessment is required.</Notice>}
      {pdfs.length > 0 && <Field label="PDF version" hint="Choose the scientific version you intend to review. The server verifies access and the PDF before saving it.">
        <select aria-label="PDF version" value={version} disabled={acquiring || Boolean(document)} onChange={event => setVersion(event.target.value)}>
          {versions.length > 1 && <option value="">Select a reported version</option>}
          {versions.filter(item => item || versions.length === 1).map(item => <option key={item || 'unspecified'} value={item}>{item ? versionLabel(item) : 'Version not reported'}</option>)}
        </select>
      </Field>}
      {resolution.locations.length ? <div className="access-locations">{resolution.locations.map((location, index) => {
        const url = safeExternalUrl(location.url);
        return <article className="source-card" key={`${location.url}:${index}`}>
          <div className="source-heading"><strong>{humanize(location.kind)} · {humanize(location.provider)}</strong>{url && <a href={url} className="text-link" target="_blank" rel="noreferrer">Inspect source<ExternalLink size={13} /></a>}</div>
          <div className="source-facts"><span>{location.version ? versionLabel(location.version) : 'Version not reported'}</span><span>License: {location.license || 'Not reported'}</span>{location.manuscript && <span>Author manuscript</span>}</div>
        </article>;
      })}</div> : <Empty title="No open PDF location was found">Upload an authorized source copy through Bibliography &amp; runs. An unavailable PDF does not exclude a study.</Empty>}
      {!canAcquire && <Notice>Your role allows inspecting access locations. The owner or an editor can add a PDF to the private bibliography.</Notice>}
      {explicitVersionNeeded && <Notice kind="warning">Multiple PDF versions are available. Select a reported version explicitly, or upload an authorized copy if the required version is unspecified.</Notice>}
      {document ? <Notice kind="success"><strong>PDF added to the private bibliography.</strong><p>Extraction has not started.</p><a className="text-link" href={documentUrl(document.id)} target="_blank" rel="noreferrer">Open stored PDF<ExternalLink size={13} /></a></Notice> : canAcquire && pdfs.length > 0 && <button className="button primary full" disabled={acquiring || explicitVersionNeeded || resolution.publication_status !== 'active'} onClick={() => void acquire()}><Download size={16} />{acquiring ? 'Adding permitted PDF…' : 'Add PDF to bibliography'}</button>}
    </>}
  </Modal>;
}

export default function PublicationsView({ project, publications, canDiscover, canAcquire, busy, onDiscover, onAcquire }: {
  project: Project; publications: Publication[]; canDiscover: boolean; canAcquire: boolean;
  busy: boolean; onDiscover: () => void; onAcquire: Acquire;
}) {
  const [query, setQuery] = useState('');
  const [pendingOnly, setPendingOnly] = useState(true);
  const [selected, setSelected] = useState<Publication | null>(null);
  const filtered = useMemo(() => publications.filter(item => (!pendingOnly || item.state === 'pending_extraction') && [item.title, item.doi, item.source, ...(item.authors ?? [])].filter(Boolean).join(' ').toLowerCase().includes(query.toLowerCase())), [publications, query, pendingOnly]);
  return <>
    <PageHeader eyebrow="LIVING LITERATURE" title="New publications, ready for review" action={<button className="button primary" onClick={onDiscover} disabled={!canDiscover || busy}><RefreshCw size={16} className={busy ? 'spin' : ''} />{busy ? 'Checking sources…' : 'Check literature now'}</button>}>Discovery runs weekly. New citations enter the pending list; extraction starts only when the owner requests it.</PageHeader>
    <div className="discovery-banner"><BookOpen size={24} /><div><strong>Last metadata check</strong><span>{date(project.last_metadata_check)}</span></div><div><strong>Pending extraction</strong><span>{publications.filter(item => item.state === 'pending_extraction').length} publications</span></div></div>
    <Notice>Database indexing can lag publication. Search queries, source status and dates are preserved. Corrections, retractions and preprints require eligibility review before synthesis.</Notice>
    <section className="panel">
      <div className="table-toolbar"><div className="search-input"><Search size={17} /><input value={query} onChange={e => setQuery(e.target.value)} aria-label="Search publications" placeholder="Search title, author, DOI or source…" /></div><label className="checkbox-label"><input type="checkbox" checked={pendingOnly} onChange={e => setPendingOnly(e.target.checked)} />Pending extraction only</label><span className="count-pill">{filtered.length}</span></div>
      {!filtered.length ? <Empty icon={<BookOpen size={28} />} title={publications.length ? 'No publications match this view' : 'No discovered citations yet'}>{publications.length ? 'Change the pending filter or search to inspect the full citation list.' : 'Save your source-specific queries and run a literature check. Newly discovered records will appear here.'}</Empty> : <div className="publication-list">{filtered.map(item => {
        const link = safeExternalUrl(item.doi ? `https://doi.org/${item.doi}` : item.url);
        const fullText = safeExternalUrl(item.full_text_url);
        return <article className="publication-card" key={item.id || `${item.source}:${item.source_id}`}>
          <div className="publication-badges"><Badge value={item.status} /><span className="source-kind">{item.source}</span>{item.is_preprint && <Badge value="preprint" />}{item.state === 'pending_extraction' && <Badge value="pending_extraction" />}</div>
          <h3>{link ? <a href={link} target="_blank" rel="noreferrer">{item.title}<ExternalLink size={14} /></a> : item.title}</h3>
          <p className="small muted">{item.authors?.slice(0, 5).join(', ') || 'Authors not available'}{(item.authors?.length ?? 0) > 5 ? ', et al.' : ''}{item.year ? ` · ${item.year}` : ''}</p>
          {item.doi && <p className="doi">{item.doi}</p>}
          {item.abstract && <details className="publication-abstract"><summary>Read abstract</summary><p>{item.abstract}</p></details>}
          <div className="publication-footer"><span>Discovered {date(item.discovered_at)}</span>{item.license && <span>{item.license}</span>}{fullText && <a className="text-link" href={fullText} target="_blank" rel="noreferrer">Full-text location<ExternalLink size={13} /></a>}<button className="button small" disabled={busy || item.status !== 'active'} onClick={() => setSelected(item)}>Inspect PDF access<ExternalLink size={13} /></button></div>
        </article>;
      })}</div>}
    </section>
    {selected && <AccessDialog publication={selected} canAcquire={canAcquire} onAcquire={onAcquire} onClose={() => setSelected(null)} />}
  </>;
}
