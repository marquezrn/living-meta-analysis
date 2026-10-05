import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api';
import type { DescriptiveAnalysis, Experiment, Measurement, Project } from '../types';
import Synthesis from '../views/Synthesis';

vi.mock('../api', () => ({ api: { analysis: vi.fn(), synthesize: vi.fn() } }));

const project: Project = {
  id: 'project-a', name: 'Synthetic evidence',
  protocol: {
    name: 'Synthetic protocol', version: '1', topic: 'Synthetic emulsion experiments',
    inclusion: [], exclusion: [], variables: [], queries: {}, enabled_sources: [],
    analysis_mode: 'inferential', outcome: 'droplet_diameter', unit: 'um', effect_measure: 'MD',
    outcome_definition: 'Mean diameter', measurement_method: 'microscopy', comparator: 'matched control',
    time_point: '0 day', concentration_basis: 'mass',
  },
};
const pooled = { status: 'completed', estimate: 2.345, confidence_interval: [1, 3] };
const fresh: DescriptiveAnalysis = { groups: [], synthesis_stale: false, inferential_result: pooled };
const props = (value = project, refreshKey = 0) => ({ project: value, refreshKey, canEdit: true, measurements: [], experiments: [] });
const treatment: Measurement = { id: 'treatment', experiment_id: 'treatment-experiment', outcome: 'droplet_diameter',
  raw_value: '7', value: 7, unit: 'um', qualifier: 'exact', statistic: 'mean', measurement_method: 'microscopy',
  uncertainty_type: 'SD', uncertainty: 1, n_independent: 3, origin: 'reported', status: 'accepted',
  validation_notes: [], evidence: [] };
const control: Measurement = { ...treatment, id: 'control', experiment_id: 'control-experiment', raw_value: '5', value: 5 };
const arms: Experiment[] = [treatment, control].map(measurement => ({ id: measurement.experiment_id,
  sample_label: measurement.id, study_family: 'synthetic-family', attributes: [], measurements: [measurement] }));

async function selectSourceArms() {
  await screen.findByRole('heading', { name: 'Returned synthesis' });
  fireEvent.click(screen.getByRole('button', { name: 'Add contrast' }));
  fireEvent.change(screen.getByRole('combobox', { name: 'Contrast 1 treatment measurement' }), { target: { value: treatment.id } });
  fireEvent.change(screen.getByRole('combobox', { name: 'Contrast 1 control measurement' }), { target: { value: control.id } });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((success, failure) => { resolve = success; reject = failure; });
  return { promise, resolve, reject };
}

beforeEach(() => { vi.resetAllMocks(); vi.mocked(api.analysis).mockResolvedValue(fresh); });
afterEach(cleanup);

describe('scientific synthesis freshness', () => {
  it('shows a completed inference only for the loaded project context', async () => {
    render(<Synthesis {...props()} />);
    expect(await screen.findByRole('heading', { name: 'Returned synthesis' })).toBeInTheDocument();
    expect(screen.getByText(/2.345/)).toBeInTheDocument();
  });

  it('removes the old estimate immediately on refresh and keeps it absent for a descriptive response', async () => {
    const view = render(<Synthesis {...props()} />);
    await screen.findByRole('heading', { name: 'Returned synthesis' });
    const next = deferred<DescriptiveAnalysis>();
    vi.mocked(api.analysis).mockReturnValueOnce(next.promise);
    view.rerender(<Synthesis {...props(project, 1)} />);
    expect(screen.queryByText(/2.345/)).not.toBeInTheDocument();
    await act(async () => { next.resolve({ groups: [], inferential_result: null, synthesis_stale: false }); });
    expect(screen.queryByRole('heading', { name: 'Returned synthesis' })).not.toBeInTheDocument();
  });

  it('does not retain a pooled estimate when a refresh fails', async () => {
    const view = render(<Synthesis {...props()} />);
    await screen.findByRole('heading', { name: 'Returned synthesis' });
    vi.mocked(api.analysis).mockRejectedValueOnce(new Error('Summary is unavailable.'));
    view.rerender(<Synthesis {...props(project, 1)} />);
    expect(await screen.findByText('Summary is unavailable.')).toBeInTheDocument();
    expect(screen.queryByText(/2.345/)).not.toBeInTheDocument();
  });

  it('ignores a delayed response from the previous project', async () => {
    const old = deferred<DescriptiveAnalysis>();
    vi.mocked(api.analysis).mockReturnValueOnce(old.promise).mockResolvedValueOnce({ groups: [], inferential_result: null });
    const view = render(<Synthesis {...props()} />);
    view.rerender(<Synthesis {...props({ ...project, id: 'project-b' })} />);
    await waitFor(() => expect(screen.queryByText('Loading scientific summary')).not.toBeInTheDocument());
    await act(async () => { old.resolve(fresh); });
    expect(screen.queryByText(/2.345/)).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Returned synthesis' })).not.toBeInTheDocument();
  });

  it.each([
    { groups: [], synthesis_stale: true, inferential_result: pooled },
    { groups: [], synthesis_stale: true, inferential_result: null, historical_inferential_result: pooled, inferential_stale: true },
    { groups: [], synthesis_stale: false, inferential_result: null, historical_inferential_result: pooled, inferential_stale: true },
  ])('labels stale and historical estimates explicitly instead of presenting a current pooled analysis', async response => {
    vi.mocked(api.analysis).mockResolvedValueOnce(response);
    render(<Synthesis {...props()} />);
    expect(await screen.findByRole('heading', { name: 'Historical inferential synthesis' })).toBeInTheDocument();
    expect(screen.getByText(/They are not a current pooled analysis/)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Returned synthesis' })).not.toBeInTheDocument();
    if (response.synthesis_stale) expect(screen.getByText(/This synthesis is stale/)).toBeInTheDocument();
  });

  it('invalidates the displayed result immediately when the registered protocol changes', async () => {
    const view = render(<Synthesis {...props()} />);
    await screen.findByRole('heading', { name: 'Returned synthesis' });
    const next = deferred<DescriptiveAnalysis>();
    vi.mocked(api.analysis).mockReturnValueOnce(next.promise);
    view.rerender(<Synthesis {...props({ ...project, protocol: { ...project.protocol, unit: 'nm' } })} />);
    expect(screen.queryByText(/2.345/)).not.toBeInTheDocument();
    await act(async () => { next.resolve({ groups: [], synthesis_stale: true }); });
    expect(screen.getByText(/This synthesis is stale/)).toBeInTheDocument();
  });

  it('clears inference throughout an explicit descriptive update, even if an older server retains that field', async () => {
    const update = deferred<Record<string, unknown>>();
    vi.mocked(api.synthesize).mockReturnValueOnce(update.promise);
    render(<Synthesis {...props()} />);
    await screen.findByRole('heading', { name: 'Returned synthesis' });
    fireEvent.click(screen.getByRole('button', { name: 'Update descriptive synthesis' }));
    expect(screen.queryByText(/2.345/)).not.toBeInTheDocument();
    expect(api.synthesize).toHaveBeenCalledWith(project.id, expect.objectContaining({ unit: 'um' }), []);
    await act(async () => { update.resolve({ groups: [] }); });
    await waitFor(() => expect(screen.queryByText('Loading scientific summary')).not.toBeInTheDocument());
    expect(screen.queryByText(/2.345/)).not.toBeInTheDocument();
  });

  it('clears the prior inference when a descriptive update fails', async () => {
    vi.mocked(api.synthesize).mockRejectedValueOnce(new Error('Descriptive update was rejected.'));
    render(<Synthesis {...props()} />);
    await screen.findByRole('heading', { name: 'Returned synthesis' });
    fireEvent.click(screen.getByRole('button', { name: 'Update descriptive synthesis' }));
    expect(await screen.findByText('Descriptive update was rejected.')).toBeInTheDocument();
    expect(screen.queryByText(/2.345/)).not.toBeInTheDocument();
  });

  it('clears the prior inference when a source-linked inferential request fails', async () => {
    vi.mocked(api.synthesize).mockRejectedValueOnce(new Error('Source comparison was rejected.'));
    render(<Synthesis {...props()} measurements={[treatment, control]} experiments={arms} />);
    await selectSourceArms();
    fireEvent.click(screen.getByRole('button', { name: 'Validate and synthesize' }));
    expect(await screen.findByText('Source comparison was rejected.')).toBeInTheDocument();
    expect(screen.queryByText(/2.345/)).not.toBeInTheDocument();
  });

  it('presents an unsuccessful analysis response as a limitation rather than a historical pooled estimate', async () => {
    vi.mocked(api.synthesize).mockResolvedValueOnce({ status: 'engine_unavailable', limitation: 'R is unavailable.' });
    render(<Synthesis {...props()} measurements={[treatment, control]} experiments={arms} />);
    await selectSourceArms();
    vi.mocked(api.analysis).mockResolvedValueOnce({ groups: [], synthesis_stale: true, historical_inferential_result: pooled });
    fireEvent.click(screen.getByRole('button', { name: 'Validate and synthesize' }));
    expect(await screen.findByRole('heading', { name: 'Analysis response' })).toBeInTheDocument();
    expect(screen.getByText(/R is unavailable/)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Historical inferential synthesis' })).not.toBeInTheDocument();
    expect(screen.queryByText(/2.345/)).not.toBeInTheDocument();
  });
});
