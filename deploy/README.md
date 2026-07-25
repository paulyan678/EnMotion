# EnMotion portable server deployment

This directory is the portable application deployment entrypoint for
`enmotion-web`.
It runs the same source bundle on Apple Silicon Macs and AMD64/ARM64 Linux
servers by building Linux images natively on the destination host.

Quick start on a Mac with Docker Desktop:

```sh
deploy/bin/bootstrap --mode mac
deploy/bin/doctor --mode mac
```

The bootstrap command creates `deploy/.env.server` with owner-only permissions,
generates database/session secrets, builds all images, applies migrations, asks
for the first administrator, and starts the stack at
`http://127.0.0.1:8080`.

Do not commit or copy `deploy/.env.server` into an application archive. See
[`docs/PORTABLE_SERVER_DEPLOYMENT.md`](../docs/PORTABLE_SERVER_DEPLOYMENT.md)
for production, backup, restore, transfer, and troubleshooting instructions.
If Alibaba OSS is enabled, its bucket is external state and must be backed up or
made available separately; EnMotion state archives do not copy OSS objects.
