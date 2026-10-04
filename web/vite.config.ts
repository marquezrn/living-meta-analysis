import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';


declare const process: { env: Record<string, string | undefined> };
export default defineConfig({
  base: process.env.VITE_BASE_PATH ?? '/',
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000', '/auth': 'http://127.0.0.1:8000' } },
  build: { target: 'es2022', sourcemap: false },
  test: { environment: 'jsdom', setupFiles: ['./src/test/setup.ts'], clearMocks: true },
});
