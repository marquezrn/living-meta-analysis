import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api';
import { initialProtocol } from '../protocol';
import type { AccessResolution, Document, Publication } from '../types';
import PublicationsView, { AccessDialog } from '../views/Publications';
import { ConditionEvidence } from '../views/Evidence';

const publication: Publication = {
  id: 'publication-1', state: 'pending_extraction', source: 'pubmed', source_id: '1',
  title: 'Synthetic open-access study', authors: [], is_preprint: false, status: 'active', related_dois: [],
};
const document: Document = { id: 'document-1', filename: 'source.pdf', sha256: 'hash', status: 'uploaded' };
const access = (...versions: string[]): AccessResolution => ({
  status: 'available', publication_status: 'active', limitations: [],
  locations: versions.map(version => ({ url: `https://arxiv.org/pdf/synthetic-${version}`, kind: 'pdf',
    provider: 'synthetic', version, license: 'CC BY', manuscript: false, redistribution_requires_license_review: true })),
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('private PDF acquisition', () => {
  it('keeps editor acquisition separate from owner-only literature monitoring', async () => {
    vi.spyOn(api, 'accessLocations').mockResolvedValue(access('publishedVersion'));
    const discover = vi.fn();
    render(<PublicationsView project={{ id: 'project-1', name: 'Synthetic review', protocol: initialProtocol(), role: 'editor' }} publications={[publication]} canDiscover={false} canAcquire busy={false} onDiscover={discover} onAcquire={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Check literature now' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Inspect PDF access' }));
    expect(await screen.findByRole('button', { name: 'Add PDF to bibliography' })).toBeEnabled();
    expect(discover).not.toHaveBeenCalled();
  });
  it('requires a scientific version choice before acquiring ambiguous PDFs', async () => {
    vi.spyOn(api, 'accessLocations').mockResolvedValue(access('PMC123.1', 'PMC123.2'));
    const acquire = vi.fn().mockResolvedValue(document);
    render(<AccessDialog publication={publication} canAcquire onAcquire={acquire} onClose={vi.fn()} />);
    const add = await screen.findByRole('button', { name: 'Add PDF to bibliography' });
    expect(add).toBeDisabled();
    expect(acquire).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole('combobox', { name: 'PDF version' }), { target: { value: 'PMC123.1' } });
    fireEvent.click(add);
    expect(acquire).toHaveBeenCalledWith('publication-1', 'PMC123.1');
    expect(await screen.findByText('Extraction has not started.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open stored PDF' })).toHaveAttribute('href', '/api/v1/documents/document-1/file');
  });
  it('allows readers to inspect locations without offering a mutation', async () => {
    vi.spyOn(api, 'accessLocations').mockResolvedValue(access('publishedVersion'));
    const acquire = vi.fn();
    render(<AccessDialog publication={publication} canAcquire={false} onAcquire={acquire} onClose={vi.fn()} />);
    expect(await screen.findByRole('link', { name: 'Inspect source' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add PDF to bibliography' })).not.toBeInTheDocument();
    expect(screen.getByText(/Your role allows inspecting access locations/)).toBeInTheDocument();
    expect(acquire).not.toHaveBeenCalled();
  });
  it('shows failed access without claiming a document was added', async () => {
    vi.spyOn(api, 'accessLocations').mockResolvedValue(access('publishedVersion'));
    const acquire = vi.fn().mockRejectedValue(new Error('Access restricted; upload an authorized copy.'));
    render(<AccessDialog publication={publication} canAcquire onAcquire={acquire} onClose={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add PDF to bibliography' }));
    expect(await screen.findByText('Access restricted; upload an authorized copy.')).toBeInTheDocument();
    expect(screen.queryByText('PDF added to the private bibliography.')).not.toBeInTheDocument();
  });
  it('blocks acquisition when fresh access metadata flags a publication', async () => {
    vi.spyOn(api, 'accessLocations').mockResolvedValue({ ...access('publishedVersion'), publication_status: 'retracted' });
    const acquire = vi.fn();
    render(<AccessDialog publication={publication} canAcquire onAcquire={acquire} onClose={vi.fn()} />);
    expect(await screen.findByRole('button', { name: 'Add PDF to bibliography' })).toBeDisabled();
    expect(screen.getByText(/publication is flagged as retracted/i)).toBeInTheDocument();
    expect(acquire).not.toHaveBeenCalled();
  });
});

describe('experimental condition evidence', () => {
  it('shows accepted condition provenance at the matching private PDF page', () => {
    render(<ConditionEvidence documents={[document]} attributes={[{ name: 'comparator', text: 'Untreated sample', status: 'accepted',
      evidence: [{ document_hash: 'hash', page: 3, source_type: 'text', locator: 'Methods', excerpt: 'Untreated samples served as controls.' }] }]} />);
    expect(screen.getByText('Accepted')).toBeInTheDocument();
    expect(screen.getByText('Text · p. 3 · Methods')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open condition source' })).toHaveAttribute('href', '/api/v1/documents/document-1/file#page=3');
  });
  it('does not portray an unsupported condition as verified', () => {
    render(<ConditionEvidence documents={[]} attributes={[{ name: 'arms_independent', text: 'true' }]} />);
    expect(screen.getByText('Candidate')).toBeInTheDocument();
    expect(screen.getByText('Primary source evidence is missing; this condition is not verified.')).toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });
});
