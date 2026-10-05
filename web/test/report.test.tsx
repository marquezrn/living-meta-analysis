import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ReportApp } from '../report/main';
import { csvSnapshot, measurements, normalizeSnapshot, sha256, stale } from '../report/core';
import type { Snapshot } from '../report/core';
import { comparableGroups } from '../src/utils';

vi.mock('plotly.js-dist-min', () => ({ default: { newPlot: vi.fn(async () => ({ on: vi.fn() })), purge: vi.fn(), Plots: { resize: vi.fn() } } }));
const snapshot = (): Snapshot => normalizeSnapshot({ schema_version: 2, project: { name: 'Synthetic offline review' },
  protocol: { name: 'Synthetic protocol' }, documents: [], publications: [],
  experiments: [{ id: 'e1', sample_label: 'Synthetic sample', study_family: 'family-1', attributes: [], measurements: [{ id: 'm1', experiment_id: 'e1', outcome: 'diameter', raw_value: '7', value: 7,
    unit: 'um', qualifier: 'exact', status: 'accepted', origin: 'reported', statistic: 'mean', measurement_method: 'Synthetic method', uncertainty_type: 'none', validation_notes: [], evidence: [{ document_hash: '0'.repeat(64), page: 2, source_type: 'text', locator: 'Results', excerpt: 'Synthetic diameter was 7 um.' }] }] }],
  benchmark: { status: 'not_run' }, synthesis: { status: 'not_run' }, freshness: { last_extraction: '2026-01-01T00:00:00Z' } });

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(() => { throw new Error('Offline reader must not fetch'); }));
  vi.stubGlobal('crypto', webcrypto);
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
  URL.createObjectURL = vi.fn(() => 'blob:verified-local-source'); URL.revokeObjectURL = vi.fn();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('offline snapshot science and integrity', () => {
  it('keeps unknown uncertainty and n missing instead of creating inferential inputs', () => {
    const data = snapshot();
    expect(measurements(data)[0].n_independent).toBeUndefined();
    expect(measurements(data)[0].uncertainty_type).toBe('none');
    expect(csvSnapshot(data)).toContain('"n_independent"');
    expect(comparableGroups(measurements(data))).toHaveLength(1);
  });
  it('excludes quarantined source evidence from accepted charts', () => {
    const data = snapshot(); data.documents = [{ id: 'd1', filename: 'source.pdf', sha256: '0'.repeat(64), status: 'quarantined' }];
    expect(measurements(data)[0].status).toBe('stale');
    expect(comparableGroups(measurements(data))).toHaveLength(0);
    expect(stale(data)).toBe(true);
  });
  it('rejects wrong schema, non-finite numbers and inconsistent scientific identifiers', () => {
    expect(() => normalizeSnapshot({ schema_version: 1 })).toThrow('schema_version 2');
    expect(() => normalizeSnapshot({ schema_version: 2, provenance: { value: Infinity } })).toThrow('Non-finite');
    const data = snapshot(); data.experiments[0].measurements[0].experiment_id = 'different-experiment';
    expect(() => normalizeSnapshot(data)).toThrow('identity');
  });
  it('preserves XML and media locators without inventing PDF pages', () => {
    const data = snapshot();
    data.experiments[0].measurements[0].evidence = [{ document_hash: '0'.repeat(64), source_format: 'xml', page: null, xml_element: '/article/body/sec[1]/p[2]', source_type: 'text', locator: 'Results paragraph', excerpt: 'Synthetic value 7 um.' }];
    expect(normalizeSnapshot(data).experiments[0].measurements[0].evidence[0].page).toBeNull();
    data.experiments[0].measurements[0].evidence[0].xml_element = null;
    expect(() => normalizeSnapshot(data)).toThrow('element ID');
    data.experiments[0].measurements[0].evidence = [{ document_hash: '0'.repeat(64), source_format: 'media', page: null, asset_path: 'media/figure-1.png', source_type: 'figure', locator: 'Figure 1', excerpt: '' }];
    expect(normalizeSnapshot(data).experiments[0].measurements[0].evidence[0].asset_path).toBe('media/figure-1.png');
    data.experiments[0].measurements[0].evidence[0].asset_path = '../private-file.png';
    expect(() => normalizeSnapshot(data)).toThrow('safe workspace-relative');
  });
  it('escapes spreadsheet formulas while retaining original numerical observations', () => {
    const data = snapshot(); data.experiments[0].sample_label = '=EXECUTE_UNTRUSTED';
    const csv = csvSnapshot(data);
    expect(csv).toContain("'=EXECUTE_UNTRUSTED"); expect(csv).toContain('"7"');
  });
});

describe('server-free reader', () => {
  it('renders local evidence, benchmark and charts without any HTTP request', async () => {
    render(<ReportApp initialSnapshot={snapshot()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Evidence table' }));
    expect(screen.getByText('Synthetic sample')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'View source' }));
    expect(screen.getByRole('dialog', { name: 'Experimental source evidence' })).toBeInTheDocument();
    expect(screen.getByText(/only a matching SHA-256 file/)).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Manual benchmark' }));
    expect(screen.getByText(/No extraction comparison has been run/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Outcome explorer' }));
    expect(screen.getByRole('img', { name: /Recorded experimental measurements/ })).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });
  it('does not equate the selected filename with source integrity', async () => {
    const data = snapshot(); data.documents = [{ id: 'd1', filename: 'source.pdf', sha256: '0'.repeat(64), status: 'extracted' }];
    const file = new File(['%PDF-wrong-source'], 'source.pdf', { type: 'application/pdf' });
    Object.defineProperty(file, 'arrayBuffer', { value: async () => new TextEncoder().encode('%PDF-wrong-source').buffer });
    render(<ReportApp initialSnapshot={data} />);
    fireEvent.change(screen.getByLabelText('Select local source PDFs, XML and images'), { target: { files: [file] } });
    expect(await screen.findByText(/0 local source files matched by SHA-256/)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Verified PDF' })).not.toBeInTheDocument();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });
  it('makes a matching local PDF available through a Blob URL without transmission', async () => {
    const bytes = new TextEncoder().encode('%PDF-synthetic-source').buffer;
    const digest = await sha256(bytes);
    const data = snapshot(); data.documents = [{ id: 'd1', filename: 'source.pdf', sha256: digest, status: 'extracted' }];
    const file = new File([bytes], 'renamed-source.pdf', { type: 'application/pdf' }); Object.defineProperty(file, 'arrayBuffer', { value: async () => bytes });
    render(<ReportApp initialSnapshot={data} />);
    fireEvent.change(screen.getByLabelText('Select local source PDFs, XML and images'), { target: { files: [file] } });
    expect(await screen.findByRole('link', { name: 'Verified PDF' })).toHaveAttribute('href', 'blob:verified-local-source');
    expect(fetch).not.toHaveBeenCalled();
    cleanup(); expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:verified-local-source');
  });
  it('keeps closed mobile navigation inert and traps keyboard access when opened', async () => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    render(<ReportApp initialSnapshot={snapshot()} />);
    const sidebar = document.querySelector('.sidebar'); expect(sidebar).toHaveAttribute('inert');
    screen.getByRole('button', { name: 'Open navigation' }).focus();
    fireEvent.click(screen.getByRole('button', { name: 'Open navigation' }));
    await waitFor(() => expect(sidebar).not.toHaveAttribute('inert'));
    expect(screen.getByRole('button', { name: 'Close navigation' })).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(sidebar).toHaveAttribute('inert'); expect(screen.getByRole('button', { name: 'Open navigation' })).toHaveFocus();
  });
});
