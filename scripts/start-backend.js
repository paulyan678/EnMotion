const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

const projectRoot = path.join(__dirname, '..');
const isWin = os.platform() === 'win32';
const pythonPath = isWin
  ? path.join(projectRoot, '.venv', 'Scripts', 'python.exe')
  : path.join(projectRoot, '.venv', 'bin', 'python');

function readEnvFile(filePath) {
  if (!fs.existsSync(filePath)) return {};
  const values = {};
  for (const rawLine of fs.readFileSync(filePath, 'utf8').split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const match = line.match(/^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
    if (!match) continue;
    let value = match[2].trim();
    if (
      value.length >= 2
      && ((value.startsWith('"') && value.endsWith('"'))
        || (value.startsWith("'") && value.endsWith("'")))
    ) {
      value = value.slice(1, -1);
    }
    values[match[1]] = value;
  }
  return values;
}

function appendNoProxy(current) {
  const entries = new Set(
    String(current || '').split(',').map((entry) => entry.trim()).filter(Boolean),
  );
  // Requests/urllib3 do not interpret a leading wildcard in NO_PROXY. A
  // leading dot is the portable suffix form for every aliyuncs.com host.
  for (const entry of ['.aliyuncs.com', 'aliyuncs.com', 'localhost', '127.0.0.1']) {
    entries.add(entry);
  }
  return [...entries].join(',');
}

function startBackend() {
  const [nodeMajor, nodeMinor] = process.versions.node.split('.').map(Number);
  if (nodeMajor !== 20 || nodeMinor < 9) {
    throw new Error(
      `Node.js 20.9 or newer (but below 21) is required (found ${process.versions.node}).`,
    );
  }
  if (!fs.existsSync(pythonPath)) {
    throw new Error(
      `Python virtual environment not found at ${pythonPath}. Run npm run predev first.`,
    );
  }

  const env = {
    ...readEnvFile(path.join(projectRoot, '.env')),
    ...process.env,
  };
  env.NO_PROXY = appendNoProxy(env.NO_PROXY);
  env.no_proxy = appendNoProxy(env.no_proxy);

  const host = env.API_HOST || '127.0.0.1';
  const port = Number(env.API_PORT || '17177');
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`Invalid API_PORT: ${env.API_PORT}`);
  }

  const args = [
    '-m', 'uvicorn', 'src.apps.comic_gen.api:app',
    '--port', String(port), '--host', host,
  ];
  if (env.ENMOTION_BACKEND_RELOAD !== '0') args.push('--reload');

  console.log(`[backend] Starting at http://${host}:${port}`);
  const backend = spawn(pythonPath, args, {
    stdio: 'inherit',
    env,
    cwd: projectRoot,
  });

  backend.on('error', (error) => {
    console.error(`Failed to start backend: ${error.message}`);
    process.exit(1);
  });
  backend.on('exit', (code, signal) => {
    if (signal) process.kill(process.pid, signal);
    else process.exit(code ?? 1);
  });

  for (const signal of ['SIGINT', 'SIGTERM']) {
    process.on(signal, () => {
      if (!backend.killed) backend.kill(signal);
    });
  }
  return backend;
}

if (require.main === module) {
  try {
    startBackend();
  } catch (error) {
    console.error(`[backend] ${error.message}`);
    process.exit(1);
  }
}

module.exports = { appendNoProxy, readEnvFile, startBackend };
