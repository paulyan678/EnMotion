# EnMotion conversion and acceptance plan

## Phase 1 — Independent foundation

- Export a committed source snapshot without its Git metadata or local secrets.
- Initialize a fresh public repository and independent EnMotion namespace.
- Remove inherited production deployment automation.
- Rename product, package, protocol, process, storage, and release identifiers.
- Preserve license attribution.

Acceptance:

- No obsolete product identifiers in tracked text, paths, assets, or metadata.
- The two source repositories retain their original commits and clean diffs.
- Both applications can coexist without sharing ports, cookies, or storage.

## Phase 2 — Managed local data

- Resolve the OS application-data directory.
- Default all generated media to its private `enmotion-output` child.
- Scope projects and browser state by a stable remote account identifier.
- Keep refresh credentials in the OS credential store.
- Use atomic writes and backup-first metadata migrations.

Acceptance:

- Two accounts cannot see or mutate one another's local projects.
- Reinstall and update preserve output, settings, and credentials.
- Account switching cannot move an existing task to a different billing owner.

## Phase 3 — Account and credit control plane

- Implement authentication, session rotation/revocation, and no-public-signup.
- Implement users, roles, activation, password reset, and device sessions.
- Implement versioned rate cards and an immutable integer credit ledger.
- Implement reserve, settle, refund, adjustment, and reconciliation.
- Implement an allowlisted provider gateway and admin audit trail.
- Build a responsive static admin interface.

Acceptance:

- Concurrent charges cannot overspend or double-charge.
- Deactivated and revoked accounts cannot start new billable work.
- Every administrative balance change has an actor and reason.
- Provider credentials and generated media never persist on employee clients or
  the control-plane database, respectively.

## Phase 4 — Native desktop and updater

- Replace the development webview shell with Tauri 2.
- Package the Python API as a target-specific sidecar.
- Build macOS arm64/x86_64 and Windows x64 installers.
- Implement signed update checking, download progress, safe state flush,
  installation, and relaunch.

Acceptance:

- A clean user installs without Git or developer tooling.
- A real old-to-new update preserves login, settings, project data, and media.
- Interrupted or invalid updates leave the previous installation usable.
- Linux is absent from the desktop release matrix.

## Phase 5 — UX and performance

- Capture real running screenshots before judging inconsistencies.
- Repair semantic theme tokens and shared interaction primitives.
- Standardize dialogs, confirmation, focus, and responsive behavior.
- Self-host or replace remote fonts.
- Centralize duplicate polling and lazy-load optional heavy editors.

Acceptance:

- Core flows pass at phone, tablet, desktop, and wide-desktop viewports.
- No critical or serious automated accessibility violations.
- No eager heavy 3D/editor load and no polling storm.
- Production startup, bundle, memory, and interaction measurements do not
  regress.

## Phase 6 — Publication and deployment

- Run full source, frontend, control-plane, package, and update checks.
- Secret-scan the complete fresh history.
- Create and verify the public `paulyan678/EnMotion` repository.
- Publish signed macOS and Windows assets from one immutable
  `desktop-vX.Y.Z` tag, then verify the public download page.
- Test the control plane locally on macOS.
- Deploy the same lightweight artifact to the verified AlmaLinux VPS.
- Verify TLS, health, backup/restore, and the login-to-settlement workflow.

Unsigned installers remain test artifacts. Missing signing, DNS, GitHub, or
server credentials are reported as explicit release blockers rather than hidden
behind a success claim.
