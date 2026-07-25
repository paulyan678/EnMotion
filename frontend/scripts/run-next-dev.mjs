import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

function readProjectEnvSource() {
  try {
    return fs.readFileSync(path.resolve("..", ".env"), "utf8");
  } catch (error) {
    if (error?.code === "ENOENT") return "";
    throw error;
  }
}

/**
 * @param {Record<string, string | undefined>} [baseEnv]
 * @param {string} [envSource]
 */
export function resolveBackendPort(baseEnv = process.env, envSource = readProjectEnvSource()) {
  const match = envSource.match(/^\s*(?:export\s+)?API_PORT\s*=\s*(.*?)\s*$/m);
  const fileValue = match?.[1]?.replace(/^(?:"(.*)"|'(.*)')$/, "$1$2");
  const rawPort = baseEnv.NEXT_PUBLIC_BACKEND_PORT || baseEnv.API_PORT || fileValue || "17177";
  const port = Number(rawPort);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`Invalid backend port: ${rawPort}`);
  }
  return String(port);
}

/**
 * @param {Record<string, string | undefined>} [baseEnv]
 * @param {NodeJS.Platform} [platform]
 */
export function buildNextDevEnv(
  baseEnv = process.env,
  platform = process.platform,
) {
  const env = { ...baseEnv };
  env.NEXT_PUBLIC_BACKEND_PORT = resolveBackendPort(baseEnv);

  // Watchpack's native watcher can hit EMFILE on large macOS workspaces.
  // Polling trades a little CPU for a much more stable dev server.
  if (platform === "darwin") {
    if (!env.WATCHPACK_POLLING) {
      env.WATCHPACK_POLLING = "true";
    }
    if (env.WATCHPACK_POLLING === "true" && !env.WATCHPACK_POLLING_INTERVAL) {
      env.WATCHPACK_POLLING_INTERVAL = "1000";
    }
  }

  return env;
}

export function runNextDev(args = process.argv.slice(2)) {
  const env = buildNextDevEnv();
  const nextEntry = path.resolve("node_modules", "next", "dist", "bin", "next");
  const hasExplicitPortArg = args.some(
    (arg, index) =>
      arg === "--port" ||
      arg === "-p" ||
      arg.startsWith("--port=") ||
      (index > 0 && (args[index - 1] === "--port" || args[index - 1] === "-p")),
  );
  const resolvedArgs = hasExplicitPortArg
    ? args
    : ["--port", env.PORT || "3008", ...args];

  if (env.WATCHPACK_POLLING === "true") {
    console.log(
      "[dev] Enabling Watchpack polling on macOS to avoid EMFILE watcher failures in this workspace.",
    );
  }

  const child = spawn(process.execPath, [nextEntry, "dev", ...resolvedArgs], {
    stdio: "inherit",
    env,
  });

  child.on("exit", (code, signal) => {
    if (signal) {
      process.kill(process.pid, signal);
      return;
    }
    process.exit(code ?? 1);
  });

  return child;
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  runNextDev();
}
