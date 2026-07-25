from __future__ import annotations

import argparse
from pathlib import Path

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test a local EnMotion control plane")
    parser.add_argument("--base-url", default="http://127.0.0.1:18787")
    parser.add_argument("--username", default="admin")
    parser.add_argument(
        "--password-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".local" / "admin-password",
    )
    args = parser.parse_args()
    password = args.password_file.read_text(encoding="utf-8").rstrip("\r\n")
    with httpx.Client(base_url=args.base_url, timeout=10) as client:
        live = client.get("/health/live")
        live.raise_for_status()
        ready = client.get("/health/ready")
        ready.raise_for_status()
        admin_page = client.get("/admin/")
        admin_page.raise_for_status()
        if "EnMotion 管理中心" not in admin_page.text:
            raise RuntimeError("administrator page content check failed")
        admin_script = client.get("/admin/app.js")
        admin_script.raise_for_status()
        if (
            "refreshPromise" not in admin_script.text
            or "MODEL_CATALOG" not in admin_script.text
        ):
            raise RuntimeError("administrator application bundle check failed")
        login = client.post(
            "/api/v1/auth/login",
            json={
                "username": args.username,
                "password": password,
                "device_label": "本机冒烟测试",
            },
        )
        login.raise_for_status()
        payload = login.json()
        if payload["user"]["role"] != "admin":
            raise RuntimeError("bootstrap account is not an administrator")
        users = client.get(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {payload['access_token']}"},
        )
        users.raise_for_status()
        pending = client.get(
            "/api/v1/admin/usage",
            headers={"Authorization": f"Bearer {payload['access_token']}"},
            params={"usage_status": "pending_reconciliation"},
        )
        pending.raise_for_status()
        runtime = client.get("/api/v1/runtime-config")
        runtime.raise_for_status()
        if runtime.json()["app_name"] != "EnMotion":
            raise RuntimeError("runtime identity check failed")
    print(
        "Local smoke test passed: health, readiness, admin UI, login, users, "
        "pending reconciliation, runtime config"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
