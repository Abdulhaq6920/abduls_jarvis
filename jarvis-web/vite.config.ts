/// <reference types="vitest/config" />
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// The dashboard is served by the FastAPI backend under /ui/ (see jarvis_backend/app.py), so the
// production build is written straight into the backend package. That directory is generated:
// it is git-ignored and rebuilt by `npm run build` (and by the Docker image build).
const BACKEND_WEB_DIR = fileURLToPath(new URL('../jarvis-backend/src/jarvis_backend/web', import.meta.url));

// `npm run dev` proxies API and WebSocket traffic to a locally running backend.
const BACKEND = process.env.VITE_BACKEND_URL ?? 'http://127.0.0.1:8077';
// The backend checks the Origin header on cookie endpoints and the WebSocket. In dev the browser
// is on :5173, so the proxy presents the backend's own origin instead.
const BACKEND_ORIGIN = process.env.VITE_BACKEND_ORIGIN ?? 'http://localhost:8077';

export default defineConfig({
  base: '/ui/',
  plugins: [react()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  build: {
    outDir: BACKEND_WEB_DIR,
    emptyOutDir: true,
    target: 'es2022',
    sourcemap: false,
    // The backend CSP only allows same-origin resources: no data: fonts or inline assets.
    assetsInlineLimit: 0,
    cssCodeSplit: true,
    chunkSizeWarningLimit: 400,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: BACKEND, headers: { Origin: BACKEND_ORIGIN } },
      '/ws': { target: BACKEND.replace(/^http/, 'ws'), ws: true, headers: { Origin: BACKEND_ORIGIN } },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    restoreMocks: true,
    // Threads start far faster than forked processes on Windows and low-memory machines.
    pool: 'threads',
    maxWorkers: 2,
  },
});
