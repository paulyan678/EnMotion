# Frontend lint policy

`npm run lint` treats every ESLint error as a blocking failure. In particular,
`react-hooks/rules-of-hooks` remains an error and must never be suppressed by the
incremental cleanup policy.

The project uses ESLint 9 flat config in `eslint.config.mjs`, as required by
Next.js 16. Framework, Core Web Vitals, React, Hooks, and TypeScript rules come
from `eslint-config-next`; project overrides are intentionally limited to the
legacy rules below and explicit correctness severities.

Two TypeScript rules have substantial pre-existing debt and are temporarily
warnings:

- `@typescript-eslint/no-explicit-any`
- `@typescript-eslint/no-unused-vars`

Next.js 16 also enables React Compiler-readiness rules that were not present in
the previous lint stack. The migration fixed all findings from
`preserve-manual-memoization`, `purity`, `refs`, and `static-components`; those
rules now use the framework's blocking defaults. The foundational
`react-hooks/rules-of-hooks` rule also remains a blocking error.

Two compiler-readiness classes still have explicit, decrease-only warning
budgets:

- `react-hooks/set-state-in-effect` (21): these are concrete synchronization
  boundaries, not a blanket exemption. Request/open lifecycle components
  (`ReconcileModal`, the project/series config dialogs, `AssetPickerModal`,
  `SettingsPage`, and `SeriesArtDirectionPanel`) clear stale form/error state and
  enter loading before a fetch. Editable project surfaces (`ArtDirection`,
  `CharacterWorkbench`, `ScriptProcessor`, and `AssetLibraryPage`) reconcile
  backend revisions or invalid selections with local drafts; removing those
  effects safely requires dirty/revision tracking so polling cannot clobber user
  edits. `StoryboardR2V` reconciles project-level task completion into local shot
  state; its updater is now pure and auto-select requests are deduplicated, but a
  single-source state redesign is needed to remove the boundary. The playground
  history/template components intentionally bridge store visibility to exit
  animation state.
- `react-hooks/immutability` (1): `CompareModal` seeks synchronized
  `HTMLMediaElement`s by assigning `currentTime` from a user event handler. That
  imperative browser media API mutation is required and does not mutate React
  render data.

These residuals stay visible at exact per-rule ceilings; new occurrences fail
lint. Future cleanup should use keyed modal content, explicit request state, and
dirty/revision-aware editor models rather than suppressing the rules inline.

All warnings are governed by the per-rule ceilings in
`.eslint-warning-baseline.json`. A warning from an unlisted rule has a ceiling of
zero, so introducing either a new warning class or more warnings in a tracked
class fails the lint gate. Fixes should lower the corresponding ceiling in the
same change; ceilings may never be raised to accommodate new debt.

This keeps the current codebase shippable while making the policy incremental:
existing debt is visible, React correctness errors stay blocking, and the
warning budget can only ratchet down.
