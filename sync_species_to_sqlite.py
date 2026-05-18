#!/usr/bin/env python3
"""Sync one species row into plants.db using current RAG availability."""

import argparse

from build_plants_sqlite import (
    SQLITE_DB_PATH,
    get_rag_collection,
    get_rag_context,
    init_db,
    sqlite3,
    upsert_plant,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync one species into plants.db")
    parser.add_argument("--species", required=True, help="Species name")
    args = parser.parse_args()

    species = args.species.strip()
    if not species:
        raise ValueError("--species cannot be empty")

    collection = get_rag_collection()
    context = get_rag_context(collection, species)
    indexed = bool((context or "").strip())

    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        init_db(conn)
        upsert_plant(conn, species_name=species, indexed=indexed, profile=None)

    print({"species": species, "indexed": indexed, "db": str(SQLITE_DB_PATH)})


if __name__ == "__main__":
    main()
