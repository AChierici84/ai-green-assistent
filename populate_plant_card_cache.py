import argparse
import json
import os
from typing import Any

from fastapi import HTTPException

import api


def iter_species_from_rag(batch_size: int = 500) -> list[str]:
    collection = api.get_rag_collection()
    species_set: set[str] = set()
    offset = 0

    while True:
        result = collection.get(
            limit=batch_size,
            offset=offset,
            include=["metadatas"],
        )
        ids = result.get("ids") or []
        metadatas = result.get("metadatas") or []
        if not ids:
            break

        for meta in metadatas:
            if not isinstance(meta, dict):
                continue
            species_name = str(meta.get("species_name") or "").strip()
            if species_name:
                species_set.add(species_name)

        offset += len(ids)
        if len(ids) < batch_size:
            break

    return sorted(species_set, key=lambda item: item.lower())


def run(lang: str, refresh: bool, limit: int | None, batch_size: int, no_openai: bool) -> int:
    if no_openai:
        os.environ["OPENAI_API_KEY"] = ""

    species = iter_species_from_rag(batch_size=batch_size)
    if limit is not None and limit > 0:
        species = species[:limit]

    total = len(species)
    print(f"Specie trovate in RAG: {total}")

    ok = 0
    skipped = 0
    failed = 0

    for idx, species_name in enumerate(species, start=1):
        try:
            if not refresh and api.get_cached_plant_card(species_name, lang) is not None:
                skipped += 1
                if idx % 25 == 0 or idx == total:
                    print(f"[{idx}/{total}] skipped={skipped} ok={ok} failed={failed}")
                continue

            response = api.plant_info(
                name=species_name,
                lang=lang,
                refresh_cache=refresh,
                authorization=None,
            )
            payload = json.loads(response.body.decode("utf-8"))
            source = str(payload.get("source") or "")
            if source == "rag":
                ok += 1
            else:
                failed += 1
                print(f"WARN [{idx}/{total}] {species_name}: source inatteso '{source}'")
        except HTTPException as exc:
            failed += 1
            print(f"ERR  [{idx}/{total}] {species_name}: HTTP {exc.status_code} - {exc.detail}")
        except Exception as exc:
            failed += 1
            print(f"ERR  [{idx}/{total}] {species_name}: {type(exc).__name__}: {exc}")

        if idx % 25 == 0 or idx == total:
            print(f"[{idx}/{total}] skipped={skipped} ok={ok} failed={failed}")

    print("\nCompletato.")
    print(f"Totale: {total}")
    print(f"Cache aggiornate: {ok}")
    print(f"Gia presenti (skip): {skipped}")
    print(f"Errori: {failed}")

    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Popola offline la tabella plant_cards_cache per tutte le specie presenti in RAG."
    )
    parser.add_argument("--lang", default="it", help="Lingua scheda (default: it)")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Rigenera anche le schede gia in cache",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limita il numero di specie processate (test)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Batch size lettura metadata RAG",
    )
    parser.add_argument(
        "--no-openai",
        action="store_true",
        help="Non usa OpenAI: genera summary locale dal testo RAG (piu veloce)",
    )
    args = parser.parse_args()

    lang = (args.lang or "it").strip().lower()
    return run(
        lang=lang,
        refresh=bool(args.refresh),
        limit=args.limit,
        batch_size=max(50, args.batch_size),
        no_openai=bool(args.no_openai),
    )


if __name__ == "__main__":
    raise SystemExit(main())
