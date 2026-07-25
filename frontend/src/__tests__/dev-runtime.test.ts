import { describe, expect, it } from 'vitest';

import nextConfig from '../../next.config.mjs';
import packageJson from '../../package.json';
import { buildNextDevEnv, resolveBackendPort } from '../../scripts/run-next-dev.mjs';
import {
    buildUpstreamPath,
    isDiagnosticPath,
    isLoopbackAddress,
    resolveStaticPath,
    shouldProxyPath,
    validateServerBind,
} from '../../scripts/serve-export.mjs';

describe('frontend dev runtime', () => {
    it('routes npm run dev through the repo-controlled wrapper script', () => {
        expect(packageJson.scripts.dev).toBe('node ./scripts/run-next-dev.mjs');
    });

    it('hydrates when the root launcher opens the loopback IP', () => {
        expect(nextConfig.allowedDevOrigins).toContain('127.0.0.1');
    });

    it('enables Watchpack polling by default on macOS', () => {
        const env = buildNextDevEnv({}, 'darwin');

        expect(env.WATCHPACK_POLLING).toBe('true');
        expect(env.WATCHPACK_POLLING_INTERVAL).toBe('1000');
    });

    it('respects explicit watcher overrides from the user environment', () => {
        const env = buildNextDevEnv(
            {
                WATCHPACK_POLLING: 'false',
                WATCHPACK_POLLING_INTERVAL: '250',
            },
            'darwin'
        );

        expect(env.WATCHPACK_POLLING).toBe('false');
        expect(env.WATCHPACK_POLLING_INTERVAL).toBe('250');
    });

    it('keeps the frontend backend port aligned with the root environment', () => {
        expect(resolveBackendPort({}, "API_PORT='18181'\n")).toBe('18181');
        expect(resolveBackendPort({ API_PORT: '19191' }, 'API_PORT=18181')).toBe('19191');
        expect(resolveBackendPort(
            { NEXT_PUBLIC_BACKEND_PORT: '20202', API_PORT: '19191' },
            'API_PORT=18181',
        )).toBe('20202');
        expect(() => resolveBackendPort({}, 'API_PORT=not-a-port')).toThrow(
            'Invalid backend port: not-a-port',
        );
    });

    it('serves production through the static-export runtime instead of next start', () => {
        expect(packageJson.scripts.start).toBe('node ./scripts/serve-export.mjs');
        expect(packageJson.scripts['check:export']).toBe(
            'node ./scripts/check-static-export.mjs',
        );
        expect(shouldProxyPath('/projects/project-1')).toBe(true);
        expect(shouldProxyPath('/files/video/example.mp4')).toBe(true);
        expect(shouldProxyPath('/jobs/job-1')).toBe(true);
        expect(shouldProxyPath('/debug/config')).toBe(true);
        expect(shouldProxyPath('/redoc')).toBe(true);
        expect(shouldProxyPath('/static/index.html')).toBe(false);
    });

    it('keeps static export paths inside the out directory', () => {
        const root = '/tmp/enmotion-export';

        expect(resolveStaticPath(root, '/static/')).toBe('/tmp/enmotion-export/index.html');
        expect(resolveStaticPath(root, '/static/_next/app.js')).toBe(
            '/tmp/enmotion-export/_next/app.js'
        );
        expect(resolveStaticPath(root, '/static/%2e%2e/package.json')).toBeNull();
        expect(resolveStaticPath(root, '/unrelated')).toBeNull();
    });

    it('keeps remote static-server exposure and diagnostics explicit', () => {
        expect(isLoopbackAddress('127.0.0.1')).toBe(true);
        expect(isLoopbackAddress('::ffff:127.0.0.1')).toBe(true);
        expect(isLoopbackAddress('203.0.113.10')).toBe(false);
        expect(isDiagnosticPath('/diagnose/log_tail')).toBe(true);
        expect(isDiagnosticPath('/projects/project-1')).toBe(false);
        expect(() => validateServerBind('0.0.0.0')).toThrow(
            'Refusing remote frontend bind 0.0.0.0',
        );
        expect(() => validateServerBind('0.0.0.0', true)).not.toThrow();
    });

    it('preserves a configured backend path prefix when proxying', () => {
        expect(buildUpstreamPath(
            new URL('https://gateway.example/internal/api/'),
            new URL('http://studio.local/projects/p1?full=true'),
        )).toBe('/internal/api/projects/p1?full=true');
    });
});
