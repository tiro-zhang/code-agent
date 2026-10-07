import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
export default defineConfig({
    plugins: [react()],
    base: '/',
    build: { outDir: '../src/mewcode/web/static', emptyOutDir: true, sourcemap: false, rollupOptions: {output: {manualChunks(id) { if (id.includes('/node_modules/react-dom/') || id.includes('/node_modules/react/') || id.includes('/node_modules/scheduler/')) return 'react'; if (id.includes('/highlight.js/')) return 'syntax'; }}} },
    test: { environment: 'jsdom', setupFiles: ['./tests/setup.ts'], include: ['tests/**/*.test.{ts,tsx}'] },
});
