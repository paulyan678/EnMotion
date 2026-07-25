const http = require('http');
const { spawn } = require('child_process');

const port = Number(process.env.PORT || '3008');
if (!Number.isInteger(port) || port < 1 || port > 65535) {
  console.error(`[open] Invalid PORT: ${process.env.PORT}`);
  process.exit(1);
}

const url = `http://127.0.0.1:${port}`;

function probeFrontend() {
  return new Promise((resolve) => {
    const request = http.get(url, { timeout: 1000 }, (response) => {
      response.resume();
      resolve(response.statusCode >= 200 && response.statusCode < 500);
    });
    request.on('timeout', () => request.destroy());
    request.on('error', () => resolve(false));
  });
}

async function waitForFrontend(timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await probeFrontend()) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Frontend did not become ready at ${url} within ${timeoutMs}ms`);
}

function openBrowser() {
  if (process.env.ENMOTION_OPEN_BROWSER === '0') {
    console.log(`[open] Frontend ready at ${url}; automatic browser launch is disabled.`);
    return;
  }

  let command;
  let args;
  if (process.platform === 'win32') {
    command = 'cmd.exe';
    args = ['/d', '/s', '/c', 'start', '""', url];
  } else if (process.platform === 'darwin') {
    command = 'open';
    args = [url];
  } else {
    command = 'xdg-open';
    args = [url];
  }

  const opener = spawn(command, args, { detached: true, stdio: 'ignore' });
  opener.on('error', (error) => {
    console.warn(`[open] Could not open a browser automatically: ${error.message}`);
  });
  opener.unref();
}

async function main() {
  await waitForFrontend();
  console.log(`[open] EnMotion frontend ready at ${url}`);
  console.log('[open] Backend expected at http://127.0.0.1:17177');
  openBrowser();
}

if (require.main === module) {
  main().catch((error) => {
    console.error(`[open] ${error.message}`);
    process.exitCode = 1;
  });
}

module.exports = { openBrowser, probeFrontend, waitForFrontend };
