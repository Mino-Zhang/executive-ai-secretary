from __future__ import annotations

import pytest

from executive_ai_api.migration_compatibility import (
    IncompatibleMigrationError,
    _default_config_path,
    supported_upgrade_head,
)
from executive_ai_api.routers.health import EXPECTED_DATABASE_REVISION


@pytest.mark.parametrize(
    "backup_revision",
    [
        "902b75c8e14e",
        "7c4a9e1d2f30",
        "a83f4c91d720",
        EXPECTED_DATABASE_REVISION,
    ],
)
def test_known_ancestor_can_upgrade_to_the_only_supported_head(backup_revision: str) -> None:
    assert supported_upgrade_head(backup_revision) == EXPECTED_DATABASE_REVISION


@pytest.mark.parametrize("backup_revision", ["", "future_revision", "c5d91f4"])
def test_unknown_empty_or_abbreviated_revision_is_rejected(backup_revision: str) -> None:
    with pytest.raises(IncompatibleMigrationError):
        supported_upgrade_head(backup_revision)


def test_runtime_config_resolver_finds_config_and_migration_directory() -> None:
    config_path = _default_config_path()
    assert config_path.name == "alembic.ini"
    assert config_path.is_file()
    assert (config_path.parent / "alembic").is_dir()
