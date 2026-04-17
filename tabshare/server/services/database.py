"""
Database models and engine for TabShare.
Uses SQLAlchemy async with PostgreSQL (asyncpg).
"""
import os
from datetime import datetime, timezone
from sqlalchemy import Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
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
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


async def init_db():
    """Create all tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[database] tables initialized")


async def get_session() -> AsyncSession:
    async with async_session() as session:
        return session
