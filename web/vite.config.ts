import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000', '/auth': 'http://127.0.0.1:8000' } },
  build: { target: 'es2022', sourcemap: false },
  test: { environment: 'jsdom', setupFiles: ['./src/test/setup.ts'], clearMocks: true },
});
