from __future__ import annotations

import argparse
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError


class IncompatibleMigrationError(RuntimeError):
    """Raised when a backup cannot be upgraded by this release's migration chain."""


def _default_config_path() -> Path:
    # Editable development installs keep this module under ``src/`` while the
    # production image installs it into ``.venv/site-packages``. In the latter
    # layout the migration assets deliberately remain at ``/app`` rather than
    # being copied into the wheel, so first honor the image working directory
    # and then search source-layout parents.
    module_path = Path(__file__).resolve()
    candidates = [Path.cwd() / "alembic.ini"]
    candidates.extend(parent / "alembic.ini" for parent in module_path.parents)
    for candidate in candidates:
        if candidate.is_file() and (candidate.parent / "alembic").is_dir():
            return candidate
    raise IncompatibleMigrationError(
        "release image does not contain alembic.ini and its migration directory"
    )


def supported_upgrade_head(
    backup_revision: str,
    *,
    config_path: Path | None = None,
) -> str:
    """Return the sole supported head when ``backup_revision`` is its ancestor.

    Phase-one restores intentionally support a single, linear Alembic history.
    A backup from an unknown revision, a future release, or a divergent branch is
    rejected before the destination database or file volume is modified.
    """

    normalized_revision = backup_revision.strip()
    if not normalized_revision:
        raise IncompatibleMigrationError("backup Alembic revision is empty")

    resolved_config = (config_path or _default_config_path()).resolve()
    config = Config(str(resolved_config))
    config.set_main_option("script_location", str(resolved_config.parent / "alembic"))
    try:
        script = ScriptDirectory.from_config(config)
    except CommandError as exc:
        raise IncompatibleMigrationError("release migration graph could not be loaded") from exc
    heads = script.get_heads()
    if len(heads) != 1:
        raise IncompatibleMigrationError(
            f"release must expose exactly one Alembic head, found {len(heads)}"
        )

    try:
        backup_script = script.get_revision(normalized_revision)
    except CommandError as exc:
        raise IncompatibleMigrationError(
            f"backup Alembic revision {normalized_revision!r} is unknown to this release"
        ) from exc
    if backup_script is None or backup_script.revision != normalized_revision:
        raise IncompatibleMigrationError(
            f"backup Alembic revision {normalized_revision!r} must be a full known revision"
        )

    head = heads[0]
    cursor: str | None = head
    visited: set[str] = set()
    while cursor is not None:
        if cursor in visited:
            raise IncompatibleMigrationError("Alembic migration history contains a cycle")
        visited.add(cursor)
        if cursor == normalized_revision:
            return head

        revision = script.get_revision(cursor)
        if revision is None:
            break
        down_revision = revision.down_revision
        if down_revision is None:
            cursor = None
        elif isinstance(down_revision, str):
            cursor = down_revision
        else:
            raise IncompatibleMigrationError(
                "branched Alembic histories require a reviewed restore migration"
            )

    raise IncompatibleMigrationError(
        f"backup Alembic revision {normalized_revision!r} is not an ancestor of {head!r}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check whether a backup revision can upgrade to this release"
    )
    parser.add_argument("backup_revision")
    args = parser.parse_args()
    try:
        head = supported_upgrade_head(args.backup_revision)
    except IncompatibleMigrationError as exc:
        parser.exit(2, f"incompatible backup migration: {exc}\n")
    print(head)


if __name__ == "__main__":
    main()
