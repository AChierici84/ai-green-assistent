#!/usr/bin/env python3
"""Append one species to PlantCLEF FAISS index and cache using Wikipedia images."""

import argparse
import io
import json
import os
import pickle
import re
import sqlite3
import urllib.parse
from pathlib import Path

import faiss
import numpy as np
import open_clip
import requests
import torch
from PIL import Image

DEFAULT_MODEL_NAME = "ViT-B-32"
DEFAULT_PRETRAINED = "laion2b_s34b_b79k"
DEFAULT_INDEX_PATH = Path("data/planclef.faiss")
DEFAULT_CACHE_PATH = Path("data/planclef_cache.pt")
HEADERS = {"User-Agent": "clorofilla/1.0 (contact: local-dev)"}
IMAGE_SKIP_KEYWORDS = (
    "commons-logo",
    "wikidata",
    "wikiquote",
    "disambig",
    "folder",
    "wiktionary",
    "wikimedia",
    "icon",
    "edit-clear",
    "blue_pencil",
    "padlock",
    "question_book",
    "portal",
    "wikiversity",
)
IMAGE_SKIP_EXTENSIONS = (".svg", ".ogg", ".ogv", ".webm")


def _load_cache(cache_path: Path) -> dict:
    try:
        data = torch.load(cache_path, map_location="cpu", weights_only=True)
    except TypeError:
        data = torch.load(cache_path, map_location="cpu")
    except Exception:
        try:
            data = torch.load(cache_path, map_location="cpu", weights_only=False)
        except Exception:
            with open(cache_path, "rb") as f:
                data = pickle.load(f)

    if not isinstance(data, dict):
        raise TypeError("PlantCLEF cache must be dict-like")
    if "labels" not in data:
        raise KeyError("Missing 'labels' in cache")
    return data


def _save_cache(cache_path: Path, data: dict) -> None:
    torch.save(data, cache_path)


def parse_wikipedia_url(url: str) -> tuple[str, str] | None:
    txt = (url or "").strip()
    m = re.match(r"^https?://([a-z\-]+)\.wikipedia\.org/wiki/(.+)$", txt, flags=re.IGNORECASE)
    if not m:
        return None
    lang = m.group(1).lower()
    title = urllib.parse.unquote(m.group(2)).replace("_", " ").strip()
    if not title:
        return None
    return lang, title


def _action_api(lang: str, **params) -> dict | None:
    url = f"https://{lang}.wikipedia.org/w/api.php"
    try:
        r = requests.get(url, params={"format": "json", **params}, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def fetch_wiki_extract(title: str, lang: str) -> tuple[str, str] | None:
    data = _action_api(
        lang,
        action="query",
        prop="extracts",
        titles=title,
        explaintext="1",
        redirects="1",
    )
    if not data:
        return None
    pages = data.get("query", {}).get("pages", {})
    for pid, page in pages.items():
        if pid == "-1":
            return None
        return page.get("title", title), page.get("extract", "")
    return None


def _looks_like_list_or_disambiguation(title: str, extract: str) -> bool:
    title_low = (title or "").strip().lower()
    extract_low = (extract or "").strip().lower()

    title_markers = (
        "list of",
        "lista di",
        "elenco",
        "disambiguation",
        "disambigua",
        "(genere)",
        " genus",
    )
    extract_markers = (
        "may refer to",
        "can refer to",
        "puo riferirsi a",
        "può riferirsi a",
        "questa pagina di disambiguazione",
        "elenco delle specie",
        "lista delle specie",
        "species in this genus",
    )

    if any(marker in title_low for marker in title_markers):
        return True
    if any(marker in extract_low[:800] for marker in extract_markers):
        return True
    return False


def _is_probably_species_page(species_name: str, resolved_title: str, extract: str) -> bool:
    if _looks_like_list_or_disambiguation(resolved_title, extract):
        return False

    species = re.sub(r"\s+", " ", (species_name or "").strip())
    species_low = species.lower()
    title_low = (resolved_title or "").strip().lower()
    extract_low = (extract or "").strip().lower()

    if not species_low:
        return False

    # Strong positive signal: exact species appears in title or lead text.
    if species_low in title_low:
        return True
    if species_low in extract_low[:1200]:
        return True

    # Accept when the genus is in title and lead says it's a species.
    genus = species_low.split()[0]
    if genus and genus in title_low and "species" in extract_low[:500]:
        return True

    return False


def fetch_wiki_image_urls(title: str, lang: str, max_images: int) -> list[str]:
    data = _action_api(
        lang,
        action="query",
        prop="images",
        titles=title,
        imlimit=200,
        redirects="1",
    )
    if not data:
        return []

    pages = data.get("query", {}).get("pages", {})
    image_titles: list[str] = []
    for page in pages.values():
        for img in page.get("images", []) or []:
            t = (img.get("title") or "").strip()
            if not t:
                continue
            low = t.lower()
            if any(x in low for x in IMAGE_SKIP_KEYWORDS):
                continue
            if low.endswith(IMAGE_SKIP_EXTENSIONS):
                continue
            image_titles.append(t)

    if not image_titles:
        return []

    urls: list[str] = []
    step = 50
    for i in range(0, len(image_titles), step):
        chunk = image_titles[i : i + step]
        info = _action_api(
            lang,
            action="query",
            prop="imageinfo",
            titles="|".join(chunk),
            iiprop="url",
        )
        if not info:
            continue

        for page in (info.get("query", {}).get("pages", {}) or {}).values():
            ii = page.get("imageinfo", []) or []
            if not ii:
                continue
            url = (ii[0].get("url") or "").strip()
            if not url:
                continue
            low = url.lower()
            if any(x in low for x in IMAGE_SKIP_KEYWORDS):
                continue
            if low.endswith(IMAGE_SKIP_EXTENSIONS):
                continue
            urls.append(url)
            if len(urls) >= max_images:
                return urls

    return urls


def _download_image(url: str) -> Image.Image | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content)).convert("RGB")
    except Exception:
        return None


def embed_images(urls: list[str], model_name: str) -> tuple[np.ndarray, list[str]]:
    model, preprocess, _ = open_clip.create_model_and_transforms(
        model_name=model_name,
        pretrained=DEFAULT_PRETRAINED,
    )
    model.eval()

    vectors: list[np.ndarray] = []
    used_urls: list[str] = []

    for url in urls:
        img = _download_image(url)
        if img is None:
            continue
        tensor = preprocess(img).unsqueeze(0)
        with torch.no_grad():
            emb = model.encode_image(tensor)
            emb = emb / emb.norm(dim=-1, keepdim=True)
        vectors.append(emb.cpu().numpy().astype("float32"))
        used_urls.append(url)

    if not vectors:
        raise RuntimeError("No usable images were embedded")

    return np.vstack(vectors), used_urls


def append_embeddings_to_cache(data: dict, new_embeddings: np.ndarray, species_label: str) -> None:
    labels = data.get("labels")
    if not isinstance(labels, list):
        raise TypeError("cache['labels'] must be a list")

    labels.extend([species_label] * len(new_embeddings))

    existing = data.get("embeddings")
    if existing is None:
        data["embeddings"] = new_embeddings
        return

    if isinstance(existing, np.ndarray):
        data["embeddings"] = np.vstack([existing, new_embeddings])
        return

    if torch.is_tensor(existing):
        new_t = torch.from_numpy(new_embeddings)
        data["embeddings"] = torch.cat([existing, new_t], dim=0)
        return


def resolve_title(species_name: str, wikipedia_url: str, langs: tuple[str, ...]) -> tuple[str, str]:
    parsed = parse_wikipedia_url(wikipedia_url) if wikipedia_url else None

    if parsed:
        lang, title = parsed
        resolved = fetch_wiki_extract(title, lang)
        if resolved and _is_probably_species_page(species_name, resolved[0], resolved[1]):
            return lang, resolved[0]

    # Try the full species name first
    for lang in langs:
        resolved = fetch_wiki_extract(species_name, lang)
        if resolved and _is_probably_species_page(species_name, resolved[0], resolved[1]):
            return lang, resolved[0]

    # Generate fallback variants: first word, remove 'x' (hybrid), etc.
    variants: list[str] = []
    
    # Variant 1: First word only (e.g. "Petunia x atkinsiana" -> "Petunia")
    first_word = species_name.split()[0] if species_name else ""
    if first_word and first_word != species_name:
        variants.append(first_word)
    
    # Variant 2: Remove " x " (hybrid marker) — e.g. "Petunia x atkinsiana" -> "Petunia atkinsiana"
    if " x " in species_name.lower():
        variant = re.sub(r"\s+x\s+", " ", species_name, flags=re.IGNORECASE)
        if variant and variant != species_name and variant not in variants:
            variants.append(variant)
    
    # Variant 3: Genus only if species binomial (e.g. "Rosa canina" -> "Rosa")
    parts = species_name.split()
    if len(parts) >= 2 and parts[0] not in variants:
        variants.append(parts[0])
    
    # Try each variant across all languages
    for variant in variants:
        for lang in langs:
            try:
                resolved = fetch_wiki_extract(variant, lang)
                if resolved and _is_probably_species_page(species_name, resolved[0], resolved[1]):
                    print(f"  [fallback] Using variant '{variant}' from {lang} Wikipedia for '{species_name}'")
                    return lang, resolved[0]
            except Exception:
                continue

    raise RuntimeError(f"Wikipedia page not found for '{species_name}' (tried: {species_name}, {', '.join(variants)})")

def add_to_faiss(
    species_name: str,
    image_urls: list[str],
    *,
    lang: str,
    resolved_title: str,
    model_name: str = DEFAULT_MODEL_NAME,
    index_path: Path = DEFAULT_INDEX_PATH,
    cache_path: Path = DEFAULT_CACHE_PATH,
) -> dict:
    # --- DB setup: support MySQL if enabled ---
    import chromadb
    from db_config import load_mysql_config, is_mysql_enabled
    from data_storage import DataStorage
    from build_plants_sqlite import SQLITE_DB_PATH, init_db
    import sqlite3

    mysql_config = load_mysql_config()
    use_mysql = is_mysql_enabled(mysql_config)
    data_storage = None
    if use_mysql:
        # Dummy format_datetime_display for CLI
        data_storage = DataStorage(mysql_config, plant_card_cache_enabled=False, format_datetime_display=lambda x: x)

    # Check what's already in the system
    cache = _load_cache(cache_path)
    cache_labels = cache.get("labels", [])
    species_in_faiss = species_name in cache_labels

    # Load imports for RAG/DB checks
    from build_plant_rag import COLLECTION_NAME, RAG_DIR

    rag_client = chromadb.PersistentClient(path=str(RAG_DIR))
    try:
        rag_collection = rag_client.get_collection(name=COLLECTION_NAME)
        rag_results = rag_collection.get(
            where={"species_name": {"$eq": species_name}},
            limit=1,
        )
        species_in_rag = bool(rag_results.get("ids"))
    except Exception:
        species_in_rag = False

    # --- Check if species already in DB (MySQL or SQLite) ---
    if use_mysql:
        profile_row = data_storage.get_plant_profile_from_db(species_name)
        species_in_db = bool(profile_row and profile_row.get("indexed"))
    else:
        with sqlite3.connect(SQLITE_DB_PATH) as conn:
            init_db(conn)
            db_row = conn.execute(
                "SELECT indexed FROM plants WHERE species_name = ?",
                (species_name,),
            ).fetchone()
            species_in_db = bool(db_row and db_row[0])
    
    # Step 1: FAISS + cache (skip if already present)
    before = None
    after = None
    used_urls: list[str] = []
    
    if species_in_faiss:
        print(f"[skip] {species_name}: already in FAISS index")
    else:
        if image_urls:
            embeddings, used_urls = embed_images(image_urls, model_name=model_name)
            print(f"Embedded {len(used_urls)}/{len(image_urls)} images for {species_name}")

            index = faiss.read_index(str(index_path))
            if embeddings.shape[1] != index.d:
                raise RuntimeError(f"Embedding dim mismatch: got {embeddings.shape[1]}, index expects {index.d}")

            before = index.ntotal
            index.add(embeddings)
            after = index.ntotal
            faiss.write_index(index, str(index_path))
            print(f"Added {embeddings.shape[0]} embeddings to FAISS index. Total before: {before}, after: {after}")

            append_embeddings_to_cache(cache, embeddings, species_name)
            _save_cache(cache_path, cache)
        else:
            print(f"[skip] {species_name}: no image URLs available, skipping FAISS append")

    # Step 2: Add textual chunks to RAG (skip if already present)
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

    from build_plant_rag import (
        COLLECTION_NAME,
        EMBEDDING_MODEL,
        RAG_DIR,
        SKIP_SECTIONS,
        chunk_by_words,
        extract_common_name,
        fetch_wiki_extract as fetch_wiki_extract_sections,
        split_wiki_sections,
    )
    from build_plants_sqlite import (
        DEFAULT_MODEL,
        SQLITE_DB_PATH,
        extract_plant_profile,
        extract_plant_profile_generic,
        fetch_external_sources,
        get_rag_collection,
        get_rag_context,
        init_db,
        merge_missing_fields,
        normalize_profile_with_evidence,
        profile_has_missing_fields,
        upsert_plant,
    )
    from openai import OpenAI
    import chromadb

    rag_chunks = 0
    if species_in_rag:
        print(f"[skip] {species_name}: already in RAG")
    else:
        wiki_payload = fetch_wiki_extract_sections(resolved_title, lang)
        if not wiki_payload:
            wiki_payload = fetch_wiki_extract_sections(species_name, "it")
        if not wiki_payload:
            wiki_payload = fetch_wiki_extract_sections(species_name, "en")

        if wiki_payload:
            rag_title, wiki_extract = wiki_payload
            sections = split_wiki_sections(wiki_extract)
            text_parts: list[str] = []
            for sec_title, sec_body in sections:
                if sec_title.lower() in SKIP_SECTIONS:
                    continue
                if not sec_body:
                    continue
                text_parts.append(f"{sec_title}\n{sec_body}" if sec_title else sec_body)

            full_text = "\n\n".join(text_parts)
            chunks = chunk_by_words(full_text)
            rag_chunks = len(chunks)

            if chunks:
                slug = re.sub(r"[^\w]", "_", species_name).lower()
                ids = [f"{slug}__{i}" for i in range(len(chunks))]
                lead_text = sections[0][1] if sections else ""
                common_name = extract_common_name(lead_text, species_name)
                encoded_images = json.dumps(used_urls or image_urls or [], ensure_ascii=False)
                metadatas = [
                    {
                        "species_name": species_name,
                        "common_name": common_name,
                        "image_paths": encoded_images,
                        "chunk_index": i,
                        "lang": lang,
                        "source_lang": lang,
                        "translated_it": False,
                        "content_lang": lang,
                    }
                    for i in range(len(chunks))
                ]

                ef = SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)
                rag_client = chromadb.PersistentClient(path=str(RAG_DIR))
                rag_collection = rag_client.get_or_create_collection(
                    name=COLLECTION_NAME,
                    embedding_function=ef,
                    metadata={"hnsw:space": "cosine"},
                )
                rag_collection.upsert(ids=ids, documents=chunks, metadatas=metadatas)
                print(f"Added {len(chunks)} chunks to RAG for {species_name} ({lang}:{rag_title})")

    # Step 3: Sync DB (MySQL o SQLite): indexed=1 e arricchimento profilo
    # --- Estrazione immagini Wikipedia ---
    # image_urls è già calcolato nella fase precedente (usato per FAISS)
    # used_urls contiene quelle effettivamente embeddate
    image_paths_json = json.dumps(used_urls or image_urls or [], ensure_ascii=False)
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    from openai import OpenAI
    openai_client = OpenAI(api_key=api_key) if api_key else None

    profile = None
    backend_label = "MySQL" if use_mysql else "plants.db"
    # Lettura profilo esistente
    if species_in_db:
        print(f"[update] {species_name}: already in {backend_label}, filling missing fields only")
        if use_mysql:
            existing_profile = data_storage.get_plant_profile_from_db(species_name) or {}
        else:
            with sqlite3.connect(SQLITE_DB_PATH) as conn:
                row = conn.execute(
                    """SELECT annaffiatura_gg, annaffiatura_time, luce, temperatura, umidita, \
                              altezza_media, pulizia, terriccio, concimazione, prevenzione \
                       FROM plants WHERE species_name = ?""",
                    (species_name,),
                ).fetchone()
                keys = ("annaffiatura_gg", "annaffiatura_time", "luce", "temperatura", "umidita",
                        "altezza_media", "pulizia", "terriccio", "concimazione", "prevenzione")
                existing_profile = {k: v for k, v in zip(keys, row)} if row else {}
        # Solo se OpenAI disponibile e ci sono campi mancanti
        if openai_client and any(v is None for v in existing_profile.values()):
            rag_collection_for_context = get_rag_collection()
            rag_context = get_rag_context(rag_collection_for_context, species_name)
            external_sources = fetch_external_sources(species_name)
            fallback_profile = extract_plant_profile_generic(
                openai_client,
                DEFAULT_MODEL,
                species_name,
                partial_profile=existing_profile,
            )
            if any(external_sources.values()):
                fallback_profile = normalize_profile_with_evidence(
                    client=openai_client,
                    model=DEFAULT_MODEL,
                    species_name=species_name,
                    rag_context=rag_context,
                    partial_profile=fallback_profile,
                    external_sources=external_sources,
                )
            profile = merge_missing_fields(existing_profile, fallback_profile)
        else:
            profile = existing_profile
    else:
        print(f"[new] {species_name}: adding to {backend_label} with enrichment")
        rag_collection_for_context = get_rag_collection()
        rag_context = get_rag_context(rag_collection_for_context, species_name)
        external_sources = fetch_external_sources(species_name)

        if openai_client is not None:
            if rag_context.strip():
                profile = extract_plant_profile(openai_client, DEFAULT_MODEL, species_name, rag_context)
                if any(external_sources.values()):
                    profile = normalize_profile_with_evidence(
                        client=openai_client,
                        model=DEFAULT_MODEL,
                        species_name=species_name,
                        rag_context=rag_context,
                        partial_profile=profile,
                        external_sources=external_sources,
                    )
            else:
                profile = extract_plant_profile_generic(
                    openai_client,
                    DEFAULT_MODEL,
                    species_name,
                    partial_profile=None,
                )
                if any(external_sources.values()):
                    profile = normalize_profile_with_evidence(
                        client=openai_client,
                        model=DEFAULT_MODEL,
                        species_name=species_name,
                        rag_context="",
                        partial_profile=profile,
                        external_sources=external_sources,
                    )

            if profile_has_missing_fields(profile):
                fallback_profile = extract_plant_profile_generic(
                    openai_client,
                    DEFAULT_MODEL,
                    species_name,
                    partial_profile=profile,
                )
                profile = merge_missing_fields(profile, fallback_profile)

    # Scrittura su DB
    if use_mysql:
        # upsert su MySQL
        now_iso = __import__('datetime').datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        fields = [
            "species_name", "indexed", "image_paths", "annaffiatura_gg", "annaffiatura_time", "luce", "temperatura",
            "umidita", "altezza_media", "pulizia", "terriccio", "concimazione", "prevenzione", "updated_at"
        ]
        values = [
            species_name,
            1,
            image_paths_json,
            profile.get("annaffiatura_gg") if profile else None,
            profile.get("annaffiatura_time") if profile else None,
            profile.get("luce") if profile else None,
            profile.get("temperatura") if profile else None,
            profile.get("umidita") if profile else None,
            profile.get("altezza_media") if profile else None,
            profile.get("pulizia") if profile else None,
            profile.get("terriccio") if profile else None,
            profile.get("concimazione") if profile else None,
            profile.get("prevenzione") if profile else None,
            now_iso,
        ]
        query = (
            "INSERT INTO plants (" + ", ".join(fields) + ") "
            "VALUES (" + ", ".join(["%s"] * len(fields)) + ") "
            "ON DUPLICATE KEY UPDATE "
            + ", ".join([f"{f}=VALUES({f})" for f in fields[1:]])
        )
        with data_storage.get_plants_db_connection() as conn:
            conn.execute(query, tuple(values))
            conn.commit()
    else:
        with sqlite3.connect(SQLITE_DB_PATH) as conn:
            init_db(conn)
            from build_plants_sqlite import upsert_plant
            upsert_plant(conn, species_name=species_name, indexed=True, profile=profile, image_paths=image_paths_json)
            conn.commit()

    if before is None:
        before = "skipped"
    if after is None:
        after = "skipped"

    return {
        "species": species_name,
        "resolved_title": resolved_title,
        "lang": lang,
        "images_used": len(used_urls or image_urls or []),
        "index_ntotal_before": before,
        "index_ntotal_after": after,
        "rag_chunks": rag_chunks,
        "plants_db": "updated",
        "profile_enriched": bool(profile),
        "faiss_skipped": species_in_faiss,
        "rag_skipped": species_in_rag,
        "db_updated": not species_in_db,
    }



def main() -> None:
    parser = argparse.ArgumentParser(description="Add one species to PlantCLEF FAISS index/cache")
    parser.add_argument("--species", required=True, help="Species label to add (e.g. Guzmania lingulata)")
    parser.add_argument("--wikipedia-url", default="", help="Optional explicit Wikipedia URL")
    parser.add_argument("--langs", default="it,en", help="Fallback Wikipedia languages")
    parser.add_argument("--max-images", type=int, default=8, help="Max image URLs to fetch")
    parser.add_argument("--model", default=DEFAULT_MODEL_NAME, help="OpenCLIP model name")
    parser.add_argument("--index-path", default=str(DEFAULT_INDEX_PATH), help="Path to FAISS index")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH), help="Path to PlantCLEF cache")
    parser.add_argument("--force", action="store_true", help="Allow duplicate species labels")
    args = parser.parse_args()

    species = args.species.strip()
    if not species:
        raise ValueError("--species cannot be empty")

    index_path = Path(args.index_path)
    cache_path = Path(args.cache_path)
    if not index_path.exists():
        raise FileNotFoundError(f"Index not found: {index_path}")
    if not cache_path.exists():
        raise FileNotFoundError(f"Cache not found: {cache_path}")

    cache = _load_cache(cache_path)
    labels = cache.get("labels")
    if not isinstance(labels, list):
        raise TypeError("cache['labels'] must be list")

    if (species in labels) and (not args.force):
        raise RuntimeError(f"Species '{species}' already exists in cache labels. Use --force to add more samples")

    langs = tuple(x.strip().lower() for x in args.langs.split(",") if x.strip())
    lang, resolved_title = resolve_title(species, args.wikipedia_url, langs)
    print(f"Resolved Wikipedia page: {lang}:{resolved_title}")

    urls = fetch_wiki_image_urls(resolved_title, lang, max_images=max(1, args.max_images))
    if not urls:
        raise RuntimeError("No suitable Wikipedia images found")

    result = add_to_faiss(
        species,
        urls,
        lang=lang,
        resolved_title=resolved_title,
        model_name=args.model,
        index_path=index_path,
        cache_path=cache_path,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
