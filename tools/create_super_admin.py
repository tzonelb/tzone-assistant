import getpass
import sys

from backend.services.auth_service import auth_service
from config.settings import config
from database.database import db


def ask_required(
    label: str,
) -> str:
    while True:
        value = input(label).strip()

        if value:
            return value

        print("This value is required.")


def main():
    db.create_tables()
    auth_service.create_tables()

    print("=" * 60)
    print("CREATE T-ZONE SUPER ADMIN")
    print("=" * 60)

    email = ask_required(
        "Email: "
    )

    full_name = ask_required(
        "Full name: "
    )

    password = getpass.getpass(
        "Password: "
    )

    password_confirmation = getpass.getpass(
        "Confirm password: "
    )

    if password != password_confirmation:
        print("Passwords do not match.")
        sys.exit(1)

    if len(password) < 8:
        print(
            "Password must contain at least 8 characters."
        )
        sys.exit(1)

    try:
        user_id = auth_service.create_user(
            email=email,
            password=password,
            full_name=full_name,
            is_super_admin=True,
        )

        auth_service.assign_user_to_company(
            user_id=user_id,
            company_id=config.DEFAULT_COMPANY_ID,
            role_code="owner",
            branch_id=config.DEFAULT_BRANCH_ID,
        )

        print()
        print("✅ SUPER ADMIN CREATED")
        print(f"User ID: {user_id}")
        print(f"Email: {email}")
        print(
            f"Company ID: {config.DEFAULT_COMPANY_ID}"
        )

    except ValueError as error:
        print(f"❌ {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()