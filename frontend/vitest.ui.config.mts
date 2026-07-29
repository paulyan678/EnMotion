import { defineConfig } from 'vitest/config';
import path from 'path';

export default defineConfig({
    esbuild: {
        jsx: 'automatic',
    },
    test: {
        environment: 'happy-dom',
        include: [
            'src/components/**/*.test.tsx',
            'src/components/**/*.spec.tsx',
        ],
        setupFiles: ['./vitest.setup.ts'],
        sequence: {
            // Unmount components before file-local hooks restore API spies and stores.
            hooks: 'list',
        },
    },
    resolve: {
        alias: {
            '@': path.resolve(__dirname, 'src'),
        },
    },
});
