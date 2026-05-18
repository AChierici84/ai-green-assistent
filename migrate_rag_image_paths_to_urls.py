#!/usr/bin/env python3
"""Replace local image paths in plant RAG metadata with Wikipedia image URLs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import chromadb

from build_plant_rag import (
    COLLECTION_NAME,
    DEFAULT_ALIAS_CSV_PATH,
    DEFAULT_WIKI_LANGS,
    MAX_IMAGES,
    RAG_DIR,
    fetch_wiki_extract,
    fetch_wiki_image_urls,
    load_alias_map,
    parse_langs,
)


def resolve_title_for_species(
    species_name: str,
    lang: str,
    alias_info: dict[str, str] | None,
) -> str | None:
    if alias_info:
        alias_lang = (alias_info.get("lang") or "").strip().lower()
        alias_title = (alias_info.get("title") or "").strip()
        if alias_lang == lang and alias_title:
            resolved = fetch_wiki_extract(alias_title, lang)
            if resolved:
                return resolved[0]

    resolved = fetch_wiki_extract(species_name, lang)
    if resolved:
        return resolved[0]
    return None


def load_species_records(collection: chromadb.Collection) -> dict[str, dict[str, object]]:
    payload = collection.get(include=["metadatas", "documents"])
    ids = payload.get("ids", []) or []
    documents = payload.get("documents", []) or []
    metadatas = payload.get("metadatas", []) or []

    species_records: dict[str, dict[str, object]] = {}
    for doc_id, document, metadata in zip(ids, documents, metadatas):
        meta = metadata or {}
        species_name = str(meta.get("species_name") or "").strip()
        if not species_name:
            continue

        record = species_records.setdefault(
            species_name,
            {"ids": [], "documents": [], "metadatas": [], "lang": ""},
        )
        record["ids"].append(doc_id)
        record["documents"].append(document)
        record["metadatas"].append(meta)
        if not record["lang"]:
            record["lang"] = str(meta.get("lang") or "").strip().lower()

    return species_records


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate Chroma image_paths metadata from local files to Wikipedia URLs.",
    )
    parser.add_argument("--rag-db-path", default=str(RAG_DIR), help="Path to the Chroma persistent directory.")
    parser.add_argument(
        "--alias-csv",
        default=str(DEFAULT_ALIAS_CSV_PATH),
        help="CSV mapping specie->Wikipedia URL (columns: species_name;wikipedia_url).",
    )
    parser.add_argument(
        "--langs",
        default=",".join(DEFAULT_WIKI_LANGS),
        help="Fallback languages to try when a species has no stored lang.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Only process the first N species (0 means all).")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing to Chroma.")
    args = parser.parse_args()

    client = chromadb.PersistentClient(path=args.rag_db_path)
    collection = client.get_collection(name=COLLECTION_NAME)
    alias_map = load_alias_map(Path(args.alias_csv))
    fallback_langs = parse_langs(args.langs)
    species_records = load_species_records(collection)

    species_names = sorted(species_records)
    if args.limit > 0:
        species_names = species_names[: args.limit]

    updated = 0
    skipped = 0
    failed = 0

    for species_name in species_names:
        record = species_records[species_name]
        stored_lang = str(record.get("lang") or "").strip().lower()
        candidate_langs = [stored_lang] if stored_lang else list(fallback_langs)
        alias_info = alias_map.get(species_name)

        resolved_lang = ""
        resolved_title = None
        for lang in candidate_langs:
            resolved_title = resolve_title_for_species(species_name, lang, alias_info)
            if resolved_title:
                resolved_lang = lang
                break

        if not resolved_title:
            print(f"[skip] {species_name}: wikipedia page not resolved")
            skipped += 1
            continue

        image_urls = fetch_wiki_image_urls(resolved_title, resolved_lang)[:MAX_IMAGES]
        if not image_urls:
            print(f"[skip] {species_name}: no image URLs found")
            skipped += 1
            continue

        encoded_urls = json.dumps(image_urls)
        new_metadatas = []
        changed = False
        for metadata in record["metadatas"]:
            new_metadata = dict(metadata)
            if new_metadata.get("image_paths") != encoded_urls:
                new_metadata["image_paths"] = encoded_urls
                changed = True
            new_metadatas.append(new_metadata)

        if not changed:
            print(f"[ok]   {species_name}: already up to date")
            skipped += 1
            continue

        print(
            f"[{'dry' if args.dry_run else 'ok '}] {species_name}: "
            f"{len(image_urls)} urls | lang={resolved_lang} | title={resolved_title}"
        )

        if args.dry_run:
            updated += 1
            continue

        try:
            collection.upsert(
                ids=record["ids"],
                documents=record["documents"],
                metadatas=new_metadatas,
            )
            updated += 1
        except Exception as exc:
            print(f"[err]  {species_name}: {exc}")
            failed += 1

    print("\n=== Migration completed ===")
    print(f"Updated : {updated}")
    print(f"Skipped : {skipped}")
    print(f"Errors  : {failed}")


if __name__ == "__main__":
    main()