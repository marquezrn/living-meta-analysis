import { describe, expect, it } from 'vitest';
import type { Measurement } from '../types';
import { comparableGroups, number, progressInfo, safeExternalUrl } from '../utils';

const measurement = (change: Partial<Measurement> = {}): Measurement => ({
  id: 'synthetic', experiment_id: 'experiment', outcome: 'Droplet_Size_um', raw_value: '7', value: 7,
  unit: 'um', qualifier: 'exact', origin: 'reported', status: 'accepted', uncertainty_type: 'none',
  measurement_method: 'laser diffraction', statistic: 'mean', evidence: [], validation_notes: [], ...change,
});

describe('scientific exploration', () => {
  it('separates methods, times, units and concentration bases', () => {
    const groups = comparableGroups([measurement(), measurement({measurement_method:'DLS'}),
      measurement({time_value:7,time_unit:'day'}), measurement({unit:'nm'}),
      measurement({concentration_basis:'mass'})]);
    expect(groups).toHaveLength(5);
  });
  it('never treats censored values, curves, stale data or unknown methods as comparable exact observations', () => {
    expect(comparableGroups([measurement({qualifier:'lt'}), measurement({origin:'curve_sample'}),
      measurement({status:'stale'}), measurement({measurement_method:null}), measurement({value:NaN})])).toEqual([]);
  });
  it('uses a verified normalized value with its corresponding unit', () => {
    const group = comparableGroups([measurement({value:7000,unit:'nm',normalized_value:7,normalized_unit:'micrometer'})])[0];
    expect(group.values).toEqual([7]); expect(group.unit).toBe('micrometer');
  });
  it('requires explicit opt-in for unaccepted candidates', () => {
    expect(comparableGroups([measurement({status:'candidate'})])).toEqual([]);
    expect(comparableGroups([measurement({status:'candidate'})],true)).toHaveLength(1);
    expect(comparableGroups([measurement({status:'uncertain'})],true)).toEqual([]);
  });
});

describe('safe presentation', () => {
  it('blocks source-controlled executable URLs', () => {
    expect(safeExternalUrl('javascript:alert(1)')).toBeUndefined();
    expect(safeExternalUrl('file:///private/evidence')).toBeUndefined();
    expect(safeExternalUrl('https://doi.org/10.1234/test')).toContain('https://doi.org/');
  });
  it('preserves missingness and reports incomplete progress honestly', () => {
    expect(number(null)).toBe('Not reported'); expect(number(Infinity)).toBe('Not reported');
    expect(progressInfo({stage:'extracting_evidence',pages_completed:2}).percent).toBeNull();
    expect(progressInfo({completed:5,total:10}).percent).toBe(50);
  });
});
