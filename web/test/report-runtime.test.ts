import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { JSDOM, requestInterceptor, VirtualConsole } from 'jsdom';
import { expect, it, vi } from 'vitest';

it('boots the actual packaged classic reader without Node globals, network resources or eval', async () => {
  const code = await readFile(resolve(process.cwd(), '../src/livingmeta/report_assets/reader.js'), 'utf8');
  const errors: string[] = [];
  const requests: string[] = [];
  const console = new VirtualConsole();
  console.on('jsdomError', error => { if (!error.message.includes('Not implemented')) errors.push(error.message); });
  const blocked = vi.fn(() => { throw new Error('Network and dynamic evaluation are forbidden in the offline reader'); });
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div><script type="application/json" id="report-data">{"schema_version":2,"project":{"name":"Actual bundled reader test"}}</script></body></html>', {
    url: 'file:///offline-report.html', runScripts: 'dangerously',
    resources: { interceptors: [requestInterceptor(request => { requests.push(request.url); return new Response('', { status: 403 }); })] },
    virtualConsole: console,
    beforeParse(window) {
      Object.defineProperty(window.URL, 'createObjectURL', { value: () => 'blob:offline-test' });
      Object.defineProperty(window.URL, 'revokeObjectURL', { value: () => undefined });
      Object.defineProperty(window, 'fetch', { value: blocked });
      Object.defineProperty(window, 'eval', { value: blocked });
      window.XMLHttpRequest.prototype.open = blocked as typeof window.XMLHttpRequest.prototype.open;
      window.HTMLCanvasElement.prototype.getContext = (() => ({ measureText: (text: string) => ({ width: text.length * 7 }), fillRect: () => undefined, clearRect: () => undefined, getImageData: () => ({ data: [] }), putImageData: () => undefined, createImageData: () => [], setTransform: () => undefined, drawImage: () => undefined, save: () => undefined, restore: () => undefined, beginPath: () => undefined, moveTo: () => undefined, lineTo: () => undefined, closePath: () => undefined, stroke: () => undefined, translate: () => undefined, scale: () => undefined, rotate: () => undefined, arc: () => undefined, fill: () => undefined, transform: () => undefined, rect: () => undefined, clip: () => undefined })) as unknown as typeof window.HTMLCanvasElement.prototype.getContext;
    },
  });
  try {
    const script = dom.window.document.createElement('script'); script.textContent = code; dom.window.document.body.appendChild(script);
    await vi.waitFor(() => {
      expect(errors).toEqual([]);
      expect(dom.window.document.querySelector('#root')?.textContent).toContain('Source inventory is not extracted experimental data.');
    });
    expect(dom.window.document.querySelector('h1')?.textContent).toBe('Actual bundled reader test');
    expect(errors).toEqual([]); expect(requests).toEqual([]); expect(blocked).not.toHaveBeenCalled();
  } finally { dom.window.close(); }
});
