const { execFileSync, spawnSync } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const os = require('os');

const root = path.join(__dirname, '..');
const venv = path.join(root, '.venv');

const frontend = path.join(root, 'frontend');
const frontendModules = path.join(frontend, 'node_modules');
const npmCommand = os.platform() === 'win32' ? 'npm.cmd' : 'npm';

const MINIMUM_PYTHON = [3, 11];

function assertNodeVersion() {
  const [major, minor] = process.versions.node.split('.').map(Number);
  if (major !== 24 || minor < 11) {
    throw new Error(
      `Node.js 24.11 or newer (but below 25) is required (found ${process.versions.node}). `
      + 'Install a supported Node.js 24 LTS release before running the development setup.',
    );
  }
}

function run(command, args, options = {}) {
  execFileSync(command, args, {
    stdio: 'inherit',
    cwd: options.cwd || root,
    timeout: options.timeout,
  });
}

function findPython() {
  const candidates = process.env.PYTHON ? [process.env.PYTHON] : ['python3', 'python'];
  for (const candidate of candidates) {
    const result = spawnSync(
      candidate,
      ['-c', 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'],
      { encoding: 'utf8' },
    );
    if (result.status !== 0) continue;
    const [major, minor] = result.stdout.trim().split('.').map(Number);
    if (
      major > MINIMUM_PYTHON[0]
      || (major === MINIMUM_PYTHON[0] && minor >= MINIMUM_PYTHON[1])
    ) {
      return candidate;
    }
  }
  throw new Error('Python 3.11 or newer is required but was not found on PATH.');
}

function assertPythonVersion(pythonPath) {
  const result = spawnSync(
    pythonPath,
    [
      '-c',
      'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)',
    ],
    { stdio: 'ignore' },
  );
  if (result.status !== 0) {
    throw new Error(
      `The virtual environment at ${venv} must use Python 3.11 or newer. `
      + 'Recreate .venv with a supported Python interpreter.',
    );
  }
}

function fingerprint(paths) {
  const hash = crypto.createHash('sha256');
  for (const filePath of paths) hash.update(fs.readFileSync(filePath));
  return hash.digest('hex');
}

function sentinelMatches(sentinelPath, expected) {
  return fs.existsSync(sentinelPath)
    && fs.readFileSync(sentinelPath, 'utf8').trim() === expected;
}

function main() {
  console.log('[setup] Checking environment...');
  assertNodeVersion();

  if (!fs.existsSync(venv)) {
    console.log('[setup] Creating Python virtual environment...');
    run(findPython(), ['-m', 'venv', venv]);
  }

  const venvPython = os.platform() === 'win32'
    ? path.join(venv, 'Scripts', 'python.exe')
    : path.join(venv, 'bin', 'python');
  assertPythonVersion(venvPython);
  const requirementsPath = path.join(root, 'requirements.txt');
  const pythonFingerprint = fingerprint([requirementsPath]);
  const pythonSentinel = path.join(venv, '.enmotion-requirements-sha256');
  if (!sentinelMatches(pythonSentinel, pythonFingerprint)) {
    console.log('[setup] Installing Python dependencies...');
    run(venvPython, ['-m', 'pip', 'install', '-r', requirementsPath]);
    fs.writeFileSync(pythonSentinel, `${pythonFingerprint}\n`, 'utf8');
  }

  const frontendFingerprint = fingerprint([
    path.join(frontend, 'package.json'),
    path.join(frontend, 'package-lock.json'),
  ]);
  const frontendSentinel = path.join(frontendModules, '.enmotion-deps-sha256');
  if (!fs.existsSync(frontendModules) || !sentinelMatches(frontendSentinel, frontendFingerprint)) {
    console.log('[setup] Installing frontend dependencies from package-lock.json...');
    run(npmCommand, ['ci'], { cwd: frontend });
    fs.writeFileSync(frontendSentinel, `${frontendFingerprint}\n`, 'utf8');
  }

  if (process.env.ENMOTION_PRELOAD_DEMUCS === '1') {
    console.log('[setup] Preloading the Demucs htdemucs model...');
    run(
      venvPython,
      ['-c', "from demucs.pretrained import get_model; get_model('htdemucs')"],
      { timeout: 180000 },
    );
  }

  console.log('[setup] Done.');
}

try {
  main();
} catch (error) {
  console.error(`[setup] Failed: ${error.message}`);
  process.exitCode = 1;
}
