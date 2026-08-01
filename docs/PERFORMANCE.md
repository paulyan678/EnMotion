# EnMotion performance contract

Performance work is accepted only when the affected workflow is measured in a
production build. Unit tests and a successful compile are necessary but do not
prove startup, interaction, generation, or packaged-application performance.

## Reference workloads

Use deterministic metadata and non-sensitive media fixtures:

| Profile | Projects | Assets | Activity rows |
| --- | ---: | ---: | ---: |
| Small | 1 | 20 | 50 |
| Medium | 20 | 1,000 | 500 |
| Large | 100 | 10,000 | 10,000 |

The packaged macOS application is measured on the current development Mac and
an Apple Silicon baseline with 8 GiB memory. Record the hardware, OS, EnMotion
version, commit, cold/warm state, dataset profile, and five or more samples.

## Release budgets

| Metric | Initial budget |
| --- | ---: |
| Generation click to durable Activity entry, p95 | 500 ms |
| Local job admission, p95 | 250 ms |
| Small/medium local read, p95 | 100 ms |
| Large-project read, p95 | 250 ms |
| Cold UI ready, p95 | 12 s |
| Warm UI ready, p95 | 6 s |
| Shell plus sidecar idle RSS | 200 MiB |
| Installed macOS application | 300 MiB |
| Static frontend export | 8 MiB |
| Uncompressed frontend JavaScript | 3.4 MiB |
| Largest JavaScript chunk | 950,000 bytes |
| Uncompressed frontend CSS | 160 KiB |
| Style thumbnails | 2 MiB |

Startup and memory budgets begin as reported gates. They become blocking once
two reproducible release measurements pass. Frontend bundle and style budgets
are enforced immediately by `npm run check:bundle` and `npm run check:assets`.

## Generation reliability matrix

Exercise every model exposed for each eligible surface and modality:

- Surfaces: Playground, Workspace, Asset Library.
- Modalities: text, image, image editing, text-to-video, image-to-video.
- Lifecycles: success, rejection, timeout, rate limit, cancellation, retry,
  navigation, sleep/wake, sidecar restart, and application restart.

Every accepted request must create a visible Activity lifecycle, retain one
idempotency identity, settle or refund exactly once, write beneath the app data
output root, and provide a bounded typed failure when it cannot complete.

## Required evidence

Each optimization release records:

1. Before/after production-build measurements.
2. Full automated test and security-gate results.
3. Packaged-app UI smoke results and sanitized task identifiers.
4. Published artifact size, checksum, version, bundle identifier, and startup.
5. Any budget still unmet; releases must not be described as fully optimized
   while an accepted budget remains open.
