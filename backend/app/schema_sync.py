"""Additive schema reconciliation at startup.

`Base.metadata.create_all` creates tables that do not exist yet and stops
there: a column added to an EXISTING table is silently never applied. This
deployment has no migration tool, so every column added since the first boot
had to be applied to production by hand -- and the failure mode when someone
forgets is not a startup error but an `UndefinedColumn` raised from whichever
request first reads it, hours later.

The same hole exists for Postgres ENUM TYPES: a new member added to a Python
enum that backs a `sa.Enum` column is never added to the type, and the failure
surfaces as `invalid input value for enum fix_status` on the first row that
uses it.

This closes both gaps for the only cases that are safe to automate: adding a
column the models declare and the database lacks, and adding an enum label the
models declare and the type lacks. It never drops a column or a label, never
changes a type, and never touches a constraint -- anything beyond "this is
missing" is left alone and reported, because guessing at a destructive
migration is far worse than an honest log line.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.schema import CreateColumn

from app.db import Base

logger = logging.getLogger("whipguard.schema_sync")


def _existing_columns(rows) -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for table_name, column_name in rows:
        tables.setdefault(table_name, set()).add(column_name)
    return tables


async def sync_enum_labels(conn) -> list[str]:
    """Add every enum label the models declare and the database type lacks.

    `create_all` creates an enum type the first time and never revisits it, so
    adding a member to a Python enum leaves the Postgres type behind. Nothing
    fails at startup; the first INSERT carrying the new value fails instead,
    with `invalid input value for enum <type>`.

    Additive only. A label that exists in the database but not in the models
    is left alone: rows may still be using it, and dropping a label is not
    something Postgres supports anyway.
    """
    applied: list[str] = []
    seen: set[str] = set()

    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            enum_type = column.type
            if not isinstance(enum_type, sa.Enum) or not enum_type.name or enum_type.name in seen:
                continue
            seen.add(enum_type.name)

            result = await conn.execute(
                text(
                    "SELECT e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = :name"
                ),
                {"name": enum_type.name},
            )
            present = {row[0] for row in result.fetchall()}
            if not present:
                # The type does not exist yet; create_all owns that case.
                continue

            for label in enum_type.enums:
                if label in present:
                    continue
                statement = f"ALTER TYPE {enum_type.name} ADD VALUE IF NOT EXISTS '{label}'"
                try:
                    await conn.execute(text(statement))
                except Exception:  # noqa: BLE001 - one bad label must not block boot
                    logger.exception("could not add label %s to enum %s", label, enum_type.name)
                    continue
                applied.append(statement)
                logger.info("schema sync: added enum label %s.%s", enum_type.name, label)

    return applied


async def sync_additive_columns(conn) -> list[str]:
    """Add every model-declared column the database is missing.

    Returns the DDL it applied, so a caller (and the startup log) can see
    exactly what changed rather than trusting that nothing did.
    """
    result = await conn.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema()"
        )
    )
    existing = _existing_columns(result.fetchall())
    dialect = conn.sync_engine.dialect
    applied: list[str] = []

    for table in Base.metadata.sorted_tables:
        present = existing.get(table.name)
        if present is None:
            # The table itself is missing; create_all owns that case and has
            # already run. Nothing to reconcile.
            continue

        for column in table.columns:
            if column.name in present:
                continue

            spec = CreateColumn(column).compile(dialect=dialect).string

            # A NOT NULL column with no server default cannot be added to a
            # table that already has rows -- Postgres has nothing to put in
            # them. Adding it nullable keeps the deployment up and leaves a
            # loud, specific record of what still needs a real migration,
            # which beats refusing to boot over a column nobody is reading
            # yet.
            if not column.nullable and column.server_default is None:
                spec = spec.replace(" NOT NULL", "")
                logger.warning(
                    "column %s.%s is NOT NULL with no server default; added as NULLABLE. "
                    "Backfill it and add the constraint by hand.",
                    table.name,
                    column.name,
                )

            statement = f'ALTER TABLE "{table.name}" ADD COLUMN IF NOT EXISTS {spec}'
            try:
                await conn.execute(text(statement))
            except Exception:  # noqa: BLE001 - one bad column must not block boot
                logger.exception("could not add %s.%s", table.name, column.name)
                continue
            applied.append(statement)
            logger.info("schema sync: added %s.%s", table.name, column.name)

    return applied
