"""EnMotion's loopback-only Python API sidecar.

The Tauri process passes a short-lived runtime configuration in one inherited
environment variable. The configuration contains a random port and 256-bit
bootstrap nonce; neither value is embedded in the application nor written to a
command line. The sidecar removes the variable immediately after reading it.
"""

from __future__ import annotations

import time

STARTUP_STARTED = time.perf_counter()

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlparse

if TYPE_CHECKING:
    from starlette.requests import Request

RUNTIME_CONFIG_ENV = "ENMOTION_DESKTOP_RUNTIME_CONFIG"
RUNTIME_SCHEMA_VERSION = 1
COOKIE_NAME = "enmotion_desktop_session"
NONCE_HEADER = "X-EnMotion-Desktop-Nonce"
LOCAL_API_NONCE_HEADER = "X-EnMotion-Local-Nonce"
MAX_RUNTIME_CONFIG_BYTES = 16 * 1024
UPDATE_METADATA_FILES = (
    "projects.json",
    "series.json",
    "library_assets.json",
    "playground_history.json",
    "playground_templates.json",
    "config.json",
)
ACTIVE_JOB_STATES = {"pending", "queued", "processing", "running", "retrying"}
SUPPORTED_UPDATE_TARGETS = {
    ("darwin", "aarch64"),
    ("darwin", "x86_64"),
    ("windows", "x86_64"),
}
BASE_CSP = (
    "default-src 'self'; connect-src 'self' ipc: http://ipc.localhost; "
    "img-src 'self' data: blob: https:; media-src 'self' blob: https:; "
    "style-src 'self' 'unsafe-inline'; {script_policy}; "
    "font-src 'self' data:; object-src 'none'; base-uri 'self'; "
    "frame-ancestors 'none'; form-action 'self'"
)


def report_startup_phase(phase: str) -> None:
    """Emit bounded phase timings without request data or credentials."""

    elapsed_ms = round((time.perf_counter() - STARTUP_STARTED) * 1000)
    print(f"[startup] phase={phase} elapsed_ms={elapsed_ms}", file=sys.stderr, flush=True)


@dataclass(frozen=True)
class RuntimeConfig:
    schema_version: int
    host: str
    port: int
    nonce: str
    static_dir: Path
    data_dir: Path
    output_dir: Path
    current_version: str
    control_plane_url: str

    @property
    def origin(self) -> str:
        return f"http://{self.host}:{self.port}"


class UpdateBarrier:
    """Atomically stop new local mutations while installation is prepared."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_mutations: set[int] = set()
        self._next_token = 0
        self._preparing = False

    def enter_mutation(self) -> int:
        with self._lock:
            if self._preparing:
                raise RuntimeError("application update is being installed")
            self._next_token += 1
            token = self._next_token
            self._active_mutations.add(token)
            return token

    def leave_mutation(self, token: int) -> None:
        with self._lock:
            self._active_mutations.discard(token)

    def begin_prepare(self) -> int:
        with self._lock:
            if self._preparing:
                raise RuntimeError("application update is already being prepared")
            self._preparing = True
            return len(self._active_mutations)

    def cancel_prepare(self) -> None:
        with self._lock:
            self._preparing = False


def _decode_base64url(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(value + padding)


def parse_runtime_config(encoded: str) -> RuntimeConfig:
    """Decode and strictly validate the inherited Tauri runtime contract."""

    if not encoded or len(encoded) > MAX_RUNTIME_CONFIG_BYTES:
        raise ValueError("desktop runtime configuration is missing or too large")
    try:
        raw = _decode_base64url(encoded)
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("desktop runtime configuration is not valid base64 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("desktop runtime configuration must be an object")

    expected_keys = {
        "schemaVersion",
        "host",
        "port",
        "nonce",
        "staticDir",
        "dataDir",
        "outputDir",
        "currentVersion",
        "controlPlaneUrl",
    }
    if set(payload) != expected_keys:
        raise ValueError("desktop runtime configuration has unexpected fields")
    if payload["schemaVersion"] != RUNTIME_SCHEMA_VERSION:
        raise ValueError("unsupported desktop runtime schema")
    if payload["host"] != "127.0.0.1":
        raise ValueError("desktop sidecar may only bind to 127.0.0.1")
    if not isinstance(payload["port"], int) or not 1024 <= payload["port"] <= 65535:
        raise ValueError("desktop sidecar port is outside the allowed range")

    nonce = payload["nonce"]
    if (
        not isinstance(nonce, str)
        or len(nonce) != 64
        or any(character not in "0123456789abcdef" for character in nonce)
    ):
        raise ValueError("desktop bootstrap nonce must be 256-bit lowercase hex")

    version = payload["currentVersion"]
    if not isinstance(version, str) or not version.strip():
        raise ValueError("desktop version is required")
    raw_control_plane_url = payload["controlPlaneUrl"]
    if not isinstance(raw_control_plane_url, str):
        raise ValueError("controlPlaneUrl must be a string")
    control_plane_url = raw_control_plane_url.strip().rstrip("/")
    parsed_control_plane_url = urlparse(control_plane_url)
    loopback = parsed_control_plane_url.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        parsed_control_plane_url.scheme not in {"http", "https"}
        or not parsed_control_plane_url.hostname
        or (parsed_control_plane_url.scheme != "https" and not loopback)
        or parsed_control_plane_url.username
        or parsed_control_plane_url.password
        or parsed_control_plane_url.path not in {"", "/"}
        or parsed_control_plane_url.params
        or parsed_control_plane_url.query
        or parsed_control_plane_url.fragment
    ):
        raise ValueError("controlPlaneUrl must be a secure absolute origin")

    paths: dict[str, Path] = {}
    for key in ("staticDir", "dataDir", "outputDir"):
        value = payload[key]
        if not isinstance(value, str) or not value:
            raise ValueError(f"{key} must be a non-empty absolute path")
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError(f"{key} must be absolute")
        paths[key] = path.resolve()

    if not (paths["staticDir"] / "index.html").is_file():
        raise ValueError("staged frontend is missing static/index.html")

    return RuntimeConfig(
        schema_version=payload["schemaVersion"],
        host=payload["host"],
        port=payload["port"],
        nonce=nonce,
        static_dir=paths["staticDir"],
        data_dir=paths["dataDir"],
        output_dir=paths["outputDir"],
        current_version=version.strip(),
        control_plane_url=control_plane_url,
    )


def load_runtime_config_from_environment() -> RuntimeConfig:
    encoded = os.environ.pop(RUNTIME_CONFIG_ENV, "")
    return parse_runtime_config(encoded)


def validated_update_manifest_url(value: Any, control_plane_url: str) -> str:
    """Accept only an HTTPS capability URL on the configured control plane."""

    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("update session response has no manifest URL")
    candidate = urlparse(value)
    control_plane = urlparse(control_plane_url)

    def effective_port(parsed: Any) -> int | None:
        if parsed.port is not None:
            return parsed.port
        return 443 if parsed.scheme == "https" else 80 if parsed.scheme == "http" else None

    parts = candidate.path.split("/")
    token = parts[5] if len(parts) == 7 else ""
    if (
        candidate.scheme != "https"
        or candidate.hostname != control_plane.hostname
        or effective_port(candidate) != effective_port(control_plane)
        or candidate.username
        or candidate.password
        or candidate.params
        or candidate.query
        or candidate.fragment
        or parts[:5] != ["", "api", "v1", "releases", "session"]
        or parts[6:] != ["manifest"]
        or not 32 <= len(token) <= 256
        or any(
            character not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_-"
            for character in token
        )
    ):
        raise ValueError("update session returned an untrusted manifest URL")
    return value


def session_cookie_value(nonce: str) -> str:
    return hashlib.sha256(f"enmotion-desktop-cookie-v1:{nonce}".encode()).hexdigest()


def desktop_session_response(cookie_value: str) -> Any:
    """Set the loopback session before redirecting from the Tauri bootstrap page.

    The navigation starts on ``tauri://localhost``. ``SameSite=Strict`` cookies
    set during that cross-site top-level navigation are withheld by WebKit on
    the immediate redirect, so use ``Lax`` for this one loopback-only cookie.
    All mutating requests remain protected by same-origin checks.
    """

    from fastapi.responses import RedirectResponse

    response = RedirectResponse("/static/index.html", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        cookie_value,
        httponly=True,
        secure=False,
        samesite="lax",
        path="/",
    )
    return response


def local_api_nonce(nonce: str) -> str:
    return hmac.new(
        bytes.fromhex(nonce),
        b"enmotion-local-api-nonce-v1",
        hashlib.sha256,
    ).hexdigest()


def headers_with_local_api_nonce(
    headers: list[tuple[bytes, bytes]],
    nonce: str,
) -> list[tuple[bytes, bytes]]:
    """Replace an untrusted local nonce while preserving streaming headers."""

    header_name = LOCAL_API_NONCE_HEADER.lower().encode("ascii")
    forwarded = [
        (name, value)
        for name, value in headers
        if name.lower() != header_name
    ]
    forwarded.append((header_name, local_api_nonce(nonce).encode("ascii")))
    return forwarded


def readiness_proof(config: RuntimeConfig) -> str:
    message = f"enmotion-desktop-ready-v1:{config.current_version}:{config.port}".encode()
    return hmac.new(bytes.fromhex(config.nonce), message, hashlib.sha256).hexdigest()


class _InlineScriptCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._active = False
        self._chunks: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        self._active = not any(name.lower() == "src" for name, _ in attrs)
        self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._active:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._active:
            script = "".join(self._chunks)
            if script:
                self.scripts.append(script)
            self._active = False
            self._chunks = []


def static_content_security_policies(static_dir: Path) -> dict[str, str]:
    """Build exact script hashes for every exported HTML document."""

    policies: dict[str, str] = {}
    for html_path in sorted(static_dir.rglob("*.html")):
        parser = _InlineScriptCollector()
        parser.feed(html_path.read_text(encoding="utf-8"))
        hashes = {
            "'sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() + "'"
            for script in parser.scripts
        }
        script_policy = "script-src 'self'"
        if hashes:
            script_policy += " " + " ".join(sorted(hashes))
        policies[html_path.relative_to(static_dir).as_posix()] = BASE_CSP.format(
            script_policy=script_policy
        )
    return policies


def csp_for_request(path: str, policies: dict[str, str]) -> str:
    relative = path.removeprefix("/static").lstrip("/")
    candidates = [
        relative or "index.html",
        f"{relative}.html" if relative else "",
        f"{relative.rstrip('/')}/index.html" if relative else "",
    ]
    for candidate in candidates:
        if candidate and candidate in policies:
            return policies[candidate]
    return BASE_CSP.format(script_policy="script-src 'self'")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        if os.name != "nt":
            os.fchmod(file_descriptor, 0o600)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _job_status(value: Any) -> str:
    if isinstance(value, dict):
        status = value.get("status", "")
    else:
        status = getattr(value, "status", "")
    raw = getattr(status, "value", status)
    return str(raw or "").strip().lower()


def active_update_blockers(pipeline: Any) -> list[str]:
    blockers: list[str] = []
    for collection_name in ("asset_generation_tasks", "video_generation_tasks"):
        collection = getattr(pipeline, collection_name, None)
        if not isinstance(collection, dict):
            continue
        for job_id, value in collection.items():
            status = _job_status(value)
            if status in ACTIVE_JOB_STATES:
                blockers.append(f"{collection_name}:{job_id}:{status}")
    scripts = getattr(pipeline, "scripts", None)
    if isinstance(scripts, dict):
        for script_id, script in scripts.items():
            tasks = getattr(script, "video_tasks", None)
            if tasks is None and isinstance(script, dict):
                tasks = script.get("video_tasks")
            for task in tasks or []:
                status = _job_status(task)
                if status not in ACTIVE_JOB_STATES:
                    continue
                task_id = (
                    task.get("id", "unknown")
                    if isinstance(task, dict)
                    else getattr(task, "id", "unknown")
                )
                blockers.append(f"scripts:{script_id}:video_tasks:{task_id}:{status}")
    return sorted(blockers)


def active_desktop_pipelines(api_module: Any) -> list[Any]:
    """Snapshot loaded local workspace pipelines without requiring a request tenant."""

    registry = getattr(api_module, "_workspace_pipelines", None)
    if registry is None:
        return []
    lock = getattr(registry, "_lock", None)

    def snapshot() -> list[Any]:
        stores = (getattr(registry, "_writer_pipelines", {}),)
        return [
            value[0]
            for store in stores
            if isinstance(store, dict)
            for value in store.values()
            if isinstance(value, tuple) and value
        ]

    pipelines = snapshot() if lock is None else _snapshot_under_lock(lock, snapshot)
    unique: dict[int, Any] = {id(pipeline): pipeline for pipeline in pipelines}
    return list(unique.values())


def active_playground_blockers() -> list[str]:
    """Snapshot unfinished local Playground work across employee workspaces."""

    from src.apps.playground.api import active_playground_generation_blockers

    return [
        str(blocker) for blocker in active_playground_generation_blockers() if str(blocker).strip()
    ]


def _snapshot_under_lock(lock: Any, callback: Callable[[], list[Any]]) -> list[Any]:
    with lock:
        return callback()


def flush_pipeline_metadata(pipeline: Any) -> None:
    """Flush the local metadata stores before the application is replaced."""

    save_lock = getattr(pipeline, "_save_lock", None)

    def flush() -> None:
        for method_name in ("_save_data", "_save_series_data", "_save_library_data"):
            method = getattr(pipeline, method_name, None)
            if callable(method):
                method()

    if save_lock is None:
        flush()
    else:
        with save_lock:
            flush()


def create_update_backup(config: RuntimeConfig, *, target_version: str) -> dict[str, Any]:
    """Publish an atomic metadata backup without touching generated media."""

    pending_path = config.data_dir / ".desktop-update-pending.json"
    if pending_path.exists():
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
        if (
            pending.get("state") == "prepared"
            and pending.get("fromVersion") == config.current_version
            and pending.get("targetVersion") == target_version
            and Path(str(pending.get("backupDirectory", ""))).is_dir()
        ):
            return {
                "transactionId": pending.get("transactionId"),
                "backupDirectory": pending.get("backupDirectory"),
                "preservedPaths": [str(config.data_dir), str(config.output_dir)],
                "reused": True,
            }
        raise RuntimeError(
            "an earlier update transaction is still pending; recover or commit it first"
        )

    transaction_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(6)
    )
    backup_root = config.data_dir / ".update-backups"
    staging = backup_root / f".{transaction_id}.staging"
    published = backup_root / transaction_id
    staging.mkdir(parents=True, exist_ok=False)
    if os.name != "nt":
        os.chmod(staging, 0o700)

    copied: list[str] = []
    try:
        for filename in UPDATE_METADATA_FILES:
            source = config.data_dir / filename
            if source.is_file():
                shutil.copy2(source, staging / filename)
                copied.append(filename)
        workspace_root = config.output_dir / "workspaces"
        if workspace_root.is_dir():
            for workspace in sorted(workspace_root.iterdir()):
                metadata_root = workspace / "output"
                if (
                    not workspace.is_dir()
                    or workspace.is_symlink()
                    or not metadata_root.is_dir()
                    or metadata_root.is_symlink()
                ):
                    continue
                for filename in UPDATE_METADATA_FILES:
                    source = metadata_root / filename
                    if not source.is_file() or source.is_symlink():
                        continue
                    relative = Path("workspaces") / workspace.name / filename
                    destination = staging / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                    copied.append(relative.as_posix())
        backup_manifest = {
            "contractVersion": 1,
            "transactionId": transaction_id,
            "fromVersion": config.current_version,
            "targetVersion": target_version,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "files": copied,
            "outputDirectory": str(config.output_dir),
        }
        _atomic_json(staging / "manifest.json", backup_manifest)
        os.replace(staging, published)
        _atomic_json(
            pending_path,
            {
                **backup_manifest,
                "backupDirectory": str(published),
                "state": "prepared",
            },
        )
        return {
            "transactionId": transaction_id,
            "backupDirectory": str(published),
            "preservedPaths": [str(config.data_dir), str(config.output_dir)],
        }
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def commit_update_transaction(config: RuntimeConfig) -> dict[str, Any]:
    pending_path = config.data_dir / ".desktop-update-pending.json"
    if not pending_path.is_file():
        return {"committed": False, "reason": "no-pending-transaction"}
    payload = json.loads(pending_path.read_text(encoding="utf-8"))
    if payload.get("targetVersion") != config.current_version:
        return {
            "committed": False,
            "reason": "version-does-not-match-target",
            "targetVersion": payload.get("targetVersion"),
        }
    payload["state"] = "healthy"
    payload["committedAt"] = datetime.now(timezone.utc).isoformat()
    _atomic_json(config.data_dir / ".desktop-update-last-good.json", payload)
    pending_path.unlink()
    return {
        "committed": True,
        "transactionId": payload.get("transactionId"),
    }


def resolve_packaged_demucs_worker() -> Path | None:
    """Locate the optional-audio worker beside the frozen core sidecar."""

    configured = os.getenv("ENMOTION_DEMUCS_WORKER", "").strip()
    if configured:
        candidate = Path(configured).expanduser().resolve()
        return candidate if candidate.is_file() else None
    if not getattr(sys, "frozen", False):
        return None
    executable = Path(sys.executable).resolve()
    suffix = ".exe" if os.name == "nt" else ""
    candidates = [executable.with_name(f"enmotion-demucs-worker{suffix}")]
    core_prefix = "enmotion-sidecar"
    if executable.name.startswith(f"{core_prefix}-"):
        target_suffix = executable.name[len(core_prefix) :]
        candidates.append(executable.with_name(f"enmotion-demucs-worker{target_suffix}"))
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def configure_runtime_environment(config: RuntimeConfig) -> None:
    """Apply the desktop runtime contract before importing application modules."""

    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    (config.data_dir / "logs").mkdir(parents=True, exist_ok=True)
    os.environ["ENMOTION_DATA_DIR"] = str(config.data_dir)
    os.environ["ENMOTION_LOG_DIR"] = str(config.data_dir / "logs")
    os.environ["ENMOTION_OUTPUT_DIR"] = str(config.output_dir)
    os.environ["ENMOTION_WORKSPACE_ROOT"] = str(config.output_dir / "workspaces")
    os.environ["ENMOTION_DESKTOP_RUNTIME"] = "1"
    os.environ["ENMOTION_SERVER_MODE"] = "0"
    os.environ["ENMOTION_HYBRID_MODE"] = "1"
    os.environ["ENMOTION_DEPLOYMENT_MODE"] = "hybrid"
    os.environ["ENMOTION_CONTROL_PLANE_URL"] = config.control_plane_url
    os.environ["ENMOTION_SIDECAR_NONCE"] = local_api_nonce(config.nonce)
    os.environ.setdefault("ENMOTION_PRELOAD_DEMUCS", "0")
    packaged_demucs_worker = resolve_packaged_demucs_worker()
    if packaged_demucs_worker is not None:
        os.environ["ENMOTION_DEMUCS_WORKER"] = str(packaged_demucs_worker)
    os.environ["API_PORT"] = str(config.port)
    os.chdir(config.data_dir)
    report_startup_phase("runtime-configured")


def configure_core_application(config: RuntimeConfig) -> tuple[Any, Any]:
    """Load the existing API outside the desktop shell's critical launch path."""

    from src.apps.comic_gen.pipeline import ComicGenPipeline

    report_startup_phase("pipeline-module-imported")
    original_initializer: Callable[..., None] = ComicGenPipeline.__init__

    def desktop_initializer(self: Any, pipeline_config: dict[str, Any] | None = None) -> None:
        effective = dict(pipeline_config or {})
        effective.setdefault("output_root", str(config.output_dir))
        effective.setdefault("metadata_root", str(config.data_dir))
        original_initializer(self, effective)

    ComicGenPipeline.__init__ = desktop_initializer
    try:
        from src.apps.comic_gen import api as api_module
    finally:
        ComicGenPipeline.__init__ = original_initializer

    report_startup_phase("api-module-imported")
    # The core application is mounted behind the desktop shell instead of
    # owning the process lifespan, so its standalone startup handlers are not
    # invoked by Starlette. Bootstrap immutable read models explicitly before
    # the first hybrid GET; otherwise an upgraded workspace with live metadata
    # but no prior snapshot would render as unavailable until its next write.
    api_module._initialize_workspace_read_models()
    report_startup_phase("workspace-read-models-initialized")
    core_app = api_module.app
    report_startup_phase("core-application-configured")
    return core_app, api_module


class DeferredCoreApplication:
    """Load the generation API in parallel with the visible desktop shell."""

    def __init__(self, loader: Callable[[], tuple[Any, Any]]) -> None:
        self._loader = loader
        self._ready = threading.Event()
        self._start_lock = threading.Lock()
        self._started = False
        self._application: Any | None = None
        self._api_module: Any | None = None
        self._error: Exception | None = None

    @property
    def ready(self) -> bool:
        return self._ready.is_set() and self._error is None

    def start(self) -> None:
        with self._start_lock:
            if self._started:
                return
            self._started = True
            report_startup_phase("core-initialization-started")
            threading.Thread(
                target=self._load,
                name="enmotion-core-loader",
                daemon=True,
            ).start()

    def _load(self) -> None:
        try:
            self._application, self._api_module = self._loader()
        except Exception as exc:
            self._error = exc
            print(
                f"EnMotion core initialization failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
        finally:
            self._ready.set()
        if self._error is None:
            report_startup_phase("core-initialization-complete")

    def wait_for_api_module(self, timeout: float = 120.0) -> Any:
        self.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("EnMotion creation service is still starting")
        if self._error is not None:
            raise RuntimeError("EnMotion creation service failed to start") from self._error
        return self._api_module

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Any],
        send: Callable[..., Any],
    ) -> None:
        import asyncio

        self.start()
        await asyncio.to_thread(self._ready.wait)
        if self._application is not None:
            await self._application(scope, receive, send)
            return
        if scope.get("type") == "websocket":
            await send({"type": "websocket.close", "code": 1011})
            return
        body = b'{"detail":"EnMotion creation service failed to start"}'
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"retry-after", b"2"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def create_application(config: RuntimeConfig) -> Any:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse, Response
    from fastapi.staticfiles import StaticFiles
    from starlette.requests import Request as StarletteRequest

    # FastAPI resolves postponed annotations against module globals when routes
    # are registered. Keep Starlette off the frozen executable's import path
    # until the shell is actually being constructed.
    globals()["Request"] = StarletteRequest

    configure_runtime_environment(config)
    deferred_core = DeferredCoreApplication(lambda: configure_core_application(config))
    desktop_app = FastAPI(
        title="EnMotion Desktop Runtime",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    cookie_value = session_cookie_value(config.nonce)
    bootstrap_lock = threading.Lock()
    bootstrap_used = False
    update_barrier = UpdateBarrier()
    expected_host = f"{config.host}:{config.port}"
    static_csp = static_content_security_policies(config.static_dir)
    report_startup_phase("static-content-validated")
    hybrid_lock = threading.Lock()
    hybrid_services: tuple[Any, Any, Any, Any, Any] | None = None

    def get_hybrid_services() -> tuple[Any, Any, Any, Any, Any]:
        nonlocal hybrid_services
        if hybrid_services is not None:
            return hybrid_services
        with hybrid_lock:
            if hybrid_services is None:
                from src.apps.hybrid.client import ControlPlaneClient, ControlPlaneError
                from src.apps.hybrid.config import HybridSettings
                from src.apps.hybrid.session import HybridUser, session_vault

                settings = HybridSettings.from_env()
                hybrid_services = (
                    settings,
                    ControlPlaneClient(settings),
                    ControlPlaneError,
                    HybridUser,
                    session_vault,
                )
        return hybrid_services

    def valid_nonce_header(request: Request) -> bool:
        value = request.headers.get(NONCE_HEADER, "")
        return hmac.compare_digest(value, config.nonce)

    def employee_remote(request: Request) -> Any:
        (
            hybrid_settings,
            control_plane,
            ControlPlaneError,
            _HybridUser,
            session_vault,
        ) = get_hybrid_services()
        local = session_vault.get_local(request.cookies.get(hybrid_settings.session_cookie_name))
        if local is None:
            raise HTTPException(status_code=401, detail="Sign in to check for updates")
        try:
            remote = session_vault.ensure_fresh(
                local.user.id,
                control_plane.refresh,
            )
        except RuntimeError as exc:
            raise HTTPException(
                status_code=401,
                detail="Sign in to check for updates",
            ) from exc
        except ControlPlaneError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return local, remote

    def confirmed_employee_remote(request: Request) -> Any:
        """Require the control plane to confirm that this employee is active now."""

        local, remote = employee_remote(request)
        (
            _hybrid_settings,
            control_plane,
            ControlPlaneError,
            HybridUser,
            _session_vault,
        ) = get_hybrid_services()
        try:
            payload = control_plane.get_json("/api/v1/auth/session", remote)
            confirmed = HybridUser.from_payload(payload)
        except ValueError as exc:
            raise HTTPException(
                status_code=502,
                detail="The EnMotion account service returned an invalid session",
            ) from exc
        except ControlPlaneError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        if confirmed.id != local.user.id:
            raise HTTPException(status_code=401, detail="Employee session changed")
        return local, remote

    @desktop_app.middleware("http")
    async def protect_loopback(request: Request, call_next: Callable[..., Any]) -> Any:
        if request.headers.get("host", "") != expected_host:
            return JSONResponse({"detail": "invalid loopback host"}, status_code=400)

        path = request.url.path
        is_bootstrap = path.startswith("/_desktop/bootstrap/")
        is_privileged = path.startswith("/_desktop/updater/") or path in {
            "/_desktop/ready",
            "/_desktop/prepare-update",
            "/_desktop/commit-update",
            "/_desktop/cancel-update",
        }
        if is_privileged:
            if not valid_nonce_header(request):
                return JSONResponse({"detail": "desktop nonce required"}, status_code=401)
        elif not is_bootstrap:
            cookie = request.cookies.get(COOKIE_NAME, "")
            if not hmac.compare_digest(cookie, cookie_value):
                return JSONResponse({"detail": "desktop session required"}, status_code=401)
            if path.startswith("/files/"):
                # Native img/video requests cannot attach the inner hybrid
                # nonce. Only after the outer HttpOnly session is validated,
                # forward the request to the mounted application with a
                # trusted nonce. Mutating the ASGI headers preserves Range,
                # conditional requests, HEAD behavior, and streamed bodies.
                request.scope["headers"] = headers_with_local_api_nonce(
                    list(request.scope.get("headers", [])),
                    config.nonce,
                )

        if request.method not in {"GET", "HEAD", "OPTIONS"} and not is_privileged:
            origin = request.headers.get("origin")
            if origin and origin != config.origin:
                return JSONResponse({"detail": "invalid request origin"}, status_code=403)

        mutation_token: int | None = None
        is_application_mutation = request.method not in {
            "GET",
            "HEAD",
            "OPTIONS",
        } and not path.startswith("/_desktop/")
        if is_application_mutation:
            try:
                mutation_token = update_barrier.enter_mutation()
            except RuntimeError:
                return JSONResponse(
                    {"detail": "EnMotion is preparing an application update"},
                    status_code=409,
                )
        try:
            response = await call_next(request)
        finally:
            if mutation_token is not None:
                update_barrier.leave_mutation(mutation_token)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        response.headers["Content-Security-Policy"] = csp_for_request(path, static_csp)
        if path == "/static/index.html" or is_bootstrap:
            response.headers["Cache-Control"] = "no-store"
        elif path.startswith("/static/_next/static/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @desktop_app.get("/_desktop/bootstrap/{nonce}", include_in_schema=False)
    async def bootstrap(request: Request, nonce: str) -> Any:
        nonlocal bootstrap_used
        existing_cookie = request.cookies.get(COOKIE_NAME, "")
        already_authenticated = hmac.compare_digest(existing_cookie, cookie_value)
        with bootstrap_lock:
            valid = hmac.compare_digest(nonce, config.nonce)
            if not valid or (bootstrap_used and not already_authenticated):
                raise HTTPException(status_code=404, detail="bootstrap not found")
            bootstrap_used = True
        return desktop_session_response(cookie_value)

    @desktop_app.get("/_desktop/ready", include_in_schema=False)
    async def ready() -> dict[str, Any]:
        return {
            "ready": True,
            "coreReady": deferred_core.ready,
            "contractVersion": RUNTIME_SCHEMA_VERSION,
            "version": config.current_version,
            "proof": readiness_proof(config),
            "outputDirectory": str(config.output_dir),
        }

    @desktop_app.get("/runtime-config.js", include_in_schema=False)
    @desktop_app.get("/static/runtime-config.js", include_in_schema=False)
    async def runtime_config() -> Any:
        payload = {
            "serverMode": False,
            "hybridMode": True,
            "apiUrl": config.origin,
            "localNonce": local_api_nonce(config.nonce),
            "updater": {"enabled": True, "channel": "stable"},
        }
        source = (
            "window.__ENMOTION_RUNTIME_CONFIG__="
            + json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
            + ";\n"
        )
        return Response(
            source,
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @desktop_app.post("/_desktop/prepare-update", include_in_schema=False)
    def prepare_update(body: dict[str, Any]) -> dict[str, Any]:
        target_version = str(body.get("targetVersion", "")).strip()
        if not target_version:
            raise HTTPException(status_code=400, detail="targetVersion is required")
        try:
            in_flight_mutations = update_barrier.begin_prepare()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            try:
                api_module = deferred_core.wait_for_api_module()
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            pipelines = active_desktop_pipelines(api_module)
            blockers = [
                blocker for pipeline in pipelines for blocker in active_update_blockers(pipeline)
            ]
            blockers.extend(active_playground_blockers())
            if in_flight_mutations:
                blockers.append(f"local_api_mutations:{in_flight_mutations}:in_progress")
            if blockers:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "generation or export work is still active",
                        "blockers": sorted(blockers),
                    },
                )
            for pipeline in pipelines:
                flush_pipeline_metadata(pipeline)
            backup = create_update_backup(config, target_version=target_version)
            return {
                "ready": True,
                "contractVersion": 1,
                **backup,
            }
        except Exception:
            update_barrier.cancel_prepare()
            raise

    @desktop_app.post("/_desktop/cancel-update", include_in_schema=False)
    def cancel_update() -> dict[str, bool]:
        update_barrier.cancel_prepare()
        return {"cancelled": True}

    @desktop_app.post("/_desktop/commit-update", include_in_schema=False)
    def commit_update(request: Request) -> dict[str, Any]:
        # The webview readiness signal is necessary but not sufficient: only
        # a currently active employee session may mark the new version healthy.
        confirmed_employee_remote(request)
        return commit_update_transaction(config)

    @desktop_app.post("/_desktop/updater/session", include_in_schema=False)
    def updater_session(body: dict[str, Any], request: Request) -> Any:
        (
            _hybrid_settings,
            control_plane,
            ControlPlaneError,
            _HybridUser,
            _session_vault,
        ) = get_hybrid_services()
        target = str(body.get("target", ""))
        arch = str(body.get("arch", ""))
        current_version = str(body.get("currentVersion", ""))
        if (target, arch) not in SUPPORTED_UPDATE_TARGETS:
            raise HTTPException(status_code=404, detail="unsupported desktop target")
        if (
            not current_version
            or len(current_version) > 120
            or any(
                character
                not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz._-+"
                for character in current_version
            )
        ):
            raise HTTPException(status_code=422, detail="invalid current version")
        _, remote = employee_remote(request)
        try:
            session = control_plane.post_json(
                "/api/v1/releases/session",
                remote,
                {
                    "target": target,
                    "arch": arch,
                    "current_version": current_version,
                    "channel": "stable",
                },
            )
        except ControlPlaneError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        try:
            manifest_url = validated_update_manifest_url(
                session.get("manifest_url") if isinstance(session, dict) else None,
                config.control_plane_url,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"The EnMotion update service returned an unsafe session: {exc}",
            ) from exc
        return JSONResponse(
            {"manifestUrl": manifest_url},
            headers={"Cache-Control": "no-store"},
        )

    desktop_app.mount(
        "/static",
        StaticFiles(directory=config.static_dir, html=True),
        name="desktop_frontend",
    )
    desktop_app.mount("/", deferred_core)
    report_startup_phase("desktop-application-created")
    deferred_core.start()
    return desktop_app


def run(config: RuntimeConfig) -> None:
    import uvicorn

    application = create_application(config)
    report_startup_phase("uvicorn-starting")
    uvicorn.run(
        application,
        host=config.host,
        port=config.port,
        log_level="info",
        log_config=None,
        access_log=False,
        reload=False,
    )


def verify_packaged_bundle() -> None:
    """Fail release CI if the frozen sidecar is missing runtime dependencies."""

    import platform

    if not getattr(sys, "frozen", False):
        raise RuntimeError("bundle verification requires a frozen sidecar")
    bundle_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    required_resources = (
        bundle_dir / "src/apps/comic_gen/style_presets.json",
        bundle_dir / "config/model_catalog/generated/model_catalog.json",
    )
    for resource in required_resources:
        if not resource.is_file():
            raise RuntimeError(f"packaged resource is missing: {resource.name}")

    import keyring
    import openai  # noqa: F401
    import oss2  # noqa: F401

    from src.apps.comic_gen import api as api_module
    from src.utils.system_check import get_ffmpeg_path

    if not getattr(api_module.app, "routes", None):
        raise RuntimeError("packaged EnMotion API has no routes")
    worker = resolve_packaged_demucs_worker()
    if worker is None:
        raise RuntimeError("packaged Demucs worker is missing")
    subprocess.run(
        [str(worker), "--verify-bundle"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=120,
    )
    credential_backend = keyring.get_keyring()
    expected_backend = {
        "Darwin": "keyring.backends.macOS",
        "Windows": "keyring.backends.Windows",
    }.get(platform.system())
    if expected_backend is None or not type(credential_backend).__module__.startswith(
        expected_backend
    ):
        raise RuntimeError("packaged OS credential-store backend is unavailable")
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("packaged FFmpeg is unavailable")
    subprocess.run(
        [ffmpeg, "-version"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=20,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="EnMotion private desktop sidecar")
    parser.add_argument(
        "--desktop-runtime",
        action="store_true",
        help="require the inherited Tauri runtime contract",
    )
    parser.add_argument(
        "--verify-bundle",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    if args.verify_bundle:
        try:
            verify_packaged_bundle()
        except Exception as exc:
            print(
                f"EnMotion packaged sidecar verification failed: " f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return 1
        print("EnMotion packaged sidecar verification passed")
        return 0
    if not args.desktop_runtime:
        parser.error("--desktop-runtime is required")
    try:
        config = load_runtime_config_from_environment()
        run(config)
    except Exception as exc:
        # The Tauri shell captures stderr. Never include the nonce or serialized
        # runtime payload in this diagnostic.
        print(f"EnMotion sidecar failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
