from datetime import datetime, timezone

from sqlalchemy import Connection, Engine, inspect, text


def migrate_schema(engine: Engine) -> None:
    with engine.begin() as connection:
        schema_name = _current_schema(connection, engine.dialect.name)
        _migrate_schema(connection, schema_name, engine.dialect.name)


def _current_schema(connection: Connection, dialect_name: str) -> str | None:
    if dialect_name == "postgresql":
        return connection.scalar(text("SELECT current_schema()"))
    return None


def _table_names(connection: Connection, schema_name: str | None) -> set[str]:
    return set(inspect(connection).get_table_names(schema=schema_name))


def _column_names(
    connection: Connection, table_name: str, schema_name: str | None
) -> set[str]:
    return {
        column["name"]
        for column in inspect(connection).get_columns(
            table_name, schema=schema_name
        )
    }


def _migrate_schema(
    connection: Connection, schema_name: str | None, dialect_name: str
) -> None:
    tables = _table_names(connection, schema_name)
    if "matches" in tables:
        match_columns = _column_names(connection, "matches", schema_name)
        missing_columns = {
            "updated_at",
            "data_origin",
            "source_name",
            "source_reference",
        } - match_columns
        if missing_columns:
            if dialect_name not in ("sqlite", "postgresql"):
                raise RuntimeError(
                    f"Schema migration is not supported for {dialect_name}."
                )
            if "updated_at" not in match_columns:
                if dialect_name == "sqlite":
                    connection.exec_driver_sql(
                        "ALTER TABLE matches ADD COLUMN updated_at DATETIME "
                        "NOT NULL DEFAULT '1970-01-01 00:00:00'"
                    )
                    connection.exec_driver_sql(
                        "UPDATE matches SET updated_at = kickoff_at"
                    )
                else:
                    connection.exec_driver_sql(
                        "ALTER TABLE matches ADD COLUMN updated_at "
                        "TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP"
                    )
            if "data_origin" not in match_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE matches ADD COLUMN data_origin VARCHAR(12) "
                    "NOT NULL DEFAULT 'SYNTHETIC' "
                    "CHECK (data_origin IN ('SIMULATED', 'SYNTHETIC', 'HISTORICAL'))"
                )
                connection.exec_driver_sql(
                    "UPDATE matches SET data_origin = "
                    "CASE WHEN is_simulated THEN 'SYNTHETIC' "
                    "ELSE 'HISTORICAL' END"
                )
            if "source_name" not in match_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE matches ADD COLUMN source_name VARCHAR(120) "
                    "NOT NULL DEFAULT 'SportsOps legacy database'"
                )
                connection.exec_driver_sql(
                    "UPDATE matches SET source_name = CASE "
                    "WHEN data_origin = 'SYNTHETIC' THEN "
                    "'SportsOps synthetic legacy data' "
                    "WHEN data_origin = 'SIMULATED' THEN "
                    "'SportsOps simulated legacy data' "
                    "ELSE 'Unverified legacy source' END"
                )
            if "source_reference" not in match_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE matches ADD COLUMN source_reference VARCHAR(255)"
                )

    tables = _table_names(connection, schema_name)
    if "trading_audit_log" in tables:
        if dialect_name == "sqlite":
            connection.exec_driver_sql(
                "CREATE TRIGGER IF NOT EXISTS trg_trading_audit_no_update "
                "BEFORE UPDATE ON trading_audit_log "
                "BEGIN SELECT RAISE(ABORT, 'trading_audit_log is append-only'); END"
            )
            connection.exec_driver_sql(
                "CREATE TRIGGER IF NOT EXISTS trg_trading_audit_no_delete "
                "BEFORE DELETE ON trading_audit_log "
                "BEGIN SELECT RAISE(ABORT, 'trading_audit_log is append-only'); END"
            )
        elif dialect_name == "postgresql":
            connection.exec_driver_sql(
                "CREATE OR REPLACE FUNCTION "
                "sportsops_reject_trading_audit_change() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN "
                "RAISE EXCEPTION 'trading_audit_log is append-only'; "
                "END; $$"
            )
            connection.exec_driver_sql(
                "DROP TRIGGER IF EXISTS trg_trading_audit_no_mutation "
                "ON trading_audit_log"
            )
            connection.exec_driver_sql(
                "CREATE TRIGGER trg_trading_audit_no_mutation "
                "BEFORE UPDATE OR DELETE ON trading_audit_log "
                "FOR EACH ROW EXECUTE FUNCTION "
                "sportsops_reject_trading_audit_change()"
            )
        else:
            raise RuntimeError(
                f"Audit-log migration is not supported for {dialect_name}."
            )

    tables = _table_names(connection, schema_name)
    if "data_quality_alerts" in tables:
        alert_columns = _column_names(
            connection, "data_quality_alerts", schema_name
        )
        if "acknowledged_at" not in alert_columns:
            if dialect_name == "sqlite":
                connection.exec_driver_sql(
                    "ALTER TABLE data_quality_alerts "
                    "ADD COLUMN acknowledged_at DATETIME"
                )
            elif dialect_name == "postgresql":
                connection.exec_driver_sql(
                    "ALTER TABLE data_quality_alerts "
                    "ADD COLUMN acknowledged_at TIMESTAMP WITH TIME ZONE"
                )
            else:
                raise RuntimeError(
                    f"Schema migration is not supported for {dialect_name}."
                )

    tables = _table_names(connection, schema_name)
    append_only_tables = (
        "simulation_events",
        "simulation_odds_history",
        "simulation_audit_log",
        "trading_risk_occurrences",
    )
    existing_append_only_tables = [
        table for table in append_only_tables if table in tables
    ]
    if existing_append_only_tables:
        if dialect_name == "sqlite":
            for table in existing_append_only_tables:
                safe_table = table.replace("_", "")
                connection.exec_driver_sql(
                    f"CREATE TRIGGER IF NOT EXISTS trg_{safe_table}_no_update "
                    f"BEFORE UPDATE ON {table} "
                    "BEGIN SELECT RAISE(ABORT, "
                    f"'{table} is append-only'); END"
                )
                connection.exec_driver_sql(
                    f"CREATE TRIGGER IF NOT EXISTS trg_{safe_table}_no_delete "
                    f"BEFORE DELETE ON {table} "
                    "BEGIN SELECT RAISE(ABORT, "
                    f"'{table} is append-only'); END"
                )
        elif dialect_name == "postgresql":
            connection.exec_driver_sql(
                "CREATE OR REPLACE FUNCTION "
                "sportsops_reject_simulation_ledger_change() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN "
                "RAISE EXCEPTION 'simulation ledger tables are append-only'; "
                "END; $$"
            )
            for table in existing_append_only_tables:
                safe_table = table.replace("_", "")
                trigger = f"trg_{safe_table}_no_mutation"
                connection.exec_driver_sql(
                    f"DROP TRIGGER IF EXISTS {trigger} ON {table}"
                )
                connection.exec_driver_sql(
                    f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {table} "
                    "FOR EACH ROW EXECUTE FUNCTION "
                    "sportsops_reject_simulation_ledger_change()"
                )
        else:
            raise RuntimeError(
                "Append-only ledger migration is not supported for "
                f"{dialect_name}."
            )

    _migrate_trading_risk_incidents(connection, schema_name, dialect_name)


def _migrate_trading_risk_incidents(
    connection: Connection, schema_name: str | None, dialect_name: str
) -> None:
    tables = _table_names(connection, schema_name)
    if "trading_risk_incidents" not in tables:
        return

    rows = connection.exec_driver_sql(
        "SELECT id, first_seen_at, last_seen_at FROM trading_risk_incidents"
    ).all()
    inconsistent_ids = [
        row[0]
        for row in rows
        if _as_utc_datetime(row[2]) < _as_utc_datetime(row[1])
    ]
    if inconsistent_ids:
        raise RuntimeError(
            "Trading risk incidents have Last Seen earlier than First Seen "
            f"(incident IDs: {inconsistent_ids[:10]}). No timestamps were changed."
        )

    columns = _column_names(connection, "trading_risk_incidents", schema_name)
    if "run_id" not in columns and "simulation_runs" not in tables:
        raise RuntimeError(
            "Cannot add run-scoped risk incidents because simulation_runs "
            "does not exist."
        )

    indexes = {
        index["name"]: index
        for index in inspect(connection).get_indexes(
            "trading_risk_incidents", schema=schema_name
        )
    }
    open_index = indexes.get("uq_trading_risk_incidents_open_key")
    expected_index_columns = [
        "run_id",
        "match_id",
        "market_id",
        "selection_id",
        "rule_code",
    ]
    if "run_id" not in columns:
        if dialect_name not in ("sqlite", "postgresql"):
            raise RuntimeError(
                "Run-scoped risk incident migration is not supported for "
                f"{dialect_name}."
            )
        connection.exec_driver_sql(
            "ALTER TABLE trading_risk_incidents ADD COLUMN run_id "
            "VARCHAR(32) REFERENCES simulation_runs(run_id) ON DELETE RESTRICT"
        )

    if dialect_name == "postgresql":
        check_names = {
            constraint["name"]
            for constraint in inspect(connection).get_check_constraints(
                "trading_risk_incidents", schema=schema_name
            )
        }
        if "ck_trading_risk_incidents_chronology" not in check_names:
            connection.exec_driver_sql(
                "ALTER TABLE trading_risk_incidents ADD CONSTRAINT "
                "ck_trading_risk_incidents_chronology "
                "CHECK (last_seen_at >= first_seen_at)"
            )

    if open_index and (
        open_index.get("column_names") != expected_index_columns
        or not open_index.get("unique")
    ):
        connection.exec_driver_sql(
            "DROP INDEX uq_trading_risk_incidents_open_key"
        )
        open_index = None
    if open_index is None:
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX uq_trading_risk_incidents_open_key "
            "ON trading_risk_incidents "
            "(run_id, match_id, market_id, selection_id, rule_code) "
            "WHERE status = 'OPEN'"
        )


def _as_utc_datetime(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
