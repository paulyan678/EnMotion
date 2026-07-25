# Contributing to EnMotion

EnMotion is maintained in a public source repository. Contributions must never
include company credentials, employee data, generated media, databases, signing
material, or any other non-public operational data.

## Workflow

1. Fork the repository or branch from the current `main`.
2. Keep the change narrowly scoped.
3. Run the affected Python, frontend, control-plane, and desktop checks.
4. Run the repository secret and obsolete-namespace scans.
5. Open a pull request with behavior and verification evidence.
6. Merge only after hosted checks are green.

Use Conventional Commit prefixes such as `feat:`, `fix:`, `test:`, `docs:`,
`refactor:`, and `chore:`.

## Invariants

- Generated media remains local under `Documents/enmotion-output` unless the user
  explicitly chooses another stable folder.
- Company provider credentials remain in the control plane.
- Employee clients cannot bypass server-authoritative rates or credit
  reservations.
- Credit values are integers and ledger history is append-only.
- Non-idempotent provider submissions are not retried implicitly.
- macOS and Windows are the only desktop release targets.
- Updates preserve user data and require signed metadata.
- Production installers are published only from immutable `desktop-vX.Y.Z`
  tags in the public EnMotion GitHub repository.
- The local sidecar is loopback-only and requires a per-launch nonce.
- Existing license attribution remains intact.

## Verification

At minimum:

```bash
python -m pytest
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run test:all
npm --prefix frontend run check:colors
npm --prefix frontend run build
```

Also run `control_plane` tests for account/credit/provider changes and Tauri
configuration/build checks for desktop or updater changes.

Never weaken a check, hide a warning, or mark unsigned artifacts as production
releases merely to make a build green.
