import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DATABASE_URL = os.getenv("DATABASE_URL") or URL.create(
    "mysql+pymysql",
    username=os.getenv("MYSQL_USER", "rapideiz"),
    password=os.getenv("MYSQL_PASSWORD", "rapideiz123"),
    host=os.getenv("MYSQL_HOST", "127.0.0.1"),
    port=int(os.getenv("MYSQL_PORT", "3307")),
    database=os.getenv("MYSQL_DATABASE", "rapideiz"),
    query={"charset": "utf8mb4"},
)
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=1800)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    with SessionLocal() as session:
        yield session


def initialize_database():
    Base.metadata.create_all(engine)
    if engine.dialect.name != "mysql":
        return

    # Atualiza bancos criados pela versão anterior, preservando os pedidos.
    columns = inspect(engine).get_columns("orders")
    total_type = next(column["type"] for column in columns if column["name"] == "total")
    if total_type.__class__.__name__ != "DECIMAL":
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE orders MODIFY total DECIMAL(10, 2) NOT NULL"))
