import argparse
import csv
import html
import json
import os
import re
import sqlite3
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import chromadb
import httpx
from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

SPECIES_CSV = BASE_DIR / "unique_species_labels.csv"
RAG_DB_PATH = Path(os.getenv("RAG_DB_PATH", str(DATA_DIR / "plant_rag")))
SQLITE_DB_PATH = Path(os.getenv("PLANTS_SQLITE_PATH", str(DATA_DIR / "plants.db")))

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
PROFILE_KEYS = (
    "annaffiatura_gg",
    "annaffiatura_time",
    "luce",
    "temperatura",
    "umidita",
    "altezza_media",
    "pulizia",
    "terriccio",
    "concimazione",
    "prevenzione",
)

RHS_SEARCH_URL = "https://www.rhs.org.uk/plants/search-results?query={query}"
MISSOURI_SEARCH_URL = (
    "https://www.missouribotanicalgarden.org/PlantFinder/PlantFinderSearch.aspx?basic={query}"
)
EPPO_SEARCH_URL = "https://gd.eppo.int/search?query={query}"

HTTP_TIMEOUT = 12.0
HTTP_USER_AGENT = os.getenv(
    "EXTERNAL_SOURCES_USER_AGENT",
    "ai-green-assistant/1.0 (contact: local-dev)",
)


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS plants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            species_name TEXT NOT NULL UNIQUE,
            indexed INTEGER NOT NULL DEFAULT 0,
            annaffiatura_gg INTEGER,
            annaffiatura_time TEXT,
            luce TEXT,
            temperatura TEXT,
            umidita TEXT,
            altezza_media TEXT,
            pulizia TEXT,
            terriccio TEXT,
            concimazione TEXT,
            prevenzione TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def load_species() -> list[str]:
    species: list[str] = []
    with open(SPECIES_CSV, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = (row.get("species_name") or "").strip()
            if name:
                species.append(name)
    return species


def get_rag_collection():
    client = chromadb.PersistentClient(path=str(RAG_DB_PATH))
    return client.get_collection(name="plants")


def get_rag_context(collection, species_name: str, max_chars: int = 9000) -> str:
    results = collection.get(
        where={"species_name": {"$eq": species_name}},
        limit=20,
    )
    docs = (results or {}).get("documents", [])
    if not docs:
        return ""

    context = "\n\n".join(docs)
    if len(context) > max_chars:
        context = context[:max_chars] + "\n..."
    return context


def _clean_json_payload(raw_text: str) -> str:
    txt = (raw_text or "").strip()
    if txt.startswith("```"):
        txt = txt.strip("`")
        if txt.startswith("json"):
            txt = txt[4:]
    return txt.strip()


def normalize_profile_data(data: dict) -> dict:
    allowed_keys = set(PROFILE_KEYS)
    normalized = {k: data.get(k) for k in allowed_keys}

    raw_days = normalized.get("annaffiatura_gg")
    if raw_days is None:
        normalized["annaffiatura_gg"] = None
    else:
        try:
            normalized["annaffiatura_gg"] = int(raw_days)
        except (TypeError, ValueError):
            normalized["annaffiatura_gg"] = None

    valid_time = {"mattino", "sera", "entrambi"}
    t = normalized.get("annaffiatura_time")
    if isinstance(t, str):
        t = t.strip().lower()
        normalized["annaffiatura_time"] = t if t in valid_time else None
    else:
        normalized["annaffiatura_time"] = None

    for key in allowed_keys - {"annaffiatura_gg", "annaffiatura_time"}:
        value = normalized.get(key)
        if value is None:
            continue
        normalized[key] = str(value).strip() or None

    return normalized


def _html_to_text(value: str) -> str:
    txt = re.sub(r"<script[\\s\\S]*?</script>", " ", value, flags=re.IGNORECASE)
    txt = re.sub(r"<style[\\s\\S]*?</style>", " ", txt, flags=re.IGNORECASE)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = html.unescape(txt)
    txt = re.sub(r"\\s+", " ", txt)
    return txt.strip()


def _fetch_page_text(url: str, species_name: str, max_chars: int = 5000) -> str:
    headers = {
        "User-Agent": HTTP_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
    }
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True, headers=headers) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return ""
            page_text = _html_to_text(resp.text)
            if not page_text:
                return ""
            species_low = species_name.lower()
            if species_low not in page_text.lower():
                return ""
            return page_text[:max_chars]
    except Exception:
        return ""


def fetch_external_sources(species_name: str) -> dict[str, str]:
    query = urllib.parse.quote_plus(species_name)
    rhs_text = _fetch_page_text(RHS_SEARCH_URL.format(query=query), species_name)
    missouri_text = _fetch_page_text(MISSOURI_SEARCH_URL.format(query=query), species_name)
    eppo_text = _fetch_page_text(EPPO_SEARCH_URL.format(query=query), species_name)

    return {
        "rhs": rhs_text,
        "missouri": missouri_text,
        "eppo": eppo_text,
    }


def normalize_profile_with_evidence(
    client: OpenAI,
    model: str,
    species_name: str,
    rag_context: str,
    partial_profile: dict | None,
    external_sources: dict[str, str] | None,
) -> dict:
    external_sources = external_sources or {}
    partial_profile = partial_profile or {}
    rhs = external_sources.get("rhs", "")
    missouri = external_sources.get("missouri", "")
    eppo = external_sources.get("eppo", "")

    system_msg = (
        "Sei un botanico professionista. Compila i campi solo usando le evidenze fornite. "
        "Priorita: RAG locale, poi RHS/Missouri per cura pratica, poi EPPO per prevenzione. "
        "Non inventare dati: se non ci sono evidenze affidabili usa null. "
        "Rispondi SOLO con JSON valido e senza testo extra."
    )

    user_msg = (
        f"Specie: {species_name}\n\n"
        "Profilo parziale gia estratto:\n"
        f"{json.dumps(partial_profile, ensure_ascii=False)}\n\n"
        "Compila/normalizza i campi JSON con queste chiavi esatte:\n"
        "annaffiatura_gg (numero intero o null),\n"
        "annaffiatura_time (mattino|sera|entrambi|null),\n"
        "luce, temperatura, umidita, altezza_media, pulizia, terriccio, concimazione, prevenzione.\n\n"
        "Evidenze RAG:\n"
        f"{rag_context or 'N/A'}\n\n"
        "Evidenze RHS (cura):\n"
        f"{rhs or 'N/A'}\n\n"
        "Evidenze Missouri Botanical Garden (cura):\n"
        f"{missouri or 'N/A'}\n\n"
        "Evidenze EPPO (prevenzione):\n"
        f"{eppo or 'N/A'}"
    )

    completion = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
    )
    payload = completion.choices[0].message.content or "{}"
    payload = _clean_json_payload(payload)
    data = json.loads(payload)
    return normalize_profile_data(data)


def extract_plant_profile(client: OpenAI, model: str, species_name: str, context: str) -> dict:
    system_msg = (
        "Sei un botanico professionista. Estrai solo dati supportati dal contesto fornito. "
        "Rispondi SOLO con JSON valido e senza testo extra. "
        "Se un dato manca, usa null."
    )
    user_msg = (
        f"Specie: {species_name}\n\n"
        "Estrai i seguenti campi in JSON con queste chiavi esatte:\n"
        "annaffiatura_gg (numero intero o null),\n"
        "annaffiatura_time (mattino|sera|entrambi|null),\n"
        "luce, temperatura, umidita, altezza_media, pulizia, terriccio, concimazione, prevenzione.\n"
        "\nContesto:\n"
        f"{context}"
    )

    completion = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
    )
    payload = completion.choices[0].message.content or "{}"
    payload = _clean_json_payload(payload)
    data = json.loads(payload)
    return normalize_profile_data(data)


def profile_has_missing_fields(profile: dict | None) -> bool:
    if not profile:
        return True
    return any(profile.get(key) is None for key in PROFILE_KEYS)


def merge_missing_fields(base_profile: dict | None, fallback_profile: dict | None) -> dict:
    merged = dict(base_profile or {})
    fallback_profile = fallback_profile or {}
    for key in PROFILE_KEYS:
        if merged.get(key) is None and fallback_profile.get(key) is not None:
            merged[key] = fallback_profile[key]
    return merged


def extract_plant_profile_generic(client: OpenAI, model: str, species_name: str, partial_profile: dict | None) -> dict:
    partial = partial_profile or {}
    system_msg = (
        "Sei un botanico professionista. Usa conoscenza generale botanica per stimare i campi mancanti. "
        "Rispondi SOLO con JSON valido e senza testo extra. "
        "Se non sei ragionevolmente sicuro, lascia null."
    )
    user_msg = (
        f"Specie: {species_name}\n\n"
        "Hai gia questi valori (da mantenere):\n"
        f"{json.dumps(partial, ensure_ascii=False)}\n\n"
        "Compila SOLO i campi mancanti in JSON con queste chiavi esatte:\n"
        "annaffiatura_gg (numero intero o null),\n"
        "annaffiatura_time (mattino|sera|entrambi|null),\n"
        "luce, temperatura, umidita, altezza_media, pulizia, terriccio, concimazione, prevenzione."
    )

    completion = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
    )
    payload = completion.choices[0].message.content or "{}"
    payload = _clean_json_payload(payload)
    data = json.loads(payload)
    return normalize_profile_data(data)


def upsert_plant(
    conn: sqlite3.Connection,
    species_name: str,
    indexed: bool,
    profile: dict | None,
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    profile = profile or {}

    conn.execute(
        """
        INSERT INTO plants (
            species_name,
            indexed,
            annaffiatura_gg,
            annaffiatura_time,
            luce,
            temperatura,
            umidita,
            altezza_media,
            pulizia,
            terriccio,
            concimazione,
            prevenzione,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(species_name) DO UPDATE SET
            indexed=excluded.indexed,
            annaffiatura_gg=excluded.annaffiatura_gg,
            annaffiatura_time=excluded.annaffiatura_time,
            luce=excluded.luce,
            temperatura=excluded.temperatura,
            umidita=excluded.umidita,
            altezza_media=excluded.altezza_media,
            pulizia=excluded.pulizia,
            terriccio=excluded.terriccio,
            concimazione=excluded.concimazione,
            prevenzione=excluded.prevenzione,
            updated_at=excluded.updated_at
        """,
        (
            species_name,
            1 if indexed else 0,
            profile.get("annaffiatura_gg"),
            profile.get("annaffiatura_time"),
            profile.get("luce"),
            profile.get("temperatura"),
            profile.get("umidita"),
            profile.get("altezza_media"),
            profile.get("pulizia"),
            profile.get("terriccio"),
            profile.get("concimazione"),
            profile.get("prevenzione"),
            now_iso,
        ),
    )


def already_enriched(conn: sqlite3.Connection, species_name: str) -> bool:
    row = conn.execute(
        """
        SELECT indexed, annaffiatura_gg, annaffiatura_time, luce, temperatura,
               umidita, altezza_media, pulizia, terriccio, concimazione, prevenzione
        FROM plants
        WHERE species_name = ?
        """,
        (species_name,),
    ).fetchone()
    if not row:
        return False
    indexed = bool(row[0])
    any_data = any(value is not None and str(value).strip() != "" for value in row[1:])
    return indexed and any_data


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Crea/aggiorna data/plants.db da CSV + RAG, con arricchimento OpenAI.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Processa solo le prime N specie")
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Ricalcola anche le specie gia arricchite nel DB",
    )
    parser.add_argument(
        "--generic-fallback",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Se mancano campi, tenta una stima OpenAI senza contesto RAG (default: true)",
    )
    parser.add_argument(
        "--external-sources",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Integra fonti esterne (RHS, Missouri, EPPO) prima della normalizzazione finale",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Modello OpenAI da usare")
    args = parser.parse_args()

    species = load_species()
    if args.limit and args.limit > 0:
        species = species[: args.limit]

    if not species:
        raise RuntimeError("Nessuna specie trovata nel CSV.")

    SQLITE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SQLITE_DB_PATH)
    init_db(conn)

    collection = get_rag_collection()

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    client = OpenAI(api_key=api_key) if api_key else None

    if client is None:
        print("OPENAI_API_KEY non impostata: verra compilato solo indexed true/false.")

    indexed_count = 0
    enriched_count = 0
    generic_fallback_count = 0
    external_sources_count = 0
    not_indexed_count = 0

    total = len(species)
    for i, species_name in enumerate(species, start=1):
        context = get_rag_context(collection, species_name)
        is_indexed = bool(context.strip())

        profile = None
        external_sources: dict[str, str] = {}
        if args.external_sources:
            external_sources = fetch_external_sources(species_name)
            if any(external_sources.values()):
                external_sources_count += 1

        if not is_indexed:
            if client is not None and args.generic_fallback:
                try:
                    profile = extract_plant_profile_generic(
                        client,
                        args.model,
                        species_name,
                        partial_profile=None,
                    )
                    if external_sources:
                        profile = normalize_profile_with_evidence(
                            client=client,
                            model=args.model,
                            species_name=species_name,
                            rag_context="",
                            partial_profile=profile,
                            external_sources=external_sources,
                        )
                    generic_fallback_count += 1
                    enriched_count += 1
                    print(f"[{i}/{total}] {species_name}: indexed=0, arricchita (fallback)")
                except Exception as exc:
                    print(f"[{i}/{total}] {species_name}: indexed=0, errore fallback OpenAI ({exc})")
            upsert_plant(conn, species_name, indexed=False, profile=profile)
            not_indexed_count += 1
            if profile is None:
                print(f"[{i}/{total}] {species_name}: indexed=0")
            continue

        indexed_count += 1
        if not args.force_refresh and already_enriched(conn, species_name):
            upsert_plant(conn, species_name, indexed=True, profile=None)
            print(f"[{i}/{total}] {species_name}: indexed=1 (gia arricchita)")
            continue

        if client is not None:
            try:
                profile = extract_plant_profile(client, args.model, species_name, context)
                if external_sources:
                    profile = normalize_profile_with_evidence(
                        client=client,
                        model=args.model,
                        species_name=species_name,
                        rag_context=context,
                        partial_profile=profile,
                        external_sources=external_sources,
                    )
                enriched_count += 1
                if args.generic_fallback and profile_has_missing_fields(profile):
                    fallback = extract_plant_profile_generic(client, args.model, species_name, partial_profile=profile)
                    profile = merge_missing_fields(profile, fallback)
                    generic_fallback_count += 1
                    print(f"[{i}/{total}] {species_name}: indexed=1, arricchita + fallback")
                else:
                    print(f"[{i}/{total}] {species_name}: indexed=1, arricchita")
            except Exception as exc:
                print(f"[{i}/{total}] {species_name}: indexed=1, errore OpenAI ({exc})")
        else:
            print(f"[{i}/{total}] {species_name}: indexed=1")

        upsert_plant(conn, species_name, indexed=True, profile=profile)

        if i % 50 == 0:
            conn.commit()

    conn.commit()
    conn.close()

    print("\n=== Completato ===")
    print(f"DB SQLite: {SQLITE_DB_PATH}")
    print(f"Specie processate: {total}")
    print(f"Presenti in RAG (indexed=1): {indexed_count}")
    print(f"Non presenti in RAG (indexed=0): {not_indexed_count}")
    print(f"Arricchite con OpenAI: {enriched_count}")
    print(f"Fallback generico usato: {generic_fallback_count}")
    print(f"Fonti esterne usate: {external_sources_count}")


if __name__ == "__main__":
    main()
