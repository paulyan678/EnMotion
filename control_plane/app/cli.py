from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from sqlalchemy import func, select

from .audit import record_audit
from .config import Settings
from .database import Database, begin_immediate
from .models import CreditLedger, UsageRequest, User
from .security import hash_password, normalize_username


def bootstrap_admin(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    db = Database(settings.database_url)
    password_file = os.getenv("ENMOTION_BOOTSTRAP_PASSWORD_FILE", "").strip()
    if password_file:
        try:
            password = Path(password_file).read_text(encoding="utf-8").rstrip("\r\n")
        except OSError as exc:
            print(f"Unable to read bootstrap password file: {exc}", file=sys.stderr)
            return 2
    else:
        password = os.getenv("ENMOTION_BOOTSTRAP_PASSWORD") or getpass.getpass(
            "Initial administrator password: "
        )
    try:
        normalized = normalize_username(args.username)
        password_hash = hash_password(password)
    except ValueError as exc:
        print(f"Invalid bootstrap account: {exc}", file=sys.stderr)
        return 2
    with db.session() as session:
        begin_immediate(session)
        existing_admin = session.scalar(
            select(User).where(User.role == "admin").where(User.active.is_(True))
        )
        if existing_admin:
            print("An active administrator already exists; bootstrap refused.", file=sys.stderr)
            return 3
        if session.scalar(select(User).where(User.normalized_username == normalized)):
            print("The requested username already exists.", file=sys.stderr)
            return 3
        admin = User(
            username=args.username.strip(),
            normalized_username=normalized,
            password_hash=password_hash,
            role="admin",
            available_credits=0,
        )
        session.add(admin)
        session.flush()
        record_audit(
            session,
            actor_user_id=admin.id,
            action="system.admin_bootstrapped",
            target_type="user",
            target_id=admin.id,
            detail={"username": admin.username},
        )
        print(f"Created administrator {admin.username} ({admin.id}).")
    return 0


def check_ledger(_args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    db = Database(settings.database_url)
    problems: list[str] = []
    with db.session() as session:
        for user in session.scalars(select(User)).all():
            if user.available_credits < 0 or user.reserved_credits < 0:
                problems.append(f"{user.id}: negative materialized balance")
            last = session.scalar(
                select(CreditLedger)
                .where(CreditLedger.user_id == user.id)
                .order_by(CreditLedger.created_at.desc(), CreditLedger.id.desc())
            )
            if last and (
                last.available_after != user.available_credits
                or last.reserved_after != user.reserved_credits
            ):
                problems.append(f"{user.id}: ledger tail differs from materialized balance")
            active_reservations = session.scalar(
                select(func.coalesce(func.sum(UsageRequest.reserved_units), 0))
                .where(UsageRequest.user_id == user.id)
                .where(UsageRequest.status.in_(["reserved", "pending_reconciliation"]))
            )
            if int(active_reservations or 0) != user.reserved_credits:
                problems.append(f"{user.id}: active reservations differ from reserved balance")
    if problems:
        print("Ledger invariant check FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    print("Ledger invariant check passed.")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="EnMotion control-plane administration")
    commands = root.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap-admin")
    bootstrap.add_argument("--username", default="admin")
    bootstrap.set_defaults(handler=bootstrap_admin)
    check = commands.add_parser("check-ledger")
    check.set_defaults(handler=check_ledger)
    return root


def main() -> int:
    args = parser().parse_args()
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
