from sqlalchemy import event
from sqlalchemy.engine import Engine


def enable_sqlite_foreign_keys(engine: Engine) -> None:
    """SQLite ignores ON DELETE/ON UPDATE clauses unless foreign key
    enforcement is turned on for every connection. Postgres enforces these
    natively, so this is a no-op there.
    """

    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
