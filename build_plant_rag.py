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
import os
import re
import signal
import sqlite3
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Optional

import requests
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
RAG_DIR = DATA_DIR / "plant_rag"
IMAGES_DIR = DATA_DIR / "images"
SPECIES_CSV = BASE_DIR / "unique_species_labels.csv"
PROGRESS_FILE = DATA_DIR / "rag_progress.json"
DEFAULT_SQLITE_DB_PATH = Path(os.getenv("PLANTS_SQLITE_PATH", str(DATA_DIR / "plants.db")))
DEFAULT_ALIAS_CSV_PATH = BASE_DIR / "missing_species_alias.csv"

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
DEFAULT_WIKI_LANGS = ("it", "en", "fr", "es", "de", "pt")
DEFAULT_TRANSLATION_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


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


def parse_langs(raw_langs: str | None) -> tuple[str, ...]:
    if not raw_langs:
        return DEFAULT_WIKI_LANGS
    langs = []
    seen = set()
    for token in raw_langs.split(","):
        lang = token.strip().lower()
        if not lang or lang in seen:
            continue
        seen.add(lang)
        langs.append(lang)
    return tuple(langs) if langs else DEFAULT_WIKI_LANGS


def split_text_for_translation(text: str, max_chars: int = 7000) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        if len(paragraph) <= max_chars:
            current = paragraph
        else:
            for i in range(0, len(paragraph), max_chars):
                chunks.append(paragraph[i:i + max_chars])

    if current:
        chunks.append(current)

    return chunks or [text]


def translate_text_to_italian(
    client: OpenAI,
    model: str,
    species_name: str,
    source_lang: str,
    text: str,
) -> str:
    parts = split_text_for_translation(text)
    translated_parts: list[str] = []

    system_msg = (
        "Sei un traduttore tecnico botanico. Traduci in italiano mantenendo precisione, "
        "nomi scientifici, unita di misura e struttura del testo. "
        "Non aggiungere spiegazioni. Restituisci solo il testo tradotto."
    )

    for idx, part in enumerate(parts, start=1):
        user_msg = (
            f"Specie: {species_name}\n"
            f"Lingua sorgente: {source_lang}\n"
            f"Parte {idx}/{len(parts)}\n\n"
            f"Testo:\n{part}"
        )
        completion = client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
        )
        translated = (completion.choices[0].message.content or "").strip()
        if not translated:
            raise RuntimeError("Traduzione vuota dal modello OpenAI")
        translated_parts.append(translated)

    return "\n\n".join(translated_parts)


def load_species_from_sqlite_indexed_zero(sqlite_path: Path) -> list[str]:
    if not sqlite_path.exists():
        raise FileNotFoundError(f"Database SQLite non trovato: {sqlite_path}")

    conn = sqlite3.connect(sqlite_path)
    try:
        rows = conn.execute(
            """
            SELECT species_name
            FROM plants
            WHERE indexed = 0
            ORDER BY species_name COLLATE NOCASE
            """
        ).fetchall()
    finally:
        conn.close()

    return [str(r[0]).strip() for r in rows if r and str(r[0]).strip()]


def parse_wikipedia_url(url: str) -> Optional[tuple[str, str]]:
    txt = (url or "").strip()
    m = re.match(r"^https?://([a-z\-]+)\.wikipedia\.org/wiki/(.+)$", txt, flags=re.IGNORECASE)
    if not m:
        return None
    lang = m.group(1).lower()
    title = urllib.parse.unquote(m.group(2)).replace("_", " ").strip()
    if not title:
        return None
    return lang, title


def load_alias_map(csv_path: Path) -> dict[str, dict[str, str]]:
    if not csv_path.exists():
        return {}

    aliases: dict[str, dict[str, str]] = {}
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            species = (row.get("species_name") or "").strip()
            wiki_url = (row.get("wikipedia_url") or "").strip()
            if not species or not wiki_url:
                continue
            parsed = parse_wikipedia_url(wiki_url)
            if not parsed:
                continue
            lang, title = parsed
            aliases[species] = {
                "lang": lang,
                "title": title,
                "url": wiki_url,
            }
    return aliases


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

def process_species(
    species_name: str,
    collection: chromadb.Collection,
    wiki_langs: tuple[str, ...],
    alias_info: dict[str, str] | None,
    translator_client: OpenAI | None,
    translation_model: str,
    translate_non_italian: bool,
) -> dict:
    slug = slugify(species_name)
    species_img_dir = IMAGES_DIR / slug

    # --- Find Wikipedia page from configured languages ---
    result_extract: Optional[tuple[str, str]] = None
    lang = "it"

    if alias_info:
        alias_lang = alias_info.get("lang", "").strip().lower()
        alias_title = alias_info.get("title", "").strip()
        if alias_lang and alias_title:
            result_extract = fetch_wiki_extract(alias_title, alias_lang)
            if result_extract:
                lang = alias_lang
                print(f"  [alias] {species_name} -> {alias_lang}:{alias_title}")

    for try_lang in wiki_langs:
        if result_extract:
            break
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
    translated = False

    if lang != "it" and translate_non_italian and translator_client is not None:
        try:
            full_text = translate_text_to_italian(
                client=translator_client,
                model=translation_model,
                species_name=species_name,
                source_lang=lang,
                text=full_text,
            )
            translated = True
        except Exception as exc:
            print(f"  [warn] translation failed ({lang}->it): {exc}")

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
            "source_lang": lang,
            "translated_it": translated,
            "content_lang": "it" if translated else lang,
        }
        for i in range(len(chunks))
    ]

    collection.upsert(ids=ids, documents=chunks, metadatas=metadatas)

    print(
        f"  [ok] {len(chunks)} chunks | {len(image_paths)} images "
        f"| lang={lang} | translated={translated} | common='{common_name}'"
    )
    return {
        "species": species_name,
        "status": "ok",
        "chunks": len(chunks),
        "images": len(image_paths),
        "lang": lang,
        "translated_it": translated,
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
    import argparse

    parser = argparse.ArgumentParser(
        description="Build/aggiorna RAG piante da Wikipedia con fallback multilingua.",
    )
    parser.add_argument(
        "--langs",
        default=",".join(DEFAULT_WIKI_LANGS),
        help="Lingue Wikipedia in ordine di tentativo, separate da virgola (es: it,en,fr,es)",
    )
    parser.add_argument(
        "--from-sqlite-indexed-zero",
        action="store_true",
        help="Processa solo specie con indexed=0 nel DB SQLite plants",
    )
    parser.add_argument(
        "--sqlite-path",
        default=str(DEFAULT_SQLITE_DB_PATH),
        help="Percorso DB SQLite usato con --from-sqlite-indexed-zero",
    )
    parser.add_argument(
        "--translate-non-italian",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Traduce in italiano i contenuti wiki trovati in lingua diversa da it (default: true)",
    )
    parser.add_argument(
        "--translation-model",
        default=DEFAULT_TRANSLATION_MODEL,
        help="Modello OpenAI per traduzione in italiano",
    )
    parser.add_argument(
        "--alias-csv",
        default=str(DEFAULT_ALIAS_CSV_PATH),
        help="CSV mapping specie->url Wikipedia (colonne: species_name;wikipedia_url)",
    )
    args = parser.parse_args()

    wiki_langs = parse_langs(args.langs)

    RAG_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    # Read species list (from CSV or SQLite indexed=0)
    species_list: list[str] = []
    if args.from_sqlite_indexed_zero:
        sqlite_path = Path(args.sqlite_path)
        species_list = load_species_from_sqlite_indexed_zero(sqlite_path)
        print(f"Species from SQLite indexed=0: {len(species_list)}")
    else:
        with open(SPECIES_CSV, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                name = row.get("species_name", "").strip()
                if name:
                    species_list.append(name)

    print(f"Species to process: {len(species_list)}")

    alias_map = load_alias_map(Path(args.alias_csv))
    if alias_map:
        print(f"Alias mappings loaded: {len(alias_map)}")

    translator_client: OpenAI | None = None
    if args.translate_non_italian:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if api_key:
            translator_client = OpenAI(api_key=api_key)
        else:
            print("[warn] OPENAI_API_KEY assente: traduzione disattivata, uso testo originale.")

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
            result = process_species(
                species_name=species,
                collection=collection,
                wiki_langs=wiki_langs,
                alias_info=alias_map.get(species),
                translator_client=translator_client,
                translation_model=args.translation_model,
                translate_non_italian=args.translate_non_italian,
            )
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
