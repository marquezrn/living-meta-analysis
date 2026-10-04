import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, documentUrl, request } from '../api';
import { demoExport, resetDemoState } from '../demoApi';
import { initialProtocol } from '../protocol';

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); resetDemoState(); });
describe('static demo mode', () => {
  it('never calls fetch and returns an authenticated demo session', async () => {
    vi.stubEnv('VITE_STATIC_DEMO', 'true');
    const fetchMock = vi.fn(); vi.stubGlobal('fetch', fetchMock);
    const session = await api.session();
    expect(session.authenticated).toBe(true);
    expect(session.user?.login).toBeTruthy();
    const [project] = await api.projects();
    await Promise.all([api.project(project.id), api.protocol(project.id), api.documents(project.id), api.runs(project.id), api.experiments(project.id), api.measurements(project.id), api.publications(project.id), api.analysis(project.id), api.benchmark(project.id), api.members(project.id), api.accessLocations('pub-1')]);
    await api.invite(project.id, 'reader'); await api.logout();
    expect((await api.analysis(project.id)).groups?.length).toBeGreaterThan(0);
    expect(documentUrl('doc-1').startsWith('data:')).toBe(true);
    expect(demoExport(project.id, 'csv').size).toBeGreaterThan(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it('updates in-memory state for writes and explains unavailable features', async () => {
    vi.stubEnv('VITE_STATIC_DEMO', 'true');
    const fetchMock = vi.fn(); vi.stubGlobal('fetch', fetchMock);
    const created = await api.createProject('Mine', initialProtocol());
    expect((await api.projects()).some(item => item.id === created.id)).toBe(true);
    await api.saveProtocol(created.id, { ...initialProtocol(), topic: 'changed' });
    expect((await api.protocol(created.id)).topic).toBe('changed');
    await expect(api.uploadDocuments(created.id, [new File(['x'], 'a.pdf')])).rejects.toMatchObject({ message: expect.stringContaining('Not available in demo mode') });
    expect((await api.acquirePublication('pub-1')).id).toContain('pub-1');
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it('keeps using the real API when the flag is off', async () => {
    vi.stubEnv('VITE_STATIC_DEMO', 'false');
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ authenticated: false, user: null, development_mode: false }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    expect((await api.session()).authenticated).toBe(false);
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/session');
    fetchMock.mockResolvedValue(new Response('<html>', { status: 404 }));
    await expect(request('/session')).rejects.toMatchObject({ status: 404, message: 'Server returned 404. Please try again.' });
    expect(documentUrl('d')).toBe('/api/v1/documents/d/file');
  });
});
