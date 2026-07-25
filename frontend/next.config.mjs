import path from 'node:path';
import { fileURLToPath } from 'node:url';

/** @type {import('next').NextConfig} */
const isProd = process.env.NODE_ENV === 'production';
const frontendRoot = path.dirname(fileURLToPath(import.meta.url));

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:17177';

const nextConfig = {
    output: isProd ? 'export' : undefined,
    distDir: isProd ? 'out' : undefined,
    basePath: isProd ? '/static' : undefined,
    assetPrefix: isProd ? '/static' : undefined,
    // The repo launcher intentionally opens the loopback IP. Trust that host
    // so Next's development client can hydrate instead of serving inert HTML.
    allowedDevOrigins: ['127.0.0.1'],
    // Dev-only: proxy /api-proxy/* to backend to avoid CORS issues (e.g. file downloads).
    // Static exports cannot contain rewrites, so omit the option entirely in production.
    ...(!isProd ? {
        async rewrites() {
            return [
                {
                    source: '/api-proxy/:path*',
                    destination: `${BACKEND_URL}/:path*`,
                },
            ];
        },
    } : {}),
    turbopack: {
        root: frontendRoot,
    },
    images: {
        unoptimized: true,
        remotePatterns: [
            {
                protocol: "https",
                hostname: "placehold.co",
            },
            {
                protocol: "http",
                hostname: "localhost",
                port: "17177",
            },
        ],
    },
};

export default nextConfig;
