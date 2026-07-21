import sqlite3
from pathlib import Path

from config.settings import config


REQUIRED_USER_COLUMNS = {
    "email": "TEXT",
    "password_hash": "TEXT",
    "full_name": "TEXT",
    "phone": "TEXT",
    "status": "TEXT NOT NULL DEFAULT 'active'",
    "is_super_admin": "INTEGER NOT NULL DEFAULT 0",
    "last_login_at": "TIMESTAMP",
    "created_at": "TIMESTAMP",
    "updated_at": "TIMESTAMP",
}


def get_database_path() -> Path:
    database_path = Path(config.DATABASE_PATH)
    database_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    return database_path


def get_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> set[str]:
    rows = connection.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()

    return {
        row[1]
        for row in rows
    }


def table_exists(
    connection: sqlite3.Connection,
    table_name: str,
) -> bool:
    row = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()

    return row is not None


def create_users_table(
    connection: sqlite3.Connection,
):
    connection.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE,
            password_hash TEXT,
            full_name TEXT,
            phone TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            is_super_admin INTEGER NOT NULL DEFAULT 0,
            last_login_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def migrate_users_table(
    connection: sqlite3.Connection,
):
    if not table_exists(connection, "users"):
        print("Creating users table...")
        create_users_table(connection)
        return

    current_columns = get_columns(
        connection,
        "users",
    )

    print(
        "Existing users columns:",
        ", ".join(sorted(current_columns)),
    )

    for column_name, definition in REQUIRED_USER_COLUMNS.items():
        if column_name in current_columns:
            continue

        print(f"Adding users.{column_name}...")

        connection.execute(
            f"""
            ALTER TABLE users
            ADD COLUMN {column_name} {definition}
            """
        )


def create_auth_sessions_table(
    connection: sqlite3.Connection,
):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TIMESTAMP NOT NULL,
            revoked_at TIMESTAMP,
            ip_address TEXT,
            user_agent TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id)
                REFERENCES users(id)
                ON DELETE CASCADE
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_auth_sessions_user
        ON auth_sessions(user_id)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_auth_sessions_token
        ON auth_sessions(token_hash)
        """
    )


def create_unique_email_index(
    connection: sqlite3.Connection,
):
    duplicate = connection.execute(
        """
        SELECT LOWER(email), COUNT(*)
        FROM users
        WHERE email IS NOT NULL
          AND TRIM(email) != ''
        GROUP BY LOWER(email)
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    ).fetchone()

    if duplicate:
        print(
            "Warning: duplicate emails exist. "
            "Unique email index was not created."
        )
        return

    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_users_email_unique
        ON users(LOWER(email))
        WHERE email IS NOT NULL
          AND TRIM(email) != ''
        """
    )


def normalize_existing_rows(
    connection: sqlite3.Connection,
):
    columns = get_columns(
        connection,
        "users",
    )

    connection.execute(
        """
        UPDATE users
        SET status = 'active'
        WHERE status IS NULL
           OR TRIM(status) = ''
        """
    )

    connection.execute(
        """
        UPDATE users
        SET is_super_admin = 0
        WHERE is_super_admin IS NULL
        """
    )

    if "name" in columns:
        connection.execute(
            """
            UPDATE users
            SET full_name = name
            WHERE (
                full_name IS NULL
                OR TRIM(full_name) = ''
            )
              AND name IS NOT NULL
            """
        )

    if "username" in columns:
        connection.execute(
            """
            UPDATE users
            SET email = username
            WHERE (
                email IS NULL
                OR TRIM(email) = ''
            )
              AND username LIKE '%@%'
            """
        )


def show_result(
    connection: sqlite3.Connection,
):
    columns = get_columns(
        connection,
        "users",
    )

    print()
    print("Users table columns after migration:")

    for column in sorted(columns):
        print(f" - {column}")

    users = connection.execute(
        """
        SELECT
            id,
            email,
            full_name,
            status,
            is_super_admin
        FROM users
        ORDER BY id
        """
    ).fetchall()

    print()
    print(f"Existing users: {len(users)}")

    for user in users:
        print(
            {
                "id": user[0],
                "email": user[1],
                "full_name": user[2],
                "status": user[3],
                "is_super_admin": bool(user[4]),
            }
        )


def main():
    database_path = get_database_path()

    print("=" * 60)
    print("T-ZONE AUTH DATABASE MIGRATION")
    print("=" * 60)
    print(f"Database: {database_path}")
    print()

    connection = sqlite3.connect(
        database_path,
        timeout=30,
    )

    try:
        connection.execute(
            "PRAGMA foreign_keys = ON"
        )

        migrate_users_table(connection)
        normalize_existing_rows(connection)
        create_auth_sessions_table(connection)
        create_unique_email_index(connection)

        connection.commit()

        show_result(connection)

        print()
        print("AUTH DATABASE MIGRATION COMPLETED")

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


if __name__ == "__main__":
    main()