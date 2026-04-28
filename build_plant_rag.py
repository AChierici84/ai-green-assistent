#!/usr/bin/env python3
"""
Build a plant RAG from Wikipedia for every species in unique_species_labels.csv.

Output
------
  data/plant_rag/          — ChromaDB persistent store
  data/images/<slug>/      — downloaded images (up to MAX_IMAGES per species)
  data/rag_progress.json   — resume file (re-run safe)

Notes
-----
- Uses the MediaWiki action API (more reliable than the REST API).
- Tries Italian Wikipedia first, falls back to English.
- Skips sections: Note, Bibliografia, Voci correlate, Altri progetti,
  Collegamenti esterni (and their English equivalents).
- Metadata per chunk: species_name, common_name, image_paths (JSON list), lang.
"""

import csv
import json
import re
import signal
import sys
import time
from pathlib import Path
from typing import Optional

import requests
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
RAG_DIR = DATA_DIR / "plant_rag"
IMAGES_DIR = DATA_DIR / "images"
SPECIES_CSV = BASE_DIR / "unique_species_labels.csv"
PROGRESS_FILE = DATA_DIR / "rag_progress.json"

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
HEADERS = {"User-Agent": "ai-green-assistant/1.0 (https://github.com/local)"}

# Section titles (lowercase) to skip entirely
SKIP_SECTIONS: set[str] = {
    "note", "bibliografia", "voci correlate", "altri progetti",
    "collegamenti esterni", "references", "notes", "external links",
    "see also", "further reading", "gallery", "galerie",
}

# Icon/logo file substrings that should not be downloaded as plant images
IMAGE_SKIP_KEYWORDS = (
    "commons-logo", "wikidata", "wikiquote", "disambig", "folder",
    "wiktionary", "wikimedia", "icon", "edit-clear", "blue_pencil",
    "padlock", "question_book", "portal", "wikiversity",
)

IMAGE_SKIP_EXTENSIONS = (
    ".svg",
    ".ogg",
    ".ogv",
    ".webm",
)

COLLECTION_NAME = "plants"
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MAX_IMAGES = 5
CHUNK_WORDS = 150
CHUNK_OVERLAP_WORDS = 20
REQUEST_DELAY = 0.5   # seconds between Wikipedia calls


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    return re.sub(r"[^\w]", "_", name).lower()


def extract_common_name(lead_text: str, species_name: str) -> str:
    """Heuristically extract the common name from the Wikipedia lead paragraph."""
    first_line = lead_text.split("\n", 1)[0].strip()

    # Frequent structure in IT/EN pages: "<common name> (Species binomial ...)"
    # Example: "L'abete bianco (Abies alba Mill., 1759) ..."
    m_lead = re.match(r"^([^\(\)\n]{3,80})\(\s*[^\)]*\)", first_line)
    if m_lead:
        candidate = m_lead.group(1).strip(" ,;:-")
        candidate = re.sub(r"^(?:l'|la|il|lo|i|gli|le|the|a|an)\s+", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"^(?:l')", "", candidate, flags=re.IGNORECASE)
        if candidate and candidate.lower() != species_name.lower():
            return candidate

    patterns = [
        r"(?:nome comune|nome volgare|comunemente (?:chiamat[ao]|conosciut[ao] come))"
        r"[:\s]+([^\.,;\(\)\n]{3,60})",
        r"(?:common name|commonly known as)[:\s]+([^\.,;\(\)\n]{3,60})",
    ]
    for pat in patterns:
        m = re.search(pat, lead_text, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip(",;")
    return ""


def chunk_by_words(text: str, size: int = CHUNK_WORDS, overlap: int = CHUNK_OVERLAP_WORDS) -> list[str]:
    words = text.split()
    if not words:
        return []
    step = max(1, size - overlap)
    return [
        " ".join(words[start: start + size])
        for start in range(0, len(words), step)
        if words[start: start + size]
    ]


def split_wiki_sections(extract: str) -> list[tuple[str, str]]:
    """Split a plain-text extract (exsectionformat=wiki) into (title, body) pairs.

    Level-2 headings look like:  == Title ==
    Level-3+:                    === Title ===  (treated as part of parent section)
    Returns a list where title='' for the lead.
    """
    # Split on level-2 headings only
    pattern = re.compile(r"^==\s*(.+?)\s*==\s*$", re.MULTILINE)
    parts = pattern.split(extract)
    # parts alternates: [lead_body, title1, body1, title2, body2, ...]
    sections: list[tuple[str, str]] = []
    # Lead section
    if parts:
        sections.append(("", parts[0].strip()))
    # Named sections
    for i in range(1, len(parts) - 1, 2):
        title = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        sections.append((title, body))
    return sections


# ---------------------------------------------------------------------------
# Wikipedia action API
# ---------------------------------------------------------------------------

def _action_api(lang: str, **params) -> Optional[dict]:
    url = f"https://{lang}.wikipedia.org/w/api.php"
    try:
        r = requests.get(url, params={"format": "json", **params}, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def fetch_wiki_extract(title: str, lang: str) -> Optional[tuple[str, str]]:
    """Return (resolved_title, plain_text_extract) or None if page not found."""
    data = _action_api(
        lang,
        action="query",
        prop="extracts",
        titles=title,
        explaintext="1",
        exsectionformat="wiki",
        redirects="1",
    )
    if not data:
        return None
    pages = data.get("query", {}).get("pages", {})
    for pid, page in pages.items():
        if pid == "-1":
            return None
        return (page.get("title", title), page.get("extract", ""))
    return None


def fetch_wiki_image_urls(title: str, lang: str) -> list[str]:
    """Return up to MAX_IMAGES+5 image URLs for the page, filtering out icons."""
    # Step 1 — get file names listed on the page
    data = _action_api(
        lang,
        action="query",
        prop="images",
        titles=title,
        redirects="1",
        imlimit="30",
    )
    if not data:
        return []

    pages = data.get("query", {}).get("pages", {})
    file_titles: list[str] = []
    for pid, page in pages.items():
        if pid == "-1":
            return []
        for img in page.get("images", []):
            t = img.get("title", "")
            t_lower = t.lower()
            if any(kw in t_lower for kw in IMAGE_SKIP_KEYWORDS):
                continue
            file_titles.append(t)

    if not file_titles:
        return []

    # Step 2 — resolve file names to direct URLs (batch, max 50)
    batch = file_titles[: min(len(file_titles), 25)]
    data2 = _action_api(
        lang,
        action="query",
        prop="imageinfo",
        titles="|".join(batch),
        iiprop="url",
    )
    if not data2:
        return []

    urls: list[str] = []
    for pid, page in data2.get("query", {}).get("pages", {}).items():
        for info in page.get("imageinfo", []):
            u = info.get("url", "")
            if u:
                urls.append(u)

    return urls


# ---------------------------------------------------------------------------
# Image download
# ---------------------------------------------------------------------------

def download_image(img_url: str, save_path: Path) -> bool:
    try:
        r = requests.get(img_url, headers=HEADERS, timeout=20, stream=True)
        r.raise_for_status()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        return True
    except Exception as e:
        print(f"    [img-err] {img_url}: {e}")
        return False


def collect_images(species_name: str, lang: str, species_img_dir: Path) -> list[str]:
    """Download up to MAX_IMAGES images; return list of absolute saved paths."""
    urls = fetch_wiki_image_urls(species_name, lang)
    image_paths: list[str] = []

    for img_url in urls:
        url_lower = img_url.lower()
        file_name_lower = img_url.split("?")[0].rstrip("/").split("/")[-1].lower()
        if any(url_lower.endswith(ext) for ext in IMAGE_SKIP_EXTENSIONS):
            continue
        if any(kw in file_name_lower for kw in IMAGE_SKIP_KEYWORDS):
            continue

        raw_name = img_url.split("?")[0].rstrip("/").split("/")[-1]
        safe_name = re.sub(r"[^\w.\-]", "_", raw_name)
        save_path = species_img_dir / safe_name

        if save_path.exists():
            image_paths.append(str(save_path.relative_to(BASE_DIR)))
        else:
            if download_image(img_url, save_path):
                image_paths.append(str(save_path.relative_to(BASE_DIR)))

        if len(image_paths) >= MAX_IMAGES:
            break

    return image_paths


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

def process_species(species_name: str, collection: chromadb.Collection) -> dict:
    slug = slugify(species_name)
    species_img_dir = IMAGES_DIR / slug

    # --- Find Wikipedia page (Italian first, then English) ---
    result_extract: Optional[tuple[str, str]] = None
    lang = "it"
    for try_lang in ("it", "en"):
        result_extract = fetch_wiki_extract(species_name, try_lang)
        if result_extract:
            lang = try_lang
            break

    if not result_extract:
        print(f"  [skip] no wiki page")
        return {"species": species_name, "status": "not_found"}

    resolved_title, extract = result_extract

    # --- Parse sections, skip unwanted ones ---
    sections = split_wiki_sections(extract)
    text_parts: list[str] = []
    for sec_title, sec_body in sections:
        if sec_title.lower() in SKIP_SECTIONS:
            continue
        if not sec_body:
            continue
        text_parts.append(f"{sec_title}\n{sec_body}" if sec_title else sec_body)

    full_text = "\n\n".join(text_parts)

    # Common name extracted from lead
    lead_text = sections[0][1] if sections else ""
    common_name = extract_common_name(lead_text, species_name)

    # --- Images ---
    species_img_dir.mkdir(parents=True, exist_ok=True)
    image_paths = collect_images(resolved_title, lang, species_img_dir)

    # --- Chunk & upsert into ChromaDB ---
    chunks = chunk_by_words(full_text)
    if not chunks:
        return {"species": species_name, "status": "no_text"}

    ids = [f"{slug}__{i}" for i in range(len(chunks))]
    metadatas = [
        {
            "species_name": species_name,
            "common_name": common_name,
            "image_paths": json.dumps(image_paths),
            "chunk_index": i,
            "lang": lang,
        }
        for i in range(len(chunks))
    ]

    collection.upsert(ids=ids, documents=chunks, metadatas=metadatas)

    print(
        f"  [ok] {len(chunks)} chunks | {len(image_paths)} images "
        f"| lang={lang} | common='{common_name}'"
    )
    return {
        "species": species_name,
        "status": "ok",
        "chunks": len(chunks),
        "images": len(image_paths),
        "lang": lang,
        "common_name": common_name,
    }


# ---------------------------------------------------------------------------
# Progress persistence
# ---------------------------------------------------------------------------

def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_progress(progress: dict) -> None:
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    RAG_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    # Read species list
    species_list: list[str] = []
    with open(SPECIES_CSV, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = row.get("species_name", "").strip()
            if name:
                species_list.append(name)

    print(f"Species in CSV: {len(species_list)}")

    # ChromaDB with multilingual sentence-transformer embeddings
    ef = SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)
    client = chromadb.PersistentClient(path=str(RAG_DIR))
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    # Resume support — skip species already successfully indexed
    progress = load_progress()
    already_done = {k for k, v in progress.items() if v.get("status") == "ok"}
    todo = [s for s in species_list if s not in already_done]
    print(f"Already indexed: {len(already_done)} | To process: {len(todo)}")

    # Save progress on Ctrl-C
    def _signal_handler(sig, frame):  # noqa: ANN001
        print("\nInterrupted — saving progress...")
        save_progress(progress)
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)

    for i, species in enumerate(todo):
        global_idx = species_list.index(species) + 1
        print(f"[{global_idx}/{len(species_list)}] {species}")
        try:
            result = process_species(species, collection)
            progress[species] = result
        except Exception as exc:
            print(f"  ERROR: {exc}")
            progress[species] = {"species": species, "status": "error", "error": str(exc)}

        # Checkpoint every 20 species
        if (i + 1) % 20 == 0:
            save_progress(progress)
            done_count = sum(1 for v in progress.values() if v.get("status") == "ok")
            print(f"  --- checkpoint: {done_count}/{len(species_list)} ok ---")

        time.sleep(REQUEST_DELAY)

    save_progress(progress)

    done_count = sum(1 for v in progress.values() if v.get("status") == "ok")
    not_found = sum(1 for v in progress.values() if v.get("status") == "not_found")
    errors = sum(1 for v in progress.values() if v.get("status") == "error")

    print("\n=== Completed ===")
    print(f"  Indexed OK : {done_count}")
    print(f"  Not found  : {not_found}")
    print(f"  Errors     : {errors}")
    print(f"  Total docs : {collection.count()}")
    print(f"  ChromaDB   : {RAG_DIR}")
    print(f"  Images     : {IMAGES_DIR}")


if __name__ == "__main__":
    main()
