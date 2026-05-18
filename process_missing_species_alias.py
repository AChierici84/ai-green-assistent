#!/usr/bin/env python3
"""Process missing species from missing_species_alias.csv and add to RAG."""

import csv
import os
from pathlib import Path
from urllib.parse import urlparse, unquote
from dotenv import load_dotenv
import chromadb
from openai import OpenAI

from build_plant_rag import process_species

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RAG_DB_PATH = Path(os.getenv("RAG_DB_PATH", str(DATA_DIR / "plant_rag")))
SQLITE_DB_PATH = Path(os.getenv("PLANTS_SQLITE_PATH", str(DATA_DIR / "plants.db")))
ALIAS_CSV = BASE_DIR / "missing_species_alias.csv"

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TRANSLATE_TO_ITALIAN = True

def extract_wiki_info(url: str) -> dict[str, str]:
    """Extract language and page title from Wikipedia URL."""
    if not url or not url.strip():
        return {}
    
    url = url.strip()
    parsed = urlparse(url)
    
    # Extract lang from domain: it.wikipedia.org -> it, en.wikipedia.org -> en
    netloc = parsed.netloc.lower()  # e.g. "it.wikipedia.org"
    if "wikipedia.org" in netloc:
        lang = netloc.split(".")[0]  # e.g. "it"
    else:
        return {}
    
    # Extract title from path: /wiki/Page_Title -> Page_Title
    path = parsed.path
    if "/wiki/" in path:
        title = path.split("/wiki/")[1]
        title = unquote(title)  # decode URL encoding
    else:
        return {}
    
    return {"lang": lang, "title": title}

def process_missing_species():
    """Read missing_species_alias.csv and process each species via RAG."""
    
    client = OpenAI()
    chroma_client = chromadb.PersistentClient(path=str(RAG_DB_PATH))
    collection = chroma_client.get_or_create_collection(name="plants")
    
    species_processed = 0
    species_failed = 0
    
    with open(ALIAS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            species_name = (row.get("species_name") or "").strip()
            wiki_url = (row.get("wikipedia_url") or "").strip()
            
            if not species_name:
                continue
            
            print(f"\n[*] Processing {species_name}...")
            
            alias_info = extract_wiki_info(wiki_url) if wiki_url else None
            
            try:
                result = process_species(
                    species_name=species_name,
                    collection=collection,
                    wiki_langs=("it", "en"),
                    alias_info=alias_info,
                    translator_client=client,
                    translation_model=DEFAULT_MODEL,
                    translate_non_italian=TRANSLATE_TO_ITALIAN,
                    sqlite_path=SQLITE_DB_PATH,
                )
                
                status = result.get("status", "unknown")
                if status == "ok":
                    print(f"    ✓ Added to RAG")
                    species_processed += 1
                else:
                    print(f"    [!] Status: {status}")
                    species_failed += 1
            except Exception as e:
                print(f"    ✗ Error: {e}")
                species_failed += 1
    
    print(f"\n[✓] Processing complete: {species_processed} added, {species_failed} failed")

if __name__ == "__main__":
    process_missing_species()
