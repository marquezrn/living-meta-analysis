import type { Protocol } from './types';
export const SOURCES: Record<string, { name: string; note: string }> = {
  openalex: { name: 'OpenAlex', note: 'Broad scholarly discovery' },
  crossref: { name: 'Crossref', note: 'DOIs, corrections and retractions' },
  pubmed: { name: 'PubMed', note: 'Biomedical and biomaterials literature' },
  europepmc: { name: 'Europe PMC', note: 'Literature, open text and article status' },
  scopus: { name: 'Scopus', note: 'Requires a verified institutional entitlement' },
  arxiv: { name: 'arXiv', note: 'Preprints are identified separately' },
};
export function initialProtocol(name = 'Nanocellulose-stabilized Pickering emulsions'): Protocol {
  return {
    name, version: '1.0', topic: 'Pickering emulsions stabilized by nanocellulose',
    inclusion: ['Original experiments on oil/water Pickering emulsions containing nanocellulose', 'Film and coating studies only when identifiable emulsion experiments are reported'],
    exclusion: ['Reviews are discovery sources only', 'Pure simulations are not primary experimental evidence'],
    variables: ['Emulsion_Type', 'Oil_Type', 'Oil_Content_vol_percent', 'Emulsification_Method', 'Droplet_Size_um', 'Stability_Days', 'Electrolyte_Concentration_mM', 'Type_of_Nanocellulose', 'Modification', 'Concentration_wt_percent', 'Particle_L_nm', 'Particle_w_nm', 'Aspect_Ratio', 'Crystallinity_Index', 'Zeta_Potential_mV', 'Surface_Charge_Density_e_nm2', 'Surface_Energy_mJ_m2', 'Contact_Angle_deg', 'Oil_Content_wt_percent', 'Surface_Charge_Density_mmol_g', 'pH', 'Temperature', 'Storage_Time', 'Rheology', 'Preparation_Settings', 'Replicates', 'Uncertainty'],
    queries: {
      openalex: 'Pickering nanocellulose', crossref: 'Pickering nanocellulose',
      pubmed: '(Pickering[Title/Abstract]) AND (nanocellulose[Title/Abstract] OR "cellulose nanocrystals"[Title/Abstract] OR "cellulose nanofibrils"[Title/Abstract])',
      europepmc: 'Pickering AND (nanocellulose OR "cellulose nanocrystals" OR "cellulose nanofibrils")',
      scopus: 'TITLE-ABS-KEY(Pickering AND (nanocellulose OR "cellulose nanocrystals" OR "cellulose nanofibrils"))',
      arxiv: 'all:Pickering AND (all:nanocellulose OR all:"cellulose nanocrystals")',
    }, enabled_sources: ['openalex', 'crossref', 'pubmed', 'europepmc'],
    analysis_mode: 'descriptive', outcome: 'Droplet_Size_um', effect_measure: 'MD',
    outcome_definition: null, measurement_method: null, comparator: null, time_point: null, concentration_basis: null,
  };
}
