# EnMotion frontend

The frontend is a Next.js static-export application. Use Node.js 24.11+ (24.x LTS)
and the checked-in npm lockfile.

```bash
npm ci
npm run dev
```

The development UI listens on `http://127.0.0.1:3008` and talks to the backend
on port `17177`.

Run the complete frontend verification before submitting changes:

```bash
npm run lint
npm run typecheck
npm run test:all
npm run check:colors
npm run build
```

`npm run build` writes the desktop-prefixed static export to `frontend/out`.
With the backend already listening on `127.0.0.1:17177`, `npm start` previews
that export on `http://127.0.0.1:3008/static/index.html` and proxies API/file
requests to the backend. Production exports always use the `/static` prefix
expected by the loopback-only desktop sidecar.
