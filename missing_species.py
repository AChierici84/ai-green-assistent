import os
from pathlib import Path

import chromadb
import pandas as pd
import serpapi
from dotenv import load_dotenv
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from add_species_to_faiss import add_to_faiss, resolve_title
from build_plant_rag import (
    COLLECTION_NAME,
    DEFAULT_TRANSLATION_MODEL,
    EMBEDDING_MODEL,
    RAG_DIR,
    process_species,
)
from build_plants_sqlite import SQLITE_DB_PATH, get_rag_collection, get_rag_context, init_db, sqlite3, upsert_plant


load_dotenv(dotenv_path=Path(__file__).with_name(".env"))


def _extract_image_url(image: dict) -> str | None:
    for key in ("original", "thumbnail", "source", "link", "image_url"):
        url = image.get(key)
        if isinstance(url, str) and url.strip():
            return url.strip()
    return None


def download_images( species_name, location="Italy", num_images=100):
    client = serpapi.Client(api_key=os.getenv("SERP_API_KEY"))
    results = client.search({
      "engine": "google_images",
      "q": species_name,
      "location": location,
      "google_domain": "google.it",
      "hl": "it",
      "gl": "it"
    })
    images_results = results["images_results"]
    return images_results   


def load_missing_species():
    df= pd.read_csv("missing_species.csv")
    return df["species_name"].tolist()


def _ensure_plants_db_record(species_name: str, indexed: bool) -> None:
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        init_db(conn)
        upsert_plant(conn, species_name=species_name, indexed=indexed, profile=None)
        conn.commit()


def _sync_indexed_from_rag(species_name: str) -> bool:
    collection = get_rag_collection()
    context = get_rag_context(collection, species_name)
    indexed = bool((context or "").strip())
    _ensure_plants_db_record(species_name, indexed=indexed)
    return indexed


def _fallback_add_without_images(
    species_name: str,
    rag_collection,
    lang: str,
    resolved_title: str,
) -> None:
    alias = {
        "lang": lang,
        "title": resolved_title,
        "url": f"https://{lang}.wikipedia.org/wiki/{resolved_title.replace(' ', '_')}",
    }
    try:
        result = process_species(
            species_name=species_name,
            collection=rag_collection,
            wiki_langs=("it", "en", "fr", "es", "de", "pt"),
            alias_info=alias,
            translator_client=None,
            translation_model=DEFAULT_TRANSLATION_MODEL,
            translate_non_italian=False,
            sqlite_path=SQLITE_DB_PATH,
        )
        indexed = _sync_indexed_from_rag(species_name)
        print(
            f"[ok] {species_name}: fallback without images -> "
            f"RAG status={result.get('status')} | plants.db indexed={indexed}"
        )
    except Exception as e:
        _ensure_plants_db_record(species_name, indexed=False)
        print(
            f"[warn] {species_name}: RAG fallback failed ({e}); "
            "inserted placeholder in plants.db (indexed=0)"
        )

def main():
    missing_species = load_missing_species()

    RAG_DIR.mkdir(parents=True, exist_ok=True)
    ef = SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)
    rag_client = chromadb.PersistentClient(path=str(RAG_DIR))
    rag_collection = rag_client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    for species in missing_species:
        
        #verifica se presente un file con i risultati per questa specie
        if os.path.exists(f"logs/images/{species.replace(' ', '_')}_images.txt"):
            #carica i risultati dal file
            with open(f"logs/images/{species.replace(' ', '_')}_images.txt", "r") as f:
                images = f.read().splitlines()
            print(f"Loaded {len(images)} images for {species} from logs/images/{species.replace(' ', '_')}_images.txt")
        else:
            print(f"Searching images for {species}...")
            try:
                images_raw = download_images(species)
                print(f"Downloaded {len(images_raw)} images for {species}.")
                # salva i risultati in un file nella cartella logs/images
                images = []
                with open(f"logs/images/{species.replace(' ', '_')}_images.txt", "w") as f:
                    for image in images_raw:
                        url = _extract_image_url(image)
                        if url:
                            f.write(url + "\n")
                            images.append(url)

                print(f"Saved image URLs for {species} in logs/images/{species.replace(' ', '_')}_images.txt")
            except Exception as e:
                print(f"[warn] Image download failed for {species}: {e} | continuing with RAG/plants.db only")
                images = []
        
        # Ensure images is a list of URL strings
        if images and isinstance(images[0], dict):
            images = [u for img in images if (u := _extract_image_url(img))]
        
        # Try to resolve Wikipedia page (with fallback variants)
        try:
            langs = ("it", "en", "fr", "es", "de", "pt")
            lang, resolved_title = resolve_title(species, "", langs)
            print(f"Resolved Wikipedia page: {lang}:{resolved_title}")
        except RuntimeError as e:
            _ensure_plants_db_record(species, indexed=False)
            print(f"[skip] {species}: {e} | inserted in plants.db with indexed=0")
            continue

        if not images:
            _fallback_add_without_images(species, rag_collection, lang, resolved_title)
            continue

        # Add to FAISS, RAG, and plants.db
        try:
            add_to_faiss(
                species,
                images,
                lang=lang,
                resolved_title=resolved_title,
            )
            print(f"[ok] {species}: added to FAISS, RAG, and plants.db")
        except Exception as e:
            print(f"[error] {species}: {e}")



if __name__ == "__main__":
    main()
