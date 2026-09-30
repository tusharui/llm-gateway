"""Compare two PostgreSQL schemas column-by-column.

Used to prove a baseline migration matches production before stamping it.
Read-only: it only ever reads ``information_schema`` and ``pg_indexes``.

Usage:
    python -m scripts.diff_schema --a postgresql://...local --b postgresql://...prod
"""

import argparse
import asyncio
import ssl
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.database import build_db_url  # noqa: E402

TABLES_SQL = """
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = 'public'
      AND table_type = 'BASE TABLE'
      AND table_name <> 'alembic_version'
    ORDER BY table_name
"""

COLUMNS_SQL = """
    SELECT table_name, column_name, data_type, character_maximum_length,
           is_nullable, column_default
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name <> 'alembic_version'
    ORDER BY table_name, ordinal_position
"""

INDEXES_SQL = """
    SELECT indexname, indexdef
    FROM pg_indexes
    WHERE schemaname = 'public'
      AND tablename <> 'alembic_version'
    ORDER BY indexname
"""

# Constraints are compared separately from indexes on purpose. PostgreSQL
# implements a UNIQUE constraint as an index, so information_schema and
# pg_indexes look identical whether the original DDL said CONSTRAINT or
# CREATE UNIQUE INDEX -- but the two are different catalog objects, and
# autogenerate treats them differently. A schema check that only looked at
# indexes would call a UNIQUE constraint and a unique index equivalent.
#
# contype 'n' is excluded: PostgreSQL 17 made NOT NULL a catalog constraint,
# so on a PG17+ server every NOT NULL column gets a pg_constraint row and on
# PG16 none do. That is a version artifact, not schema drift -- NOT NULL is
# already compared through information_schema.columns.is_nullable, which means
# the same thing on both.
CONSTRAINTS_SQL = """
    SELECT conrelid::regclass::text AS table_name,
           conname,
           contype,
           pg_get_constraintdef(oid) AS definition
    FROM pg_constraint
    WHERE connamespace = 'public'::regnamespace
      AND contype <> 'n'
      AND conrelid::regclass::text <> 'alembic_version'
    ORDER BY conname
"""

VERSION_SQL = "SHOW server_version"


def normalize_type(row: dict) -> str:
    data_type = row["data_type"]
    length = row["character_maximum_length"]
    return f"{data_type}({length})" if length else data_type


async def snapshot(url: str, ssl_mode: str = "require") -> dict:
    connect_args = {}
    if ssl_mode == "require":
        connect_args["connect_args"] = {"ssl": ssl.create_default_context()}
    elif ssl_mode != "disable":
        raise ValueError(f"ssl mode must be 'require' or 'disable', got {ssl_mode!r}")

    engine = create_async_engine(build_db_url(url), poolclass=None, **connect_args)
    async with engine.connect() as conn:
        columns = [dict(r) for r in (await conn.execute(text(COLUMNS_SQL))).mappings()]
        indexes = [dict(r) for r in (await conn.execute(text(INDEXES_SQL))).mappings()]
        constraints = [
            dict(r) for r in (await conn.execute(text(CONSTRAINTS_SQL))).mappings()
        ]
        tables = [r[0] for r in (await conn.execute(text(TABLES_SQL))).all()]
        version = await conn.scalar(text(VERSION_SQL))
    await engine.dispose()

    cols = {
        (c["table_name"], c["column_name"]): (
            normalize_type(c),
            c["is_nullable"],
            c["column_default"],
        )
        for c in columns
    }
    idx = {i["indexname"]: i["indexdef"] for i in indexes}
    con = {c["conname"]: (c["contype"], c["definition"]) for c in constraints}
    return {
        "tables": sorted(tables),
        "columns": cols,
        "indexes": idx,
        "constraints": con,
        "version": version,
    }


def diff(a: dict, b: dict, label_a: str, label_b: str) -> int:
    problems = 0

    only_a = set(a["tables"]) - set(b["tables"])
    only_b = set(b["tables"]) - set(a["tables"])
    for t in sorted(only_a):
        print(f"  TABLE only in {label_a}: {t}")
        problems += 1
    for t in sorted(only_b):
        print(f"  TABLE only in {label_b}: {t}")
        problems += 1

    for key in sorted(set(a["columns"]) | set(b["columns"])):
        in_a, in_b = key in a["columns"], key in b["columns"]
        if in_a and not in_b:
            print(f"  COLUMN only in {label_a}: {key[0]}.{key[1]}")
            problems += 1
        elif in_b and not in_a:
            print(f"  COLUMN only in {label_b}: {key[0]}.{key[1]}")
            problems += 1
        elif a["columns"][key] != b["columns"][key]:
            print(f"  COLUMN differs: {key[0]}.{key[1]}")
            print(f"      {label_a}: {a['columns'][key]}")
            print(f"      {label_b}: {b['columns'][key]}")
            problems += 1

    for name in sorted(set(a["indexes"]) | set(b["indexes"])):
        in_a, in_b = name in a["indexes"], name in b["indexes"]
        if in_a and not in_b:
            print(f"  INDEX only in {label_a}: {name}")
            problems += 1
        elif in_b and not in_a:
            print(f"  INDEX only in {label_b}: {name}")
            problems += 1
        elif a["indexes"][name] != b["indexes"][name]:
            print(f"  INDEX differs: {name}")
            print(f"      {label_a}: {a['indexes'][name]}")
            print(f"      {label_b}: {b['indexes'][name]}")
            problems += 1

    for name in sorted(set(a["constraints"]) | set(b["constraints"])):
        in_a, in_b = name in a["constraints"], name in b["constraints"]
        if in_a and not in_b:
            print(f"  CONSTRAINT only in {label_a}: {name} {a['constraints'][name]}")
            problems += 1
        elif in_b and not in_a:
            print(f"  CONSTRAINT only in {label_b}: {name} {b['constraints'][name]}")
            problems += 1
        elif a["constraints"][name] != b["constraints"][name]:
            print(f"  CONSTRAINT differs: {name}")
            print(f"      {label_a}: {a['constraints'][name]}")
            print(f"      {label_b}: {b['constraints'][name]}")
            problems += 1

    return problems


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", required=True, help="URL for schema A (usually local)")
    parser.add_argument("--b", required=True, help="URL for schema B (usually prod)")
    parser.add_argument("--label-a", default="A")
    parser.add_argument("--label-b", default="B")
    # Explicit per target, because the usual comparison is a local container
    # with a self-signed certificate against production with a valid one, and
    # a single global setting cannot express both. Defaults to requiring TLS
    # so a typo fails loudly instead of quietly sending the production
    # password in cleartext.
    parser.add_argument("--a-ssl", choices=["require", "disable"], default="require")
    parser.add_argument("--b-ssl", choices=["require", "disable"], default="require")
    args = parser.parse_args()

    snap_a = await snapshot(args.a, args.a_ssl)
    snap_b = await snapshot(args.b, args.b_ssl)

    problems = diff(snap_a, snap_b, args.label_a, args.label_b)

    print(
        f"\n{args.label_a}: pg{snap_a['version']} | "
        f"{len(snap_a['tables'])} tables, "
        f"{len(snap_a['columns'])} columns, {len(snap_a['indexes'])} indexes, "
        f"{len(snap_a['constraints'])} constraints"
    )
    print(
        f"{args.label_b}: pg{snap_b['version']} | "
        f"{len(snap_b['tables'])} tables, "
        f"{len(snap_b['columns'])} columns, {len(snap_b['indexes'])} indexes, "
        f"{len(snap_b['constraints'])} constraints"
    )

    if problems:
        print(f"\nFAIL: {problems} schema difference(s)")
        return 1

    print("\nOK: schemas are identical")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))