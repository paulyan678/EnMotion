"""Server-mode validation for user-controlled local and remote media."""

from __future__ import annotations

import base64
import http.client
import ipaddress
import os
import re
import socket
import ssl
from pathlib import Path
from urllib.parse import urljoin, urlparse


class UnsafeMediaReferenceError(ValueError):
    pass


_IMAGE_DATA_URL = re.compile(
    r"^data:(image/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/=\r\n]+)$",
    re.IGNORECASE,
)


def _positive_size_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as exc:
        raise UnsafeMediaReferenceError(f"{name} must be an integer") from exc
    if value <= 0:
        raise UnsafeMediaReferenceError(f"{name} must be greater than zero")
    return value


def image_limit_bytes() -> int:
    return _positive_size_env("ENMOTION_REMOTE_IMAGE_MAX_BYTES", 25 * 1024 * 1024)


def media_limit_bytes() -> int:
    return _positive_size_env("ENMOTION_REMOTE_MEDIA_MAX_BYTES", 512 * 1024 * 1024)


def decode_image_data_url(
    reference: str,
    *,
    max_bytes: int | None = None,
) -> tuple[str, bytes]:
    """Validate and decode a bounded image data URL.

    Checking the encoded length before decoding prevents an attacker from
    making the process allocate an arbitrarily large intermediate buffer.
    """

    limit = image_limit_bytes() if max_bytes is None else max_bytes
    match = _IMAGE_DATA_URL.fullmatch(str(reference or "").strip())
    if match is None:
        raise UnsafeMediaReferenceError(
            "Image data URLs must be base64 PNG, JPEG, WebP, or GIF data"
        )
    encoded = "".join(match.group(2).split())
    if len(encoded) > ((limit + 2) // 3) * 4:
        raise UnsafeMediaReferenceError("Image data URL is larger than the allowed limit")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise UnsafeMediaReferenceError("Image data URL contains invalid base64") from exc
    if not payload:
        raise UnsafeMediaReferenceError("Image data URL is empty")
    if len(payload) > limit:
        raise UnsafeMediaReferenceError("Image data URL is larger than the allowed limit")
    return match.group(1).lower(), payload


def current_workspace_output_root() -> Path:
    """Return the authenticated workspace root, failing closed outside a tenant."""

    from ..apps.web_runtime.context import get_tenant
    from ..apps.server.quotas import workspace_output_root

    tenant = get_tenant(required=True)
    assert tenant is not None
    return workspace_output_root(tenant.workspace_id)


def resolve_workspace_media_path(
    output_root: str | Path,
    reference: str,
    *,
    require_file: bool = True,
) -> str:
    root = Path(output_root).expanduser().resolve()
    raw = str(reference or "").strip()
    if not raw:
        raise UnsafeMediaReferenceError("Media path is empty")
    for prefix in (
        "/files/outputs/",
        "/files/output/",
        "/files/",
        "files/outputs/",
        "files/output/",
        "files/",
        "output/",
    ):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
            break
    # Historical API responses used the plural route alias even though the
    # authoritative directory is ``output/video``.
    if raw.startswith("videos/"):
        raw = "video/" + raw[len("videos/") :]
    supplied = Path(raw).expanduser()
    candidate = supplied.resolve() if supplied.is_absolute() else (root / supplied).resolve()
    if candidate != root and root not in candidate.parents:
        raise UnsafeMediaReferenceError("Local media must stay inside this workspace")
    if require_file and not candidate.is_file():
        raise UnsafeMediaReferenceError("Local media file was not found")
    return str(candidate)


def _host_allowed(hostname: str) -> bool:
    patterns = [
        item.strip().lower().rstrip(".")
        for item in os.getenv("ENMOTION_REMOTE_MEDIA_HOSTS", "").split(",")
        if item.strip()
    ]
    for pattern in patterns:
        if pattern.startswith("*."):
            suffix = pattern[1:]
            if hostname.endswith(suffix) and hostname != suffix[1:]:
                return True
        elif hostname == pattern:
            return True
    return False


def _validated_remote_target(url: str):
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeMediaReferenceError("Remote media must use an HTTP(S) URL")
    if parsed.username or parsed.password:
        raise UnsafeMediaReferenceError("Remote media URLs cannot contain credentials")
    hostname = parsed.hostname.lower().rstrip(".")
    if not _host_allowed(hostname):
        raise UnsafeMediaReferenceError(
            "Remote media host is not allowed; upload the file or ask an admin to allow it"
        )
    try:
        address_info = socket.getaddrinfo(
            hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise UnsafeMediaReferenceError("Remote media host could not be resolved") from exc
    if not address_info:
        raise UnsafeMediaReferenceError("Remote media host could not be resolved")
    addresses: list[str] = []
    for address in address_info:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise UnsafeMediaReferenceError(
                "Remote media cannot resolve to a private, local, or reserved address"
            )
        normalized_ip = str(ip)
        if normalized_ip not in addresses:
            addresses.append(normalized_ip)
    return parsed, addresses


def validate_remote_media_url(url: str) -> str:
    parsed, _addresses = _validated_remote_target(url)
    return parsed.geturl()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """TLS connection whose TCP peer is a prevalidated public IP address."""

    def __init__(self, hostname: str, address: str, port: int, timeout: float):
        super().__init__(hostname, port=port, timeout=timeout)
        self._validated_address = address

    def connect(self) -> None:
        # No proxy/tunnel is used. The original hostname remains the TLS SNI
        # and certificate-verification name while DNS cannot change the peer.
        self.sock = socket.create_connection(
            (self._validated_address, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def _open_pinned_response(parsed, addresses: list[str]):
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    hostname = parsed.hostname or ""
    default_port = 443 if parsed.scheme == "https" else 80
    host_header = hostname if port == default_port else f"{hostname}:{port}"
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    last_error: Exception | None = None
    for address in addresses:
        connection: http.client.HTTPConnection
        if parsed.scheme == "https":
            connection = _PinnedHTTPSConnection(hostname, address, port, 30)
        else:
            connection = http.client.HTTPConnection(address, port=port, timeout=30)
        try:
            connection.request(
                "GET",
                target,
                headers={
                    "Host": host_header,
                    "User-Agent": "EnMotion-Media-Fetch/1.0",
                    "Accept": "*/*",
                    "Connection": "close",
                },
            )
            return connection, connection.getresponse()
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            last_error = exc
            connection.close()
    raise UnsafeMediaReferenceError("Remote media host could not be reached") from last_error


def download_remote_media(
    url: str,
    destination: str | Path,
    *,
    max_bytes: int,
    allowed_content_prefixes: tuple[str, ...],
    max_redirects: int = 3,
) -> str:
    """Stream an allowlisted public URL with bounded redirects and size."""

    current = str(url)
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        for redirect_count in range(max_redirects + 1):
            parsed, addresses = _validated_remote_target(current)
            current = parsed.geturl()
            connection, response = _open_pinned_response(parsed, addresses)
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                response.close()
                connection.close()
                if not location or redirect_count >= max_redirects:
                    raise UnsafeMediaReferenceError("Remote media redirected too many times")
                current = urljoin(current, location)
                continue
            if not 200 <= response.status < 300:
                response.close()
                connection.close()
                raise UnsafeMediaReferenceError(
                    f"Remote media returned HTTP {response.status}"
                )
            content_length = response.getheader("Content-Length")
            if content_length:
                try:
                    advertised_size = int(content_length)
                except ValueError as exc:
                    response.close()
                    connection.close()
                    raise UnsafeMediaReferenceError(
                        "Remote media returned an invalid content length"
                    ) from exc
                if advertised_size < 0 or advertised_size > max_bytes:
                    response.close()
                    connection.close()
                    raise UnsafeMediaReferenceError(
                        "Remote media is larger than the allowed limit"
                    )
            content_type = (response.getheader("Content-Type") or "").split(";", 1)[0].lower()
            if content_type and not any(
                content_type.startswith(prefix) for prefix in allowed_content_prefixes
            ):
                response.close()
                connection.close()
                raise UnsafeMediaReferenceError("Remote media has an unsupported content type")
            written = 0
            try:
                with target.open("wb") as handle:
                    while True:
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > max_bytes:
                            raise UnsafeMediaReferenceError(
                                "Remote media is larger than the allowed limit"
                            )
                        handle.write(chunk)
            finally:
                response.close()
                connection.close()
            if written == 0:
                raise UnsafeMediaReferenceError("Remote media response was empty")
            return str(target)
        raise UnsafeMediaReferenceError("Remote media redirected too many times")
    except Exception:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        raise
