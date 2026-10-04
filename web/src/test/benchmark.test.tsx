import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../api';
import BenchmarkView from '../views/Benchmark';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('distinguishes available reference scope from an extraction comparison that has not run', async () => {
  vi.spyOn(api, 'benchmark').mockResolvedValue({ status: 'not_run', counts: { reference_rows: 103, matched: 0, missing_source_rows: 12 } });
  render(<BenchmarkView projectId="project-1" canEdit={false} refreshKey={0} />);
  expect(await screen.findByText('Reference scope is available. No extraction comparison has been run; accuracy metrics are unavailable.')).toBeInTheDocument();
  expect(screen.getByText('103')).toBeInTheDocument();
  expect(screen.getByText('Not run')).toBeInTheDocument();
  expect(screen.queryByText('0 / 0 compared fields')).not.toBeInTheDocument();
});
