#!/usr/bin/env python3
"""
Clean and fetch images for plants in the RAG database.

This script:
1. Validates existing images (removes icons/symbols)
2. Fetches missing images from Wikipedia if --refetch is enabled

Usage:
    python refetch_plant_images.py --refetch
    python refetch_plant_images.py --refetch --species "Plant_name1,Plant_name2"
"""

import argparse
import json
import logging
from pathlib import Path
from typing import Optional
import time

import chromadb

from build_plant_rag import (
    COLLECTION_NAME,
    DEFAULT_ALIAS_CSV_PATH,
    DEFAULT_WIKI_LANGS,
    IMAGE_SKIP_EXTENSIONS,
    MAX_IMAGES,
    RAG_DIR,
    fetch_wiki_image_urls,
    load_alias_map,
    parse_langs,
    _action_api,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

# Keywords indicating non-photo images
ICON_KEYWORDS = {
    "commons-logo", "wikidata", "wikiquote", "disambig", 
    "blue_pencil", "padlock", "question_book", "portal",
    "ambox", "stub", "coat_arms", "flag", "seal", "emblem", "symbol",
}


def is_valid_image(url: str) -> bool:
    """Check if URL is likely a real photo, not an icon."""
    if not url or not isinstance(url, str):
        return False
    url_lower = url.lower()
    if any(url_lower.endswith(ext) for ext in IMAGE_SKIP_EXTENSIONS):
        return False
    if any(kw in url_lower for kw in ICON_KEYWORDS):
        return False
    return True


def get_wiki_title(species: str, lang: str) -> Optional[str]:
    """Resolve Wikipedia page title."""
    data = _action_api(lang, action="query", prop="extracts", titles=species, 
                       explaintext="1", redirects="1")
    if not data:
        return None
    for pid, page in data.get("query", {}).get("pages", {}).items():
        if pid != "-1":
            return page.get("title")
    return None


def update_images(
    collection: chromadb.Collection,
    species: str,
    lang: str,
    alias_info: Optional[dict],
    refetch: bool,
) -> dict:
    """Update images for a species."""
    # Get species documents
    results = collection.get(where={"species_name": {"$eq": species}}, 
                            include=["metadatas", "documents"])
    if not results or not results["ids"]:
        return {"species": species, "status": "not_found"}
    
    # Parse existing images
    existing = []
    for meta in results.get("metadatas", []) or []:
        if meta:
            imgs = json.loads(meta.get("image_paths", "[]") or "[]")
            existing.extend(imgs)
    existing = list(dict.fromkeys(existing))
    
    # Validate existing
    valid = [img for img in existing if is_valid_image(img)]
    
    # Fetch if needed
    if len(valid) < MAX_IMAGES and refetch:
        # Get Wikipedia title
        title = None
        if alias_info:
            t = alias_info.get("title", "").strip()
            l = alias_info.get("lang", "").strip().lower()
            if t and l == lang:
                title = t
        if not title:
            title = get_wiki_title(species, lang)
        
        if title:
            try:
                fetched = fetch_wiki_image_urls(title, lang)[:MAX_IMAGES]
                valid.extend(fetched)
                valid = list(dict.fromkeys(valid))[:MAX_IMAGES]
                if fetched:
                    logger.info(f"  {species}: fetched {len(fetched)} image(s)")
                time.sleep(0.5)
            except Exception as e:
                logger.error(f"  {species}: fetch error: {e}")
    
    # Save if changed
    if valid != existing:
        encoded = json.dumps(valid)
        for meta in results.get("metadatas", []) or []:
            if meta:
                meta["image_paths"] = encoded
        
        collection.upsert(
            ids=results["ids"],
            documents=results.get("documents"),
            metadatas=results.get("metadatas"),
        )
        
        change = "added" if not existing and valid else "updated"
        logger.info(f"  {species}: {change} ({len(valid)} images)")
        return {"species": species, "status": change, "count": len(valid)}
    
    logger.info(f"  {species}: ok ({len(valid)} images)")
    return {"species": species, "status": "ok", "count": len(valid)}


def main():
    parser = argparse.ArgumentParser(description="Refetch images for plants.")
    parser.add_argument("--rag-db-path", default=str(RAG_DIR))
    parser.add_argument("--alias-csv", default=str(DEFAULT_ALIAS_CSV_PATH))
    parser.add_argument("--langs", default=",".join(DEFAULT_WIKI_LANGS))
    parser.add_argument("--species", help="Comma-separated list of species")
    parser.add_argument("--refetch", action="store_true", help="Fetch missing images")
    parser.add_argument("--limit", type=int, default=0, help="Process first N species")
    args = parser.parse_args()
    
    if not args.refetch:
        logger.error("Use --refetch to actually fetch images")
        return
    
    client = chromadb.PersistentClient(path=args.rag_db_path)
    collection = client.get_collection(name=COLLECTION_NAME)
    alias_map = load_alias_map(Path(args.alias_csv))
    langs = parse_langs(args.langs)
    
    # Get all species
    results = collection.get(include=["metadatas"])
    species_list = []
    for meta in results.get("metadatas", []) or []:
        if meta:
            s = meta.get("species_name", "").strip()
            if s and s not in species_list:
                species_list.append(s)
    
    # Filter
    if args.species:
        allowed = {s.strip() for s in args.species.split(",")}
        species_list = [s for s in species_list if s in allowed]
    
    if args.limit > 0:
        species_list = species_list[:args.limit]
    
    logger.info(f"Processing {len(species_list)} species")
    updated = 0
    
    for sp in sorted(species_list):
        lang = langs[0] if langs else "it"
        alias = alias_map.get(sp)
        try:
            result = update_images(collection, sp, lang, alias, args.refetch)
            if result["status"] in ("added", "updated"):
                updated += 1
        except Exception as e:
            logger.error(f"  {sp}: {e}")
    
    logger.info(f"Done! {updated} species updated")


if __name__ == "__main__":
    main()
