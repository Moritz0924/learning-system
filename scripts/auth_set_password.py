from __future__ import annotations

import argparse
import getpass
import sys

from backend.app.application.auth_service import AuthError, AuthService
from backend.app.core.security import auth_settings
from backend.app.db import SessionLocal


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Set a password for one historical account that has no password credentials."
    )
    parser.add_argument("--email", required=True, help="Existing account email address")
    args = parser.parse_args()

    password = getpass.getpass("New password: ")
    confirmation = getpass.getpass("Confirm new password: ")
    if password != confirmation:
        print("Passwords do not match.", file=sys.stderr)
        return 2

    with SessionLocal() as session:
        try:
            user = AuthService(session, auth_settings()).set_legacy_password(
                email=args.email,
                password=password,
            )
        except AuthError as exc:
            print(f"Password was not changed: {exc}", file=sys.stderr)
            return 1

    print(f"Password set and existing sessions revoked for user {user.id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
