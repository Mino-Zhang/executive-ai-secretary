from __future__ import annotations

from sqlalchemy import select

from .config import get_settings
from .database import SessionLocal
from .models import Memory
from .personal_data import set_memory_content


def migrate_plaintext_memories() -> int:
    """Encrypt legacy memory rows before normal runtime traffic resumes."""

    settings = get_settings()
    migrated = 0
    with SessionLocal.begin() as db:
        rows = db.scalars(
            select(Memory).where(
                Memory.content != "",
                Memory.content_ciphertext == "",
            )
        ).all()
        for row in rows:
            set_memory_content(row, row.content, settings)
            migrated += 1
    return migrated


def main() -> None:
    migrated = migrate_plaintext_memories()
    print(f"Encrypted {migrated} legacy memory rows.")


if __name__ == "__main__":
    main()
