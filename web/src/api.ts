import type { AccessResolution, AnalysisSpec, Benchmark, Contrast, DescriptiveAnalysis, Document, Experiment, Invitation, Measurement, Member, Project, Protocol, Publication, ReviewStatus, Run, Session } from './types';

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); this.name = 'ApiError'; }
}
function errorDetail(value: unknown): string {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(errorDetail).join('; ');
  if (value && typeof value === 'object') {
    const obj = value as Record<string, unknown>;
    if (typeof obj.msg === 'string') return `${Array.isArray(obj.loc) ? obj.loc.join('.') + ': ' : ''}${obj.msg}`;
    return JSON.stringify(value);
  }
  return 'The request could not be completed.';
}
export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set('Accept', 'application/json');
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  let response: Response;
  try { response = await fetch(`/api/v1${path}`, { ...options, headers, credentials: 'same-origin' }); }
  catch { throw new ApiError('Cannot reach the server. Check the connection and try again.', 0); }
  if (!response.ok) {
    let detail: unknown;
    try { const payload = await response.json() as Record<string, unknown>; detail = payload.detail ?? payload.error ?? payload; }
    catch { detail = response.status === 401 ? 'Your session has expired. Sign in again.' : `Server returned ${response.status}. Please try again.`; }
    throw new ApiError(errorDetail(detail), response.status);
  }
  if (response.status === 204) return undefined as T;
  try { return await response.json() as T; }
  catch { throw new ApiError('The server returned an unreadable response.', response.status); }
}
const projectPath = (id: string) => `/projects/${encodeURIComponent(id)}`;
const post = (body?: unknown): RequestInit => ({ method: 'POST', ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
export const api = {
  session: () => request<Session>('/session'),
  developmentSession: () => request<Session>('/development/session', post()),
  projects: () => request<Project[]>('/projects'),
  createProject: (name: string, protocol: Protocol) => request<Project>('/projects', post({ name, protocol })),
  project: (id: string) => request<Project>(projectPath(id)),
  protocol: (id: string) => request<Protocol>(`${projectPath(id)}/protocol`),
  saveProtocol: (id: string, protocol: Protocol) => request<Protocol>(`${projectPath(id)}/protocol`, { method: 'PUT', body: JSON.stringify(protocol) }),
  documents: (id: string) => request<Document[]>(`${projectPath(id)}/documents`),
  uploadDocuments: (id: string, files: File[]) => { const body = new FormData(); files.forEach(file => body.append('files', file)); return request<unknown>(`${projectPath(id)}/documents`, { method: 'POST', body }); },
  runs: (id: string) => request<Run[]>(`${projectPath(id)}/runs`),
  startRun: (id: string, budget_usd: number, document_ids: string[]) => request<Run>(`${projectPath(id)}/runs`, post({ budget_usd, document_ids })),
  cancelRun: (id: string) => request<Run>(`/runs/${encodeURIComponent(id)}/cancel`, post()),
  resumeRun: (id: string) => request<Run>(`/runs/${encodeURIComponent(id)}/resume`, post()),
  experiments: (id: string) => request<Experiment[]>(`${projectPath(id)}/experiments`),
  measurements: (id: string) => request<Measurement[]>(`${projectPath(id)}/measurements`),
  reviewMeasurement: (id: string, status: ReviewStatus, notes: string) => request<Measurement>(`/measurements/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ status, notes }) }),
  publications: (id: string) => request<Publication[]>(`${projectPath(id)}/publications`),
  discover: (id: string) => request<unknown>(`${projectPath(id)}/discover`, post()),
  acquirePublication: (id: string, version?: string) => request<Document>(`/publications/${encodeURIComponent(id)}/acquire`, post(version ? { version } : undefined)),
  accessLocations: (id: string) => request<AccessResolution>(`/publications/${encodeURIComponent(id)}/access`),
  analysis: (id: string) => request<DescriptiveAnalysis>(`${projectPath(id)}/analysis`),
  synthesize: (id: string, spec: AnalysisSpec, contrasts: Contrast[]) => request<Record<string, unknown>>(`${projectPath(id)}/analysis`, post({ spec, contrasts })),
  benchmark: (id: string) => request<Benchmark | null>(`${projectPath(id)}/benchmark`),
  compare: (id: string, file: File) => { const body = new FormData(); body.append('reference_html', file); return request<Benchmark>(`${projectPath(id)}/benchmark`, { method: 'POST', body }); },
  members: (id: string) => request<Member[]>(`${projectPath(id)}/members`),
  invite: (id: string, role: 'editor' | 'reader') => request<Invitation>(`${projectPath(id)}/invitations`, post({ role })),
  acceptInvitation: (token: string) => request<{ project_id?: string; project?: Project }>(`/invitations/${encodeURIComponent(token)}/accept`, post()),
  logout: async () => { const response = await fetch('/auth/logout', { method: 'POST', credentials: 'same-origin' }); if (!response.ok) throw new ApiError('Sign out could not be completed. Please try again.', response.status); },
};
export const documentUrl = (id: string, page?: number) => `/api/v1/documents/${encodeURIComponent(id)}/file${page ? `#page=${page}` : ''}`;
export const artifactUrl = (projectId: string, key: string) => `/api/v1${projectPath(projectId)}/artifacts?${new URLSearchParams({ key })}`;
export const exportUrl = (id: string, format: string) => `/api/v1${projectPath(id)}/exports/${encodeURIComponent(format)}`;
