import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, request } from '../api';

afterEach(() => vi.unstubAllGlobals());
describe('private API transport', () => {
  it('uses server sessions and preserves failed authorization instead of returning fabricated data', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({detail:'Membership required'}),{status:403}));
    vi.stubGlobal('fetch',fetchMock);
    await expect(request('/projects')).rejects.toMatchObject({status:403,message:'Membership required'});
    expect(fetchMock.mock.calls[0][1].credentials).toBe('same-origin');
  });
  it('uploads PDFs using the backend repeated files field', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('[]',{status:201})); vi.stubGlobal('fetch',fetchMock);
    await api.uploadDocuments('project',[new File(['%PDF-synthetic'],'synthetic.pdf')]);
    const options = fetchMock.mock.calls[0][1]; expect(options.body.getAll('files')).toHaveLength(1);
    expect(options.headers.has('Content-Type')).toBe(false);
  });
  it('reports unavailable and unreadable servers explicitly', async () => {
    vi.stubGlobal('fetch',vi.fn().mockRejectedValue(new Error('offline')));
    await expect(request('/session')).rejects.toMatchObject({status:0});
    vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response('broken',{status:200})));
    await expect(request('/session')).rejects.toMatchObject({message:'The server returned an unreadable response.'});
  });
  it('resolves publication access and sends the chosen version as a private acquisition request', async () => {
    const fetchMock = vi.fn().mockImplementation(async () => new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    await api.accessLocations('publication/1');
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/publications/publication%2F1/access');
    await api.acquirePublication('publication/1', 'PMC123.1');
    expect(fetchMock.mock.calls[1][0]).toBe('/api/v1/publications/publication%2F1/acquire');
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'POST', credentials: 'same-origin', body: JSON.stringify({ version: 'PMC123.1' }) });
  });
});
