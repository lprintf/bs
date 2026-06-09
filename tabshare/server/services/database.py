"""
Database models and engine for TabShare.
Uses SQLAlchemy async with PostgreSQL (asyncpg).
"""
import os
from datetime import datetime, timezone
from sqlalchemy import Boolean, Integer, String, DateTime, ForeignKey, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://tabshare:tabshare@localhost:5432/tabshare")

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class BrowserModel(Base):
    __tablename__ = "browsers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    proxy: Mapped[str | None] = mapped_column(String(500), nullable=True)
    user_data_dir: Mapped[str] = mapped_column(String(500), default="")
    chrome_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    restore_on_start: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "proxy": self.proxy,
            "user_data_dir": self.user_data_dir,
            "chrome_version": self.chrome_version,
            "restore_on_start": self.restore_on_start,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TabModel(Base):
    __tablename__ = "tabs"
    __table_args__ = (
        UniqueConstraint("browser_id", "tab_id", name="uq_tab_browser_tab"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    browser_id: Mapped[int] = mapped_column(Integer, ForeignKey("browsers.id", ondelete="CASCADE"), index=True)
    tab_id: Mapped[int] = mapped_column(Integer, nullable=False)
    url: Mapped[str] = mapped_column(String(2048), default="about:blank")
    viewport_width: Mapped[int] = mapped_column(Integer, default=1280)
    viewport_height: Mapped[int] = mapped_column(Integer, default=720)
    created_by: Mapped[str] = mapped_column(String(50), default="admin")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "browser_id": self.browser_id,
            "tab_id": self.tab_id,
            "url": self.url,
            "viewport": {
                "width": self.viewport_width,
                "height": self.viewport_height,
            },
            "created_by": self.created_by,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class WorkbenchModel(Base):
    __tablename__ = "workbenches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    active_browser_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_tab_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recording_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    recording_room: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recording_file: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "active_browser_id": self.active_browser_id,
            "active_tab_id": self.active_tab_id,
            "active_room": (
                f"{self.active_browser_id}_{self.active_tab_id}"
                if self.active_browser_id is not None and self.active_tab_id is not None
                else None
            ),
            "recording_enabled": self.recording_enabled,
            "recording_room": self.recording_room,
            "recording_file": self.recording_file,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class WorkbenchTabModel(Base):
    __tablename__ = "workbench_tabs"
    __table_args__ = (
        UniqueConstraint("browser_id", "tab_id", name="uq_workbench_tab_ref"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workbench_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workbenches.id", ondelete="CASCADE"), index=True
    )
    browser_id: Mapped[int] = mapped_column(Integer, nullable=False)
    tab_id: Mapped[int] = mapped_column(Integer, nullable=False)
    allowed_hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workbench_id": self.workbench_id,
            "browser_id": self.browser_id,
            "tab_id": self.tab_id,
            "allowed_hostname": self.allowed_hostname,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


async def init_db():
    """Create all tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(
            "ALTER TABLE browsers "
            "ADD COLUMN IF NOT EXISTS restore_on_start BOOLEAN NOT NULL DEFAULT FALSE"
        ))
        await conn.execute(text(
            "ALTER TABLE workbenches "
            "ADD COLUMN IF NOT EXISTS active_browser_id INTEGER"
        ))
        await conn.execute(text(
            "ALTER TABLE workbenches "
            "ADD COLUMN IF NOT EXISTS active_tab_id INTEGER"
        ))
        await conn.execute(text(
            "ALTER TABLE workbenches "
            "ADD COLUMN IF NOT EXISTS recording_enabled BOOLEAN NOT NULL DEFAULT FALSE"
        ))
        await conn.execute(text(
            "ALTER TABLE workbenches "
            "ADD COLUMN IF NOT EXISTS recording_room VARCHAR(255)"
        ))
        await conn.execute(text(
            "ALTER TABLE workbenches "
            "ADD COLUMN IF NOT EXISTS recording_file VARCHAR(1024)"
        ))
    print("[database] tables initialized")


async def get_session() -> AsyncSession:
    async with async_session() as session:
        return session
