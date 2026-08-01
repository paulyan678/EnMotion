#!/usr/bin/env python3
"""Validate EnMotion's desktop target, security, and release configuration."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DESKTOP_ROOT = REPOSITORY_ROOT / "desktop"
TAURI_ROOT = DESKTOP_ROOT / "src-tauri"
EXPECTED_TARGETS = {
    "aarch64-apple-darwin",
    "x86_64-apple-darwin",
    "x86_64-pc-windows-msvc",
}


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise AssertionError(f"invalid JSON in {path}: {error}") from error


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def is_https_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
        and ".invalid" not in parsed.hostname
    )


def validate_configuration(release: bool, staged: bool, target: str | None) -> None:
    package = load_json(REPOSITORY_ROOT / "package.json")
    frontend_package = load_json(REPOSITORY_ROOT / "frontend/package.json")
    frontend_lock = load_json(REPOSITORY_ROOT / "frontend/package-lock.json")
    cargo = tomllib.loads((TAURI_ROOT / "Cargo.toml").read_text(encoding="utf-8"))
    base = load_json(TAURI_ROOT / "tauri.conf.json")
    macos = load_json(TAURI_ROOT / "tauri.macos.conf.json")
    windows = load_json(TAURI_ROOT / "tauri.windows.conf.json")
    capability = load_json(TAURI_ROOT / "capabilities/main.json")

    check(package["version"] == cargo["package"]["version"], "root and Rust versions differ")
    check(
        package.get("packageManager") == "npm@11.16.0"
        and frontend_package.get("packageManager") == package["packageManager"]
        and package.get("engines", {}).get("node") == ">=24.11 <25"
        and frontend_package.get("engines", {}).get("node") == package["engines"]["node"],
        "Node.js and npm release toolchains must stay on the supported Node 24 LTS line",
    )
    expected_install_scripts = {
        "@parcel/watcher@2.5.6": True,
        "@swc/core@1.15.43": True,
        "esbuild@0.28.1": True,
        "fsevents@2.3.2": True,
        "fsevents@2.3.3": True,
        "unrs-resolver@1.12.2": True,
    }
    check(
        frontend_package.get("allowScripts") == expected_install_scripts
        and (REPOSITORY_ROOT / ".npmrc").read_text(encoding="utf-8").strip()
        == "strict-allow-scripts=true"
        and (REPOSITORY_ROOT / "frontend/.npmrc").read_text(encoding="utf-8").strip()
        == "strict-allow-scripts=true",
        "npm install scripts must remain explicitly reviewed, version-pinned, and strict",
    )
    check(
        frontend_lock.get("packages", {})
        .get("node_modules/minimatch/node_modules/brace-expansion", {})
        .get("version")
        == "1.1.17",
        "frontend development tooling must retain the compatible patched brace-expansion backport",
    )
    check(
        frontend_package.get("overrides", {})
        .get("minimatch@3.1.5", {})
        .get("brace-expansion")
        == "1.1.17",
        "frontend dependency resolution must pin the compatible patched brace-expansion backport",
    )
    check(
        cargo["package"].get("rust-version") == "1.88",
        "Rust MSRV must match the locked dependency graph",
    )
    check(
        cargo["dependencies"].get("tauri-plugin-single-instance") == "=2.4.3",
        "single-instance plugin must remain exactly pinned",
    )
    check(base["productName"] == "EnMotion", "desktop product name must be EnMotion")
    check(base["identifier"] == "com.enmotion.desktop", "desktop identifier is unexpected")
    for asset in (
        REPOSITORY_ROOT / "brand/enmotion-app-icon.svg",
        REPOSITORY_ROOT / "brand/enmotion-lockup.svg",
        REPOSITORY_ROOT / "brand/enmotion-lockup-on-dark.svg",
        REPOSITORY_ROOT / "icon.icns",
        REPOSITORY_ROOT / "icon.ico",
        TAURI_ROOT / "icons/icon.png",
    ):
        check(asset.is_file() and asset.stat().st_size > 0, f"brand asset is missing: {asset}")
    for lockup in (
        REPOSITORY_ROOT / "brand/enmotion-lockup.svg",
        REPOSITORY_ROOT / "brand/enmotion-lockup-on-dark.svg",
    ):
        check(
            "<text" not in lockup.read_text(encoding="utf-8"),
            f"official wordmark must be outlined: {lockup}",
        )
    bootstrap_source = (DESKTOP_ROOT / "bootstrap/index.html").read_text(encoding="utf-8")
    bootstrap_source_lower = bootstrap_source.lower()
    check(
        all(color in bootstrap_source_lower for color in ("#34d8c4", "#ffa94d", "#f2ede4")),
        "desktop bootstrap must use the official EnMotion mark palette",
    )
    check(
        not any(color in bootstrap_source_lower for color in ("#8f79ff", "#5f46dd", "#a998ff")),
        "desktop bootstrap must not use the retired purple placeholder",
    )
    check(base["bundle"]["active"] is False, "base config must never emit every bundle")
    check(
        base["bundle"]["createUpdaterArtifacts"] is False,
        "developer builds must not require release updater signing keys",
    )
    check(
        set(macos["bundle"]["targets"]) == {"app", "dmg"},
        "macOS must build only app and dmg bundles",
    )
    check(
        set(windows["bundle"]["targets"]) == {"nsis"},
        "Windows must build only the NSIS bundle",
    )
    check(
        windows["bundle"]["windows"]["nsis"].get("languages") == ["SimpChinese"]
        and windows["bundle"]["windows"]["nsis"].get("displayLanguageSelector") is False,
        "Windows installer must remain Simplified-Chinese-only",
    )
    check(
        capability["remote"]["urls"] == ["http://127.0.0.1:*"],
        "IPC capability must be loopback-only",
    )
    check(
        capability["permissions"] == ["core:event:default"],
        "web content must not receive shell, filesystem, process, or raw updater permissions",
    )
    check(
        base["bundle"]["externalBin"] == ["binaries/enmotion-demucs-worker"],
        "Tauri must package the on-demand Demucs worker as an external binary",
    )
    check(
        base["bundle"]["resources"]
        == {
            "../web/static/": "web/static/",
            "binaries/enmotion-sidecar-runtime/": "sidecar/",
        },
        "frontend or launch-runtime resource mapping is unexpected",
    )
    check(
        not base["plugins"]["updater"].get("dangerousInsecureTransportProtocol", False),
        "production updater transport must remain HTTPS-only",
    )
    csp_directives = {
        directive.strip().split(maxsplit=1)[0]: directive.strip()
        for directive in base["app"]["security"]["csp"].split(";")
        if directive.strip()
    }
    check(
        csp_directives.get("connect-src") == "connect-src 'self' ipc: http://ipc.localhost",
        "web content may connect only to its own origin and Tauri IPC",
    )
    check(
        csp_directives.get("media-src") == "media-src 'self' blob: https:",
        "desktop previews must permit signed HTTPS media without broad API access",
    )

    main_source = (TAURI_ROOT / "src/main.rs").read_text(encoding="utf-8")
    sidecar_source = (TAURI_ROOT / "src/sidecar.rs").read_text(encoding="utf-8")
    updater_source = (TAURI_ROOT / "src/updater.rs").read_text(encoding="utf-8")
    info_plist_source = (TAURI_ROOT / "Info.plist").read_text(encoding="utf-8")
    python_sidecar_source = (DESKTOP_ROOT / "python/sidecar.py").read_text(encoding="utf-8")
    playground_api_source = (REPOSITORY_ROOT / "src/apps/playground/api.py").read_text(
        encoding="utf-8"
    )
    check("compile_error!" in main_source, "non-macOS/Windows builds must fail closed")
    check(
        main_source.find("tauri_plugin_single_instance::init")
        < main_source.find("tauri_plugin_shell::init"),
        "single-instance protection must be the first Tauri plugin",
    )
    for command in (
        "desktop_confirm_ui_ready",
        "desktop_update_state",
        "desktop_check_for_updates",
        "desktop_start_update",
        "desktop_install_and_restart",
    ):
        check(command in main_source and command in updater_source, f"missing command {command}")
    check(
        "ENMOTION_DESKTOP_RUNTIME_CONFIG" in sidecar_source
        and "X-EnMotion-Desktop-Nonce" in sidecar_source,
        "sidecar nonce contract is missing",
    )
    check(
        "http://127.0.0.1" in sidecar_source,
        "sidecar must use a literal loopback origin",
    )
    check(
        ".command(sidecar_executable)" in sidecar_source
        and "resolve_core_sidecar" in sidecar_source
        and "ENMOTION_DEMUCS_WORKER" in sidecar_source,
        "desktop must launch the pre-expanded core runtime with its on-demand worker",
    )
    check(
        "create_update_session" in sidecar_source
        and "trusted_control_plane_url" in updater_source
        and "/api/v1/releases/session/" in updater_source,
        "employee-scoped HTTPS updater session boundary is missing",
    )
    check(
        "verified_package_sha256" in updater_source and "constant_time_digest_eq" in updater_source,
        "cached updater bytes must be reverified immediately before installation",
    )
    check(
        "UPDATE_DOWNLOAD_TIMEOUT" in updater_source
        and "update.timeout = Some(UPDATE_DOWNLOAD_TIMEOUT)" in updater_source
        and "read_timeout(UPDATE_READ_IDLE_TIMEOUT)" in updater_source,
        "large updater downloads need a long total timeout and a bounded idle timeout",
    )
    check(
        "active_playground_generation_blockers" in python_sidecar_source
        and "blockers.extend(active_playground_blockers())" in python_sidecar_source,
        "update preparation must block unfinished Playground generations",
    )
    check(
        "def active_playground_generation_blockers()" in playground_api_source,
        "Playground must expose its active local generation snapshot",
    )
    check(
        "class UpdateBarrier" in python_sidecar_source
        and '"/_desktop/cancel-update"' in sidecar_source
        and "cancel_update(&app)" in updater_source,
        "installation must gate local mutations and reopen them after failure",
    )
    check(
        "请先登录 EnMotion，再确认更新已就绪" in sidecar_source
        and "def commit_update(request: Request)" in python_sidecar_source
        and "confirmed_employee_remote(request)" in python_sidecar_source
        and 'get_json("/api/v1/auth/session", remote)' in python_sidecar_source,
        "update health confirmation must require a fresh employee session",
    )
    check(
        "EnMotion 本地服务意外停止。" in sidecar_source
        and "EnMotion 无法启动本地服务。" in sidecar_source
        and 'user_safe_error("无法确认 EnMotion 界面已就绪", error)' in updater_source
        and 'format!("{prefix}。请稍后重试；如果问题持续，请联系管理员。")' in updater_source,
        "native startup and updater errors must remain safely localized in Chinese",
    )
    check(
        "NSDocumentsFolderUsageDescription" not in info_plist_source
        and ".document_dir()" not in sidecar_source
        and 'data_dir.join("enmotion-output")' in sidecar_source
        and base["bundle"]["shortDescription"] == "企业专用的本地 AI 创作工作区"
        and "企业内部使用" in base["bundle"]["longDescription"],
        "native package must use private app storage and Simplified Chinese descriptions",
    )

    excluded_parts = {
        "target",
        "gen",
        "binaries",
        "static",
        "release-staging",
        "__pycache__",
    }
    desktop_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in DESKTOP_ROOT.rglob("*")
        if path.is_file()
        and not excluded_parts.intersection(path.relative_to(DESKTOP_ROOT).parts)
        and path.suffix.lower() not in {".ico", ".icns", ".png"}
    )
    check(
        re.search(r"lumen[\s_-]*x", desktop_text, flags=re.IGNORECASE) is None,
        "legacy product branding remains in desktop files",
    )
    check(
        re.search(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b", desktop_text) is None,
        "a GitHub credential-like value is embedded in desktop files",
    )

    workflow = REPOSITORY_ROOT / ".github/workflows/release-desktop.yml"
    if workflow.is_file():
        workflow_text = workflow.read_text(encoding="utf-8")
        ci_workflow_text = (REPOSITORY_ROOT / ".github/workflows/ci.yml").read_text(
            encoding="utf-8"
        )
        combined_workflows = ci_workflow_text + workflow_text
        check("ubuntu-" not in workflow_text.lower(), "desktop release must not use Linux")
        check(
            combined_workflows.count('NODE_VERSION: "24.18.0"') == 2,
            "CI and release workflows must use the reviewed Node 24 LTS patch",
        )
        node24_action_pins = {
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1": 8,
            "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020": 4,
            "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97": 7,
        }
        check(
            all(
                combined_workflows.count(action) == expected_count
                for action, expected_count in node24_action_pins.items()
            )
            and combined_workflows.count("package-manager-cache: false") == 2,
            "CI and release actions must stay pinned to reviewed Node 24 commits",
        )
        found_targets = set(
            re.findall(
                r"(?:aarch64-apple-darwin|x86_64-apple-darwin|x86_64-pc-windows-msvc)",
                workflow_text,
            )
        )
        check(found_targets == EXPECTED_TARGETS, "release workflow target matrix is incomplete")
        check(
            workflow_text.count("ref: ${{ needs.preflight.outputs.sha }}") == 3
            and "refs/tags/${RELEASE_TAG}^{commit}" in workflow_text,
            "native builds and publish must stay pinned to the preflight commit",
        )
        check(
            'git merge-base --is-ancestor "$RELEASE_SHA" origin/main' in workflow_text
            and '.event == "push" and .conclusion == "success"' in workflow_text,
            "desktop releases must require main ancestry and successful push CI",
        )
        check(
            'test "$repository" = "paulyan678/enmotion"' in workflow_text
            and 'test "$(gh api "repos/${GITHUB_REPOSITORY}" --jq .private)" = "false"'
            in workflow_text,
            "desktop releases must fail closed outside the public repository",
        )
        check(
            "https://github.com/${GITHUB_REPOSITORY}/releases/download/${RELEASE_TAG}/"
            in workflow_text,
            "public updater inventory must use immutable GitHub release download URLs",
        )
        check(
            'expected = "2.2.2" if platform.machine() == "x86_64" else "2.13.0"' in workflow_text
            and "-r requirements-desktop.txt" in workflow_text,
            "native packaging must verify the platform-specific PyTorch pin",
        )
        check(
            workflow_text.count("--verify-bundle") == 2
            and "Chocolatey FFmpeg payload was not found" in workflow_text,
            "native runners must execute the real packaged API and FFmpeg payload",
        )
        check(
            '--identifier "$identifier"' in workflow_text
            and '"com.enmotion.desktop.sidecar"' in workflow_text
            and '"com.enmotion.desktop.demucs-worker"' in workflow_text,
            "macOS sidecars must keep stable designated identifiers across releases",
        )
        check(
            'test "$core_identifier" = "com.enmotion.desktop.sidecar"' in workflow_text,
            "the notarized app must retain the stable core-sidecar identifier",
        )
        check(
            workflow_text.count("--ffmpeg") == 2,
            "every target SBOM must identify the exact bundled FFmpeg build",
        )
        check(
            "release/enmotion.exe" in workflow_text
            and "enmotion-sidecar-runtime/enmotion-sidecar.exe" in workflow_text
            and "enmotion-demucs-worker-x86_64-pc-windows-msvc.exe" in workflow_text
            and "Expected signed sidecars, application, and installer" in workflow_text,
            "Windows release must verify every executable signing boundary",
        )
        windows_signing_source = (DESKTOP_ROOT / "scripts/sign-windows.ps1").read_text(
            encoding="utf-8"
        )
        check(
            'WINDOWS_TIMESTAMP -eq "http://timestamp.digicert.com"' in workflow_text
            and '$timestamp -eq "http://timestamp.digicert.com"' in windows_signing_source
            and "official DigiCert RFC 3161 endpoint" in workflow_text
            and "official DigiCert RFC 3161 endpoint" in windows_signing_source,
            "Windows signing must permit only HTTPS or DigiCert's official HTTP timestamp service",
        )
        check(
            "-Filter *-setup.exe" in workflow_text
            and 'Copy-Item ($installer.FullName + ".sig") "release-assets/$installerName.sig"'
            in workflow_text
            and 'f"EnMotion-Setup-{version}-Windows-x64.exe.sig"' in workflow_text
            and "Windows-x64.nsis.zip" not in workflow_text,
            "Windows updater must publish the signed Tauri v2 NSIS setup executable",
        )
        check(
            workflow_text.count("working-directory: desktop") == 2
            and "--project-dir" not in workflow_text,
            "Tauri CLI builds must run from the desktop project directory",
        )
        check(
            re.search(
                r"(?m)^permissions:\n  contents: read\n",
                workflow_text,
            )
            is not None
            and re.search(
                r"(?ms)^  publish:.*?^    permissions:\n"
                r"      contents: write\n"
                r"      id-token: write\n"
                r"      attestations: write\n",
                workflow_text,
            )
            is not None,
            "only the aggregate publish job may receive release write permissions",
        )
        check(
            "- name: Attest final release assets" in workflow_text
            and "uses: actions/attest-build-provenance@" in workflow_text
            and "if: vars.ENMOTION_ENABLE_GITHUB_ATTESTATIONS" not in workflow_text,
            "public release attestations must always run",
        )

    if staged:
        check(
            (DESKTOP_ROOT / "web/static/index.html").is_file(),
            "staged frontend index.html is missing",
        )
        check(
            (DESKTOP_ROOT / "web/static/_next/static").is_dir(),
            "staged Next.js assets are missing",
        )

    if release:
        public_key = os.environ.get("ENMOTION_UPDATER_PUBLIC_KEY", "")
        control_plane_url = os.environ.get("ENMOTION_CONTROL_PLANE_URL", "")
        check(public_key and "REPLACE_" not in public_key, "release updater key is missing")
        check(
            is_https_origin(control_plane_url),
            "release control-plane URL must be a real HTTPS origin",
        )
        check((TAURI_ROOT / "Cargo.lock").is_file(), "Cargo.lock is required for releases")
        release_config = load_json(TAURI_ROOT / "tauri.release.conf.json")
        check(
            release_config.get("bundle", {}).get("createUpdaterArtifacts") is True,
            "release configuration must create signed updater artifacts",
        )
        check(
            release_config.get("plugins", {}).get("updater", {}).get("pubkey") == public_key,
            "release configuration does not contain the requested updater public key",
        )
        check(target in EXPECTED_TARGETS, "a supported --target is required for releases")
        extension = ".exe" if "windows" in target else ""
        runtime = (
            TAURI_ROOT / "binaries" / "enmotion-sidecar-runtime" / f"enmotion-sidecar{extension}"
        )
        worker = TAURI_ROOT / "binaries" / f"enmotion-demucs-worker-{target}{extension}"
        check(runtime.is_file(), f"release core runtime is missing for {target}")
        check(worker.is_file(), f"release Demucs worker is missing for {target}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--target", choices=sorted(EXPECTED_TARGETS))
    args = parser.parse_args()
    try:
        validate_configuration(args.release, args.staged, args.target)
    except AssertionError as error:
        print(f"desktop validation failed: {error}", file=sys.stderr)
        return 1
    print("EnMotion desktop configuration is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
