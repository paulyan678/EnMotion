import fs from "node:fs";
import http from "node:http";
import https from "node:https";
import path from "node:path";
import { pathToFileURL } from "node:url";

const API_PREFIXES = new Set([
  "art_direction",
  "asset-sources",
  "bgm",
  "config",
  "debug",
  "diagnose",
  "docs",
  "files",
  "health",
  "jobs",
  "library",
  "openapi.json",
  "playground",
  "projects",
  "prompt_defaults",
  "redoc",
  "series",
  "system",
  "tasks",
  "upload",
  "video",
  "voices",
]);

const DIAGNOSTIC_PREFIXES = new Set([
  "debug",
  "diagnose",
  "docs",
  "openapi.json",
  "redoc",
  "system",
]);

const TRUE_VALUES = new Set(["1", "true", "yes", "on"]);

const CONTENT_TYPES = {
  ".css": "text/css; charset=utf-8",
  ".gif": "image/gif",
  ".html": "text/html; charset=utf-8",
  ".ico": "image/x-icon",
  ".jpeg": "image/jpeg",
  ".jpg": "image/jpeg",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".txt": "text/plain; charset=utf-8",
  ".webp": "image/webp",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
};

export function shouldProxyPath(pathname) {
  const prefix = pathname.replace(/^\/+/, "").split("/", 1)[0];
  return API_PREFIXES.has(prefix);
}

export function isDiagnosticPath(pathname) {
  const prefix = pathname.replace(/^\/+/, "").split("/", 1)[0];
  return DIAGNOSTIC_PREFIXES.has(prefix);
}

export function isLoopbackAddress(address) {
  const value = String(address ?? "").toLowerCase().replace(/^::ffff:/, "");
  return value === "::1" || value === "localhost" || /^127(?:\.|$)/.test(value);
}

export function validateServerBind(host, allowRemote = false) {
  if (!isLoopbackAddress(host) && !allowRemote) {
    throw new Error(
      `Refusing remote frontend bind ${host}. Set ENMOTION_ALLOW_REMOTE_FRONTEND=1 `
      + "only behind an authenticated reverse proxy or trusted firewall.",
    );
  }
}

export function buildUpstreamPath(target, requestUrl) {
  const targetUrl = target instanceof URL ? target : new URL(target);
  return `${targetUrl.pathname.replace(/\/+$/, "")}${requestUrl.pathname}${requestUrl.search}`;
}

export function resolveStaticPath(exportRoot, pathname) {
  let decoded;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    return null;
  }
  if (decoded.includes("\0")) return null;

  let relative;
  if (decoded === "/static" || decoded === "/static/") {
    relative = "index.html";
  } else if (decoded.startsWith("/static/")) {
    relative = decoded.slice("/static/".length);
  } else {
    return null;
  }
  if (!relative || relative.endsWith("/")) relative += "index.html";

  const root = path.resolve(exportRoot);
  const candidate = path.resolve(root, relative);
  if (candidate !== root && !candidate.startsWith(`${root}${path.sep}`)) return null;
  return candidate;
}

function proxyRequest(request, response, target, requestUrl) {
  const transport = target.protocol === "https:" ? https : http;
  const headers = { ...request.headers, host: target.host };
  delete headers.connection;

  const upstream = transport.request(
    {
      protocol: target.protocol,
      hostname: target.hostname,
      port: target.port,
      method: request.method,
      path: buildUpstreamPath(target, requestUrl),
      headers,
    },
    (upstreamResponse) => {
      response.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.headers);
      upstreamResponse.pipe(response);
    },
  );
  upstream.on("error", (error) => {
    if (!response.headersSent) {
      response.writeHead(502, { "content-type": "text/plain; charset=utf-8" });
    }
    response.end(`Backend proxy failed: ${error.message}`);
  });
  request.pipe(upstream);
}

function serveFile(request, response, filePath) {
  fs.stat(filePath, (error, stats) => {
    if (error || !stats.isFile()) {
      response.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
      response.end("Not found");
      return;
    }

    response.writeHead(200, {
      "content-length": stats.size,
      "content-type": CONTENT_TYPES[path.extname(filePath).toLowerCase()]
        ?? "application/octet-stream",
      "referrer-policy": "same-origin",
      "x-content-type-options": "nosniff",
      "x-frame-options": "DENY",
    });
    if (request.method === "HEAD") {
      response.end();
      return;
    }
    fs.createReadStream(filePath).pipe(response);
  });
}

export function startExportServer(options = {}) {
  const exportRoot = path.resolve(options.exportRoot ?? "out");
  const host = options.host ?? process.env.HOST ?? "127.0.0.1";
  const port = Number(options.port ?? process.env.PORT ?? "3008");
  const backend = new URL(
    options.backendUrl
      ?? process.env.NEXT_PUBLIC_API_URL
      ?? "http://127.0.0.1:17177",
  );
  const allowRemote = options.allowRemote ?? TRUE_VALUES.has(
    String(process.env.ENMOTION_ALLOW_REMOTE_FRONTEND ?? "").trim().toLowerCase(),
  );
  const allowRemoteDiagnostics = options.allowRemoteDiagnostics ?? TRUE_VALUES.has(
    String(process.env.ENMOTION_ENABLE_REMOTE_DIAGNOSTICS ?? "").trim().toLowerCase(),
  );
  validateServerBind(host, allowRemote);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`Invalid PORT: ${options.port ?? process.env.PORT}`);
  }
  if (!['http:', 'https:'].includes(backend.protocol)) {
    throw new Error(`Unsupported backend protocol: ${backend.protocol}`);
  }
  if (backend.username || backend.password || backend.search || backend.hash) {
    throw new Error("Backend URL must contain only an HTTP(S) origin and optional path prefix.");
  }
  if (!fs.existsSync(path.join(exportRoot, "index.html"))) {
    throw new Error("Static export not found. Run npm run build before npm start.");
  }

  const server = http.createServer((request, response) => {
    const requestUrl = new URL(request.url ?? "/", `http://${request.headers.host ?? host}`);
    if (shouldProxyPath(requestUrl.pathname)) {
      if (
        isDiagnosticPath(requestUrl.pathname)
        && !isLoopbackAddress(request.socket.remoteAddress)
        && !allowRemoteDiagnostics
      ) {
        response.writeHead(404, { "content-type": "application/json; charset=utf-8" });
        response.end('{"detail":"Not found"}');
        return;
      }
      proxyRequest(request, response, backend, requestUrl);
      return;
    }
    if (requestUrl.pathname === "/") {
      response.writeHead(302, { location: "/static/index.html" });
      response.end();
      return;
    }

    const filePath = resolveStaticPath(exportRoot, requestUrl.pathname);
    if (!filePath) {
      response.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
      response.end("Not found");
      return;
    }
    serveFile(request, response, filePath);
  });

  server.listen(port, host, () => {
    console.log(`EnMotion static export: http://${host}:${port}/static/index.html`);
    console.log(`Backend proxy: ${backend.origin}`);
  });
  return server;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const server = startExportServer();
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.on(signal, () => server.close(() => process.exit(0)));
  }
}
