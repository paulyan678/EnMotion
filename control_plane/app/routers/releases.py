from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Any, BinaryIO, Callable, Iterator, Literal
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, Response, StreamingResponse
from packaging.version import InvalidVersion, Version
from sqlalchemy import delete, select, update
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from ..audit import record_audit
from ..config import ConfigurationError, validate_public_origin
from ..database import begin_immediate
from ..dependencies import CurrentPrincipal, client_ip
from ..models import ReleaseGrant, User, utcnow
from ..schemas import ReleaseSessionRequest, ReleaseSessionResponse
from ..security import new_token, token_digest


router = APIRouter(tags=["runtime and releases"])

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")
_CAPABILITY = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_MAX_RELEASE_BYTES = 2 * 1024 * 1024 * 1024
_PLATFORM_BY_TARGET = {
    ("darwin", "aarch64"): "macos-arm64",
    ("darwin", "x86_64"): "macos-x86_64",
    ("windows", "x86_64"): "windows-x86_64",
}


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


@dataclass(frozen=True)
class _GrantContext:
    user_id: str
    target: str
    arch: str
    platform: str
    channel: str
    current_version: str
    release_version: str


class _DownloadLease:
    def __init__(self, staged: BinaryIO, limiter, user_id: str):
        self.staged = staged
        self.limiter = limiter
        self.user_id = user_id
        self._released = False
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
            self.staged.close()
            self.limiter.release(self.user_id)


def _load_manifest(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "release manifest is not configured")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "release manifest is unavailable"
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("releases"), list):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "release manifest has an invalid format"
        )
    return payload


def _public_release(entry: dict[str, Any]) -> dict[str, Any]:
    required = {
        "version",
        "platform",
        "channel",
        "sha256",
        "size_bytes",
        "published_at",
        "signature",
        "source_url",
    }
    if not required.issubset(entry):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "release manifest entry is incomplete"
        )
    sha256 = str(entry["sha256"])
    try:
        size_bytes = int(entry["size_bytes"])
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "release size is invalid"
        ) from exc
    if (
        not _SHA256.fullmatch(sha256)
        or not 0 < size_bytes < _MAX_RELEASE_BYTES
    ):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "release checksum or size is invalid",
        )
    signature = entry["signature"]
    if not isinstance(signature, str) or not signature.strip() or len(signature) > 16 * 1024:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "release signature is invalid",
        )
    return {
        "version": str(entry["version"]),
        "platform": str(entry["platform"]),
        "channel": str(entry["channel"]),
        "sha256": sha256.lower(),
        "size_bytes": size_bytes,
        "published_at": str(entry["published_at"]),
        "signature": signature.strip(),
        "minimum_supported_version": entry.get("minimum_supported_version"),
        "notes": entry.get("notes", ""),
        "download_url": (
            f"/api/v1/releases/{entry['platform']}/{entry['version']}/download"
            f"?channel={entry['channel']}"
        ),
    }


def _matching_releases(
    manifest: dict[str, Any],
    *,
    platform: str,
    channel: str,
) -> list[dict[str, Any]]:
    matches = [
        item
        for item in manifest["releases"]
        if isinstance(item, dict)
        and item.get("platform") == platform
        and item.get("channel") == channel
    ]
    for item in matches:
        _public_release(item)
        try:
            Version(str(item["version"]))
        except InvalidVersion as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "release manifest contains an invalid version",
            ) from exc
    return matches


def _latest_release_entry(
    manifest: dict[str, Any],
    *,
    platform: str,
    channel: str,
) -> dict[str, Any]:
    matches = _matching_releases(
        manifest,
        platform=platform,
        channel=channel,
    )
    if not matches:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no matching release")
    return max(matches, key=lambda item: Version(str(item["version"])))


def _exact_release_entry(
    manifest: dict[str, Any],
    *,
    platform: str,
    channel: str,
    version: str,
) -> dict[str, Any]:
    match = next(
        (
            item
            for item in _matching_releases(
                manifest,
                platform=platform,
                channel=channel,
            )
            if item.get("version") == version
        ),
        None,
    )
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "release not found")
    return match


def _public_origin(request: Request) -> str:
    settings = request.app.state.settings
    canonical = settings.public_base_url
    if not canonical:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "ENMOTION_PUBLIC_BASE_URL is required for desktop update sessions",
        )
    allowed = {canonical, *settings.public_base_url_aliases}
    try:
        requested = validate_public_origin(
            "request origin",
            f"{request.url.scheme}://{request.url.netloc}",
            allow_insecure=settings.allow_insecure_upstreams,
        )
    except ConfigurationError:
        return canonical
    return requested if requested in allowed else canonical


def _grant_digest(request: Request, token: str) -> str:
    if not _CAPABILITY.fullmatch(token):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "release session not found")
    return token_digest(request.app.state.settings.session_hmac_secret, token)


def _resolve_grant(
    request: Request,
    token: str,
    *,
    phase: Literal["manifest", "download"],
) -> _GrantContext:
    digest = _grant_digest(request, token)
    now = utcnow()
    with request.app.state.db.session() as session:
        row = session.execute(
            select(ReleaseGrant, User)
            .join(User, User.id == ReleaseGrant.user_id)
            .where(ReleaseGrant.token_digest == digest)
        ).one_or_none()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "release session not found")
        grant, user = row
        if not user.active or _aware(grant.expires_at) <= now:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "release session not found")
        if phase == "manifest" and grant.manifest_consumed_at is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "release session not found")
        if phase == "download" and (
            grant.manifest_consumed_at is None
            or grant.download_consumed_at is not None
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "release session not found")
        return _GrantContext(
            user_id=user.id,
            target=grant.target,
            arch=grant.arch,
            platform=grant.platform,
            channel=grant.channel,
            current_version=grant.current_version,
            release_version=grant.release_version,
        )


def _consume_grant_phase(
    request: Request,
    token: str,
    *,
    phase: Literal["manifest", "download"],
) -> None:
    digest = _grant_digest(request, token)
    now = utcnow()
    conditions = [
        ReleaseGrant.token_digest == digest,
        ReleaseGrant.expires_at > now,
        ReleaseGrant.user_id.in_(select(User.id).where(User.active.is_(True))),
    ]
    values: dict[str, datetime] = {"last_used_at": now}
    if phase == "manifest":
        conditions.append(ReleaseGrant.manifest_consumed_at.is_(None))
        values["manifest_consumed_at"] = now
    else:
        conditions.extend(
            (
                ReleaseGrant.manifest_consumed_at.is_not(None),
                ReleaseGrant.download_consumed_at.is_(None),
            )
        )
        values["download_consumed_at"] = now

    with request.app.state.db.session() as session:
        begin_immediate(session)
        result = session.execute(
            update(ReleaseGrant)
            .where(*conditions)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "release session not found",
            )


@router.get("/runtime-config")
def runtime_config(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    return {
        "app_name": "EnMotion",
        "api_version": "v1",
        "control_plane_version": settings.app_version,
        "auth_base": "/api/v1/auth",
        "account_base": "/api/v1/account",
        "gateway_base": "/api/v1/gateway",
        "latest_release_url": "/api/v1/releases/latest",
        "max_gateway_body_bytes": settings.max_request_body_bytes,
    }


@router.get("/releases/latest")
def latest_release(
    request: Request,
    _principal: CurrentPrincipal,
    platform: str = Query(min_length=1, max_length=120),
    channel: str = Query(default="stable", min_length=1, max_length=40),
    current_version: str | None = Query(default=None, max_length=120),
) -> dict[str, Any]:
    del current_version  # Reserved for future staged-rollout/minimum-version policy.
    if not _SAFE_SEGMENT.fullmatch(platform) or not _SAFE_SEGMENT.fullmatch(channel):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid release selector")
    manifest = _load_manifest(request.app.state.settings.release_manifest)
    latest = _latest_release_entry(
        manifest,
        platform=platform,
        channel=channel,
    )
    return _public_release(latest)


async def _iter_response(response: httpx.Response):
    if response.is_stream_consumed:
        if response.content:
            yield response.content
        return
    async for chunk in response.aiter_bytes():
        yield chunk


async def _stage_verified_release(
    response: httpx.Response,
    *,
    expected_size: int,
    expected_sha256: str,
) -> BinaryIO:
    staged = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    digest = hashlib.sha256()
    received = 0
    verified = False
    try:
        try:
            async for chunk in _iter_response(response):
                received += len(chunk)
                if received > expected_size:
                    raise HTTPException(
                        status.HTTP_502_BAD_GATEWAY,
                        "release source exceeded its manifest size",
                    )
                digest.update(chunk)
                await run_in_threadpool(staged.write, chunk)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "release source failed while downloading",
            ) from exc
        if received != expected_size or digest.hexdigest() != expected_sha256:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "release source failed manifest integrity verification",
            )
        await run_in_threadpool(staged.seek, 0)
        verified = True
        return staged
    finally:
        await response.aclose()
        if not verified:
            await run_in_threadpool(staged.close)


def _iter_staged_release(lease: _DownloadLease) -> Iterator[bytes]:
    try:
        while chunk := lease.staged.read(1024 * 1024):
            yield chunk
    finally:
        lease.close()


def _validate_source_url(request: Request, source_url: str, *, redirect: bool) -> None:
    settings = request.app.state.settings
    parsed = urlparse(source_url)
    try:
        port = parsed.port
    except ValueError:
        port = -1
    allowed_schemes = (
        {"http", "https"} if settings.allow_insecure_upstreams else {"https"}
    )
    if (
        parsed.scheme not in allowed_schemes
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or (
            (parsed.scheme == "https" and port not in {None, 443})
            or (parsed.scheme == "http" and port not in {None, 80})
        )
        or parsed.hostname.lower() not in settings.release_allowed_hosts
    ):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY if redirect else status.HTTP_503_SERVICE_UNAVAILABLE,
            "release source redirect is not allowlisted"
            if redirect
            else "release source is not allowlisted",
        )


async def _open_release_source(
    request: Request,
    source_url: str,
) -> httpx.Response:
    client: httpx.AsyncClient = request.app.state.release_client
    current_url = source_url
    for redirect_count in range(4):
        _validate_source_url(
            request,
            current_url,
            redirect=redirect_count > 0,
        )
        host = urlparse(current_url).hostname
        credential = request.app.state.settings.release_source_credentials.get(
            (host or "").lower()
        )
        headers = {
            "Accept": "application/octet-stream",
            "User-Agent": "EnMotion-Control-Plane/0.1",
        }
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        if (host or "").lower() == "api.github.com":
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        response = await client.send(
            client.build_request("GET", current_url, headers=headers),
            stream=True,
        )
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response
        location = response.headers.get("location", "")
        await response.aclose()
        if not location or redirect_count >= 3:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "release source returned an invalid redirect",
            )
        current_url = urljoin(current_url, location)
    raise HTTPException(
        status.HTTP_502_BAD_GATEWAY,
        "release source exceeded the redirect limit",
    )


async def _serve_release_entry(
    request: Request,
    *,
    entry: dict[str, Any],
    user_id: str,
    on_verified: Callable[[], None] | None = None,
) -> StreamingResponse:
    public_metadata = _public_release(entry)
    source_url = str(entry.get("source_url", ""))
    _validate_source_url(request, source_url, redirect=False)

    limiter = request.app.state.release_download_limiter
    if not limiter.acquire(user_id):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "another release download is already active for this account",
            headers={"Retry-After": "30"},
        )

    upstream: httpx.Response | None = None
    staged: BinaryIO | None = None
    ownership_transferred = False
    try:
        upstream = await _open_release_source(request, source_url)
        if upstream.status_code != 200:
            await upstream.aclose()
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "release source returned an error",
            )
        upstream_length = upstream.headers.get("content-length")
        if upstream_length and not upstream.headers.get("content-encoding"):
            try:
                length_matches = int(upstream_length) == public_metadata["size_bytes"]
            except ValueError:
                length_matches = False
            if not length_matches:
                await upstream.aclose()
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY,
                    "release source size does not match the manifest",
                )
        headers = {
            "Content-Type": upstream.headers.get(
                "content-type", "application/octet-stream"
            ),
            "Content-Disposition": (
                upstream.headers.get("content-disposition")
                or (
                    f'attachment; filename="EnMotion-{public_metadata["version"]}-'
                    f'{public_metadata["platform"]}"'
                )
            ),
            "X-Content-SHA256": public_metadata["sha256"],
            "Content-Length": str(public_metadata["size_bytes"]),
        }
        staged = await _stage_verified_release(
            upstream,
            expected_size=public_metadata["size_bytes"],
            expected_sha256=public_metadata["sha256"],
        )
        if on_verified is not None:
            await run_in_threadpool(on_verified)
        lease = _DownloadLease(staged, limiter, user_id)
        staged = None
        response = StreamingResponse(
            _iter_staged_release(lease),
            status_code=200,
            headers=headers,
            background=BackgroundTask(lease.close),
        )
        ownership_transferred = True
        return response
    except httpx.RequestError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "release source unavailable",
        ) from exc
    finally:
        if upstream is not None and not upstream.is_closed:
            await upstream.aclose()
        if staged is not None:
            await run_in_threadpool(staged.close)
        if not ownership_transferred:
            limiter.release(user_id)


@router.post(
    "/releases/session",
    response_model=ReleaseSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_release_session(
    payload: ReleaseSessionRequest,
    principal: CurrentPrincipal,
    request: Request,
) -> ReleaseSessionResponse:
    platform = _PLATFORM_BY_TARGET.get((payload.target, payload.arch))
    if platform is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unsupported desktop target")
    try:
        Version(payload.current_version)
    except InvalidVersion as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "current_version is not a valid application version",
        ) from exc

    manifest = _load_manifest(request.app.state.settings.release_manifest)
    entry = _latest_release_entry(
        manifest,
        platform=platform,
        channel=payload.channel,
    )
    metadata = _public_release(entry)
    token = new_token()
    now = utcnow()
    with request.app.state.db.session() as session:
        begin_immediate(session)
        user = session.get(User, principal.user_id)
        if user is None or not user.active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account is inactive")
        session.execute(delete(ReleaseGrant).where(ReleaseGrant.expires_at <= now))
        grant = ReleaseGrant(
            token_digest=token_digest(
                request.app.state.settings.session_hmac_secret,
                token,
            ),
            user_id=user.id,
            target=payload.target,
            arch=payload.arch,
            platform=platform,
            channel=payload.channel,
            current_version=payload.current_version,
            release_version=metadata["version"],
            expires_at=now
            + timedelta(
                seconds=request.app.state.settings.release_session_ttl_seconds
            ),
        )
        session.add(grant)
        session.flush()
        record_audit(
            session,
            actor_user_id=user.id,
            action="release.session_created",
            target_type="release_grant",
            target_id=grant.id,
            detail={
                "target": payload.target,
                "arch": payload.arch,
                "platform": platform,
                "release_version": metadata["version"],
            },
            ip_address=client_ip(request),
        )

    manifest_url = (
        f"{_public_origin(request)}/api/v1/releases/session/{token}/manifest"
    )
    return ReleaseSessionResponse(manifest_url=manifest_url)


@router.get("/releases/session/{token}/manifest")
def release_session_manifest(token: str, request: Request) -> Response:
    grant = _resolve_grant(request, token, phase="manifest")
    manifest = _load_manifest(request.app.state.settings.release_manifest)
    entry = _exact_release_entry(
        manifest,
        platform=grant.platform,
        channel=grant.channel,
        version=grant.release_version,
    )
    metadata = _public_release(entry)
    try:
        no_update = Version(grant.current_version) >= Version(metadata["version"])
    except InvalidVersion as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "release session contains invalid version metadata",
        ) from exc
    if no_update:
        _consume_grant_phase(request, token, phase="manifest")
        return Response(
            status_code=status.HTTP_204_NO_CONTENT,
            headers={"Cache-Control": "no-store"},
        )
    download_url = (
        f"{_public_origin(request)}/api/v1/releases/session/{token}/download"
    )
    response = JSONResponse(
        {
            "version": metadata["version"],
            "url": download_url,
            "signature": metadata["signature"],
            "notes": metadata["notes"],
            "pub_date": metadata["published_at"],
        },
        headers={"Cache-Control": "no-store"},
    )
    _consume_grant_phase(request, token, phase="manifest")
    return response


@router.get("/releases/session/{token}/download")
async def release_session_download(token: str, request: Request) -> StreamingResponse:
    grant = await run_in_threadpool(
        _resolve_grant,
        request,
        token,
        phase="download",
    )
    try:
        if Version(grant.current_version) >= Version(grant.release_version):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "release session not found",
            )
    except InvalidVersion as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "release session not found",
        ) from exc
    manifest = await run_in_threadpool(
        _load_manifest,
        request.app.state.settings.release_manifest,
    )
    entry = _exact_release_entry(
        manifest,
        platform=grant.platform,
        channel=grant.channel,
        version=grant.release_version,
    )
    return await _serve_release_entry(
        request,
        entry=entry,
        user_id=grant.user_id,
        on_verified=lambda: _consume_grant_phase(
            request,
            token,
            phase="download",
        ),
    )


@router.get("/releases/{platform}/{version}/download")
async def download_release(
    platform: str,
    version: str,
    request: Request,
    principal: CurrentPrincipal,
    channel: str = Query(default="stable", min_length=1, max_length=40),
) -> StreamingResponse:
    if not all(_SAFE_SEGMENT.fullmatch(value) for value in (platform, version, channel)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid release selector")
    manifest = await run_in_threadpool(
        _load_manifest,
        request.app.state.settings.release_manifest,
    )
    entry = _exact_release_entry(
        manifest,
        platform=platform,
        channel=channel,
        version=version,
    )
    return await _serve_release_entry(
        request,
        entry=entry,
        user_id=principal.user_id,
    )
