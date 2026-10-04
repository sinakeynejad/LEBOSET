import argparse
import hashlib
import secrets
from datetime import timedelta
from uuid import UUID

from sqlalchemy import delete

from leboset.infrastructure.database import TokenRow, UserRow, make_engine, now, session_factory


def provision_user(db, name: str) -> tuple[UserRow, str]:
    user = UserRow(name=name)
    db.add(user)
    db.flush()
    token = secrets.token_urlsafe(32)
    db.add(
        TokenRow(
            digest=hashlib.sha256(token.encode()).hexdigest(),
            user_id=user.id,
            expires_at=now() + timedelta(days=7),
        )
    )
    db.commit()
    return user, token


def main():
    parser = argparse.ArgumentParser(description="Local LEBOSET developer access")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-user", help="Create a local test user and seven-day API token")
    create.add_argument("--name", required=True)
    revoke = sub.add_parser("revoke-tokens", help="Revoke all access tokens for a user")
    revoke.add_argument("--user-id", required=True, type=UUID)
    args = parser.parse_args()
    engine = make_engine()
    try:
        with session_factory(engine)() as db:
            if args.command == "create-user":
                name = args.name.strip()
                if not 1 <= len(name) <= 100:
                    parser.error("Name must contain 1 to 100 characters")
                user, token = provision_user(db, name)
                print(f"User: {user.id}\nAccess token (shown once; expires in 7 days): {token}")
            else:
                db.execute(delete(TokenRow).where(TokenRow.user_id == args.user_id))
                db.commit()
                print("Tokens revoked.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
