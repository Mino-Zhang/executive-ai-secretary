from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


settings = get_settings()
engine_kwargs: dict[str, object] = {"pool_pre_ping": True}
if not settings.database_url.startswith("sqlite"):
    engine_kwargs.update(
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
    )
elif settings.database_url.endswith(":memory:"):
    from sqlalchemy.pool import StaticPool

    engine_kwargs.update(
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

engine = create_engine(settings.database_url, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
