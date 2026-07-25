import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("shell", "script"),
    [("sh", "start_backend.sh"), ("sh", "start_frontend.sh")],
)
def test_shell_entrypoints_parse(shell: str, script: str):
    subprocess.run([shell, "-n", str(REPO_ROOT / script)], check=True)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
@pytest.mark.parametrize(
    "script",
    ["scripts/dev-setup.js", "scripts/start-backend.js", "scripts/open-browser.js"],
)
def test_node_entrypoints_parse(script: str):
    subprocess.run(["node", "--check", str(REPO_ROOT / script)], check=True)


def test_local_backend_launchers_bind_to_loopback_by_default():
    shell_launcher = _source("start_backend.sh")
    node_launcher = _source("scripts/start-backend.js")

    assert "env.API_HOST || '127.0.0.1'" in node_launcher
    assert "0.0.0.0" not in shell_launcher
    assert "0.0.0.0" not in node_launcher
    assert "scripts/start-backend.js" in shell_launcher
    assert "Scripts', 'python.exe'" in node_launcher


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_backend_launcher_parses_env_without_overwriting_proxy_exclusions(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nAPI_HOST=127.0.0.2\nAPI_PORT='18181'\nINVALID LINE\n",
        encoding="utf-8",
    )
    script = f"""
const launcher = require({str(REPO_ROOT / 'scripts' / 'start-backend.js')!r});
const parsed = launcher.readEnvFile({str(env_file)!r});
if (parsed.API_HOST !== '127.0.0.2' || parsed.API_PORT !== '18181') process.exit(2);
const merged = launcher.appendNoProxy('example.com,localhost');
if (merged !== 'example.com,localhost,.aliyuncs.com,aliyuncs.com,127.0.0.1') process.exit(3);
"""

    subprocess.run(["node", "-e", script], check=True)


def test_direct_launchers_use_locked_dependencies_and_stable_working_directories():
    frontend_launcher = _source("start_frontend.sh")
    windows_launcher = _source("dev.bat")

    assert 'SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)' in frontend_launcher
    assert "npm ci" in frontend_launcher
    assert "npm install" not in frontend_launcher
    assert "npm ci" in windows_launcher
    assert "npm install" not in windows_launcher


def test_browser_launcher_waits_for_the_configured_frontend_without_shell_interpolation():
    source = _source("scripts/open-browser.js")

    assert "waitForFrontend" in source
    assert "process.env.PORT || '3008'" in source
    assert "ENMOTION_OPEN_BROWSER === '0'" in source
    assert "spawn(command, args" in source
    assert "exec(" not in source


def test_logging_is_idempotent_rotating_and_does_not_duplicate_file_records(tmp_path):
    log_path = tmp_path / "app.log"
    script = f"""
import logging
from pathlib import Path
import src.utils as utils

root = logging.getLogger()
for handler in list(root.handlers):
    root.removeHandler(handler)
    handler.close()
utils._LOG_MAX_BYTES = 256
utils._LOG_BACKUP_COUNT = 2
utils.setup_logging(log_file={str(log_path)!r})
utils.setup_logging(log_file={str(log_path)!r})
handlers = [h for h in root.handlers if getattr(h, '_enmotion_handler', False)]
if len(handlers) != 2: raise SystemExit(2)
for index in range(20):
    logging.getLogger('contract').warning('rotation-line-%02d-%s', index, 'x' * 80)
logging.shutdown()
files = list(Path({str(tmp_path)!r}).glob('app.log*'))
if len(files) < 2: raise SystemExit(3)
payload = ''.join(path.read_text(encoding='utf-8') for path in files)
if payload.count('rotation-line-19-') != 1: raise SystemExit(4)
"""

    subprocess.run([sys.executable, "-c", script], cwd=REPO_ROOT, check=True)
