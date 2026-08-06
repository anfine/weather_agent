import os
from collections.abc import Iterator
from contextlib import contextmanager

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


load_dotenv()


def _database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("缺少 DATABASE_URL 环境变量")
    return database_url


class Base(DeclarativeBase):
    """所有 ORM 模型共用的声明式基类。"""


engine = create_engine(
    _database_url(),
    pool_pre_ping=True,
    pool_recycle=1800,
)

SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    autoflush=False,
    expire_on_commit=False,
)


@contextmanager
def session_scope() -> Iterator[Session]:
    """提供带提交、回滚和关闭语义的数据库会话。"""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database_connection() -> None:
    """执行轻量查询；连接失败时保留原始数据库异常。"""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


if __name__ == "__main__":
    check_database_connection()
    print("Database connection successful.")
