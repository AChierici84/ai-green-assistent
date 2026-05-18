#!/usr/bin/env python3
"""
Populate leafsnap_aliases table in plants.db and update missing_species.csv.

For each LeafSnap species not in the DB:
  - If a DB species with the same genus has an epithet edit-distance <= EDIT_THRESHOLD
    (or the full name edit-distance <= EDIT_THRESHOLD), it is saved as an alias.
  - Otherwise it is appended to missing_species.csv for manual review.

Run:
    python sync_leafsnap_aliases.py [--db data/plants.db] [--cache data/leafsnap_cache.pt]
                                    [--missing missing_species.csv] [--threshold 3] [--dry-run]
"""

import argparse
import csv
import os
import sqlite3
from collections import defaultdict
from pathlib import Path


def _edit_distance(a: str, b: str) -> int:
    """Simple Levenshtein distance."""
    a, b = a.lower(), b.lower()
    if a == b:
        return 0
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[:]
        dp[0] = i
        for j in range(1, n + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev[j - 1] + cost)
    return dp[n]


def find_best_alias(
    leafsnap_label: str,
    db_species: set[str],
    db_by_genus: dict[str, list[str]],
    threshold: int,
) -> str | None:
    """Return the best-matching DB species within *threshold* edit distance, or None."""
    genus = leafsnap_label.split()[0]
    epithet = " ".join(leafsnap_label.split()[1:])

    candidates = db_by_genus.get(genus, [])
    if not candidates:
        return None

    best_label: str | None = None
    best_dist = threshold + 1

    for db_sp in candidates:
        db_epithet = " ".join(db_sp.split()[1:])
        # Compare epithet only (genus already matches)
        d = _edit_distance(epithet, db_epithet)
        if d < best_dist:
            best_dist = d
            best_label = db_sp

    return best_label if best_dist <= threshold else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync LeafSnap aliases into plants.db")
    parser.add_argument("--db", default=os.getenv("PLANTS_SQLITE_PATH", "data/plants.db"))
    parser.add_argument("--cache", default=os.getenv("LEAFSNAP_CACHE_PATH", "data/leafsnap_cache.pt"))
    parser.add_argument("--missing", default="missing_species.csv")
    parser.add_argument("--threshold", type=int, default=2,
                        help="Max Levenshtein distance on epithet to consider an alias (default: 2)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print results without writing to DB or CSV")
    args = parser.parse_args()

    import torch

    print(f"Loading LeafSnap cache from {args.cache} ...")
    ls_data = torch.load(args.cache, map_location="cpu", weights_only=False)
    leafsnap_species = set(ls_data["labels"])

    print(f"Loading DB species from {args.db} ...")
    conn = sqlite3.connect(args.db)

    # Ensure table exists
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS leafsnap_aliases (
            leafsnap_label TEXT PRIMARY KEY,
            db_species_name TEXT NOT NULL
        )
        """
    )
    conn.commit()

    db_species = set(row[0] for row in conn.execute("SELECT species_name FROM plants"))
    existing_aliases = dict(conn.execute("SELECT leafsnap_label, db_species_name FROM leafsnap_aliases"))

    db_by_genus: dict[str, list[str]] = defaultdict(list)
    for s in db_species:
        db_by_genus[s.split()[0]].append(s)

    # Classify each LeafSnap species
    to_alias: list[tuple[str, str]] = []   # (leafsnap_label, db_species_name)
    to_missing: list[str] = []

    # Hardcoded typo overrides (edit distance > threshold but unambiguously same species)
    TYPO_OVERRIDES: dict[str, str] = {
        "Aesculus hippocastamon": "Aesculus hippocastanum",
    }

    for sp in sorted(leafsnap_species):
        if sp in db_species:
            continue  # already in DB, no alias needed
        if sp in existing_aliases:
            print(f"  [skip] {sp} -> {existing_aliases[sp]}  (already in aliases)")
            continue

        if sp in TYPO_OVERRIDES:
            target = TYPO_OVERRIDES[sp]
            if target in db_species:
                to_alias.append((sp, target))
                continue

        match = find_best_alias(sp, db_species, db_by_genus, args.threshold)
        if match:
            to_alias.append((sp, match))
        else:
            to_missing.append(sp)

    print(f"\nNew aliases found:   {len(to_alias)}")
    for ls_lbl, db_lbl in to_alias:
        print(f"  {ls_lbl}  ->  {db_lbl}")

    print(f"\nUnresolvable (-> missing_species.csv): {len(to_missing)}")
    for sp in to_missing:
        print(f"  {sp}")

    if args.dry_run:
        print("\n[dry-run] No changes written.")
        conn.close()
        return

    # Write aliases to DB
    if to_alias:
        conn.executemany(
            "INSERT OR REPLACE INTO leafsnap_aliases (leafsnap_label, db_species_name) VALUES (?, ?)",
            to_alias,
        )
        conn.commit()
        print(f"\nSaved {len(to_alias)} aliases to leafsnap_aliases table.")

    conn.close()

    # Append to missing_species.csv (avoid duplicates)
    missing_path = Path(args.missing)
    existing_missing: set[str] = set()
    if missing_path.exists():
        with open(missing_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing_missing.add(row["species_name"])

    new_missing = [sp for sp in to_missing if sp not in existing_missing]
    if new_missing:
        write_header = not missing_path.exists() or missing_path.stat().st_size == 0
        with open(missing_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["species_name"])
            if write_header:
                writer.writeheader()
            for sp in new_missing:
                writer.writerow({"species_name": sp})
        print(f"Appended {len(new_missing)} species to {missing_path}.")
    else:
        print("No new missing species to append.")


if __name__ == "__main__":
    main()
