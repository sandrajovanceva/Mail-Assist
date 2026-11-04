from sqlalchemy import create_engine, inspect, literal, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False  # SQLite + FastAPI threads

engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema() -> None:
    """Add columns that exist on the models but not yet in the database.

    `create_all` creates missing *tables* but never alters existing ones, so a
    database created by an earlier version of the app would be missing the
    newer columns. A full migration tool (Alembic) is overkill for a
    single-developer thesis project; this covers the one case that actually
    happens here — new nullable/defaulted columns on existing tables.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    # Read the current shape first — inspecting while a write transaction is
    # open would have the inspector's connection waiting on our own lock.
    columns_by_table = {
        table.name: {col["name"] for col in inspector.get_columns(table.name)}
        for table in Base.metadata.sorted_tables
        if table.name in existing_tables
    }
    indexes_by_table = {
        table.name: {idx["name"] for idx in inspector.get_indexes(table.name)}
        for table in Base.metadata.sorted_tables
        if table.name in existing_tables
    }

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # create_all handles brand-new tables
            present = columns_by_table[table.name]
            for column in table.columns:
                if column.name in present:
                    continue
                ddl = f"{column.name} {column.type.compile(engine.dialect)}"
                if column.default is not None and column.default.is_scalar:
                    # Let the dialect render the literal: Postgres needs `true`
                    # for a boolean column and rejects the `0` SQLite accepts.
                    rendered = (
                        literal(column.default.arg, type_=column.type)
                        .compile(dialect=engine.dialect, compile_kwargs={"literal_binds": True})
                    )
                    ddl += f" DEFAULT {rendered}"
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))

        # Indexes declared on the models but missing from an older database.
        # A unique index refuses to build if duplicates already exist, which is
        # the right outcome: silently tolerating them would be worse.
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            for index in table.indexes:
                if index.name not in indexes_by_table[table.name]:
                    index.create(bind=conn)
