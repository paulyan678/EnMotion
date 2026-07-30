# EnMotion Desktop

This directory is the native shell for EnMotion's local-first hybrid application.
It intentionally supports only:

- macOS on Apple silicon (`aarch64-apple-darwin`)
- macOS on Intel (`x86_64-apple-darwin`)
- Windows x64 (`x86_64-pc-windows-msvc`)

The account/control plane is a separate server deployment. The desktop process
does not expose the local API to the LAN and does not contain a GitHub token.

## Runtime architecture

1. Tauri chooses an unused loopback port and generates a 256-bit random nonce.
2. It passes a base64url JSON runtime contract to the packaged Python sidecar
   through one inherited environment variable, never a command-line argument.
3. The sidecar removes that variable, binds only to `127.0.0.1`, and reports an
   HMAC-authenticated readiness proof.
4. Tauri permits navigation only to its bundled loading page and the exact
   random loopback origin. It then visits a one-time nonce URL.
5. The sidecar replaces that nonce with an HttpOnly, SameSite=Lax session
   cookie and redirects to `/static/index.html`. `Lax` is required for the
   one-time transition from `tauri://localhost` to the loopback origin; the
   nonce, exact-origin navigation policy, and CSRF checks still protect the
   session. The frontend and API are then same-origin.

EnMotion also enforces one desktop instance per operating-system account.
Opening it again focuses the existing window instead of starting a second
sidecar against the same workspace files.

The local data directory is resolved by the operating system. Generated media
always defaults to `Documents/enmotion-output`. Application updates never replace
or delete either location.

Web content receives only Tauri events and five narrow Rust commands:

- `desktop_confirm_ui_ready`
- `desktop_update_state`
- `desktop_check_for_updates`
- `desktop_start_update`
- `desktop_install_and_restart`

It does **not** receive raw shell, process, filesystem, or updater-plugin
permissions. `desktop_confirm_ui_ready` also fails closed unless the webview
has a current employee session that the nonce-protected sidecar can confirm
against the control plane.

## Local validation

From the repository root:

```sh
python3 -m unittest discover -s desktop/python -p 'test_*.py'
python3 -m compileall -q desktop/python desktop/scripts
python3 desktop/scripts/validate.py
```

To make a native package on a supported machine:

```sh
npm --prefix frontend ci
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run test:all
npm --prefix frontend run build
npm --prefix frontend run check:export
python3 desktop/scripts/stage_frontend.py
python3 -m pip install -r requirements-desktop.txt "pyinstaller==6.21.0"
python3 desktop/scripts/build_sidecar.py --target aarch64-apple-darwin
python3 desktop/scripts/validate.py --staged
cd desktop
ENMOTION_CONTROL_PLANE_URL=https://enmotion.tianen123.xyz:9443 \
  npx --yes @tauri-apps/cli@2.11.4 build \
  --target aarch64-apple-darwin \
  --config src-tauri/tauri.macos.conf.json
cd ..
python3 desktop/scripts/sign_local_macos.py \
  --app desktop/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/EnMotion.app \
  --expected-control-plane-url https://enmotion.tianen123.xyz:9443
```

Use the Intel or Windows target only on a matching native runner. PyInstaller
does not cross-compile Python applications. The desktop requirements select the
last compatible CPython 3.12 PyTorch/Torchaudio pair (`2.2.2`) on Intel macOS
and the current pinned pair on Apple silicon and Windows, so manual builds use
the same compatibility contract as release CI.

FFmpeg must be installed or provided through `ENMOTION_FFMPEG_BINARY`. The build
script verifies it and embeds it in the sidecar. The build deliberately fails
if it is absent, and release SBOMs record the exact FFmpeg version. Before
distributing installers, confirm that the selected FFmpeg build and its enabled
codecs satisfy the applicable LGPL/GPL notice and source-offer obligations.

The final signing helper is only for a fresh local macOS validation build. It
ad-hoc signs the PyInstaller launchers without Hardened Runtime library
validation, re-seals the outer Tauri app, and runs the packaged sidecar smoke
test. It also verifies that the intended account/control-plane origin was
compiled into the release-profile executable; a runtime environment override is
deliberately ignored by production builds. The helper refuses to replace a
Developer ID or other distribution signature. Official release builds keep the
fully hardened Developer ID signing and notarization workflow in GitHub Actions.

The launch-critical Python sidecar is packaged as a pre-expanded runtime so the
desktop app does not unpack it again on every launch. Demucs and PyTorch remain
in a separate one-file worker that starts only when audio separation is used.

## Release configuration

The tracked Tauri config contains deliberately unusable updater placeholders.
CI renders an untracked override from:

- `ENMOTION_UPDATER_PUBLIC_KEY` — the public half of the Tauri Minisign key
- `ENMOTION_CONTROL_PLANE_URL` — the HTTPS origin used for login, quota, and usage
- `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`
- Apple Developer ID/notarization secrets on macOS
- Authenticode PFX/password/timestamp secrets on Windows

The public release workflow always generates GitHub build-provenance
attestations for the final asset set.

Release builds compile the control-plane origin into the Rust shell and ignore
a process-environment override at runtime. Debug builds may use an environment
override for local development.

The public `paulyan678/EnMotion` GitHub repository is the release system of
record and the first-install download page. A signed-in desktop still asks its
nonce-protected sidecar to create an employee-scoped update session. The
sidecar authenticates
`POST /api/v1/releases/session` with the employee's refreshed control-plane
session, but returns only an opaque, short-lived HTTPS manifest capability to
Rust. The Tauri updater accepts that URL and its download URL only when both are
on the configured control-plane origin and under
`/api/v1/releases/session/<token>/...`. Neither the webview nor Rust receives
the employee bearer or a GitHub token.

CI first uploads the verified native artifacts to a draft release, confirms
their names and sizes through the GitHub API, and writes immutable
`https://github.com/<owner>/<repo>/releases/download/<tag>/<asset>` URLs into
`control-plane-releases.json`. Every platform entry also contains its Minisign
`signature`. The control plane retrieves those public assets without a GitHub
credential, follows only its explicit redirect allowlist, and verifies size
plus SHA-256 before streaming the result. CI uploads the inventory and
checksums only after every native asset exists, attests the final set, then
publishes the draft. Never activate a partially uploaded release. If
aggregation fails after draft creation, inspect and delete that exact draft
before rerunning; CI refuses to mutate or silently reuse an existing release.

For a first install, users download only the matching DMG or signed Windows
setup EXE from
`https://github.com/paulyan678/EnMotion/releases/latest`. Tauri v2 update
packages are the `.app.tar.gz` archives on macOS and the signed NSIS setup EXE
on Windows; their adjacent `.sig` files are verification metadata.

See [UPDATE_CONTRACT.md](UPDATE_CONTRACT.md) for update, migration, and rollback
gates.
