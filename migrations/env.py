from logging.config import fileConfig

from alembic import context

from database import Base, engine


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ORM 模型创建后必须在这里导入，确保表已经注册到 Base.metadata。
# 例如：from models import Attraction  # noqa: F401
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """不创建数据库连接，仅根据 URL 生成 SQL。"""
    context.configure(
        url=engine.url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """使用应用的 SQLAlchemy Engine 执行迁移。"""
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
