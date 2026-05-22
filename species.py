import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import chromadb
import httpx
from fastapi import HTTPException


logger = logging.getLogger("ai_green_assistant.api")


class SpeciesService:
    def __init__(
        self,
        *,
        data_storage: Any,
        rag_db_path: str,
        get_rag_collection_handle: Callable[[], Any],
        set_rag_collection_handle: Callable[[Any], None],
        normalize_image_path: Callable[[str], str],
        plant_profile_fields: list[str],
        model_name: str,
        index_path: str,
        cache_path: str,
        leafsnap_index_path: str,
        leafsnap_cache_path: str,
        wiki_user_agent: str,
        get_index_handle: Callable[[], Any],
        set_index_handle: Callable[[Any], None],
        reset_search_handles: Callable[[], None],
    ) -> None:
        self.data_storage = data_storage
        self.rag_db_path = rag_db_path
        self.get_rag_collection_handle = get_rag_collection_handle
        self.set_rag_collection_handle = set_rag_collection_handle
        self.normalize_image_path = normalize_image_path
        self.plant_profile_fields = plant_profile_fields
        self.model_name = model_name
        self.index_path = index_path
        self.cache_path = cache_path
        self.leafsnap_index_path = leafsnap_index_path
        self.leafsnap_cache_path = leafsnap_cache_path
        self.wiki_user_agent = wiki_user_agent
        self.get_index_handle = get_index_handle
        self.set_index_handle = set_index_handle
        self.reset_search_handles = reset_search_handles

        self.species_build_jobs: dict[str, dict[str, Any]] = {}
        self.species_build_jobs_lock = threading.Lock()

    def get_rag_collection(self) -> Any:
        collection = self.get_rag_collection_handle()
        if collection is None:
            try:
                client = chromadb.PersistentClient(path=self.rag_db_path)
                collection = client.get_collection(name="plants")
                self.set_rag_collection_handle(collection)
            except Exception as exc:
                raise RuntimeError(f"Impossibile caricare il database RAG delle piante: {exc}")
        return collection

    def build_profile_context(self, profile: dict[str, Any] | None) -> str:
        if not profile:
            return ""

        labels = {
            "species_name": "Specie",
            "indexed": "Presente in RAG",
            "annaffiatura_gg": "Annaffiatura ogni giorni",
            "annaffiatura_time": "Momento annaffiatura",
            "luce": "Luce",
            "temperatura": "Temperatura",
            "umidita": "Umidita",
            "altezza_media": "Altezza media",
            "pulizia": "Pulizia",
            "terriccio": "Terriccio",
            "concimazione": "Concimazione",
            "prevenzione": "Prevenzione",
            "updated_at": "Ultimo aggiornamento",
        }

        lines = []
        for field in self.plant_profile_fields:
            value = profile.get(field)
            if value is None or value == "":
                continue
            if field == "indexed":
                value = "si" if value else "no"
            lines.append(f"- {labels[field]}: {value}")

        if not lines:
            return ""

        return "Dati strutturati estratti da plants.db:\n" + "\n".join(lines)

    def get_index(self) -> Any:
        loaded_index = self.get_index_handle()
        if loaded_index is None:
            try:
                from plentclef import PlentClefIndex

                leafsnap_aliases = self.data_storage.get_leafsnap_aliases()

                loaded_index = PlentClefIndex(
                    model_name=self.model_name,
                    index_path=self.index_path,
                    index_cache=self.cache_path,
                    leafsnap_index_path=self.leafsnap_index_path,
                    leafsnap_cache_path=self.leafsnap_cache_path,
                    leafsnap_aliases=leafsnap_aliases,
                )
                self.set_index_handle(loaded_index)
            except Exception as exc:
                cause = f"{type(exc).__name__}: {exc}"
                raise RuntimeError(
                    "Impossibile inizializzare il motore di ricerca immagini. "
                    "Probabile blocco di sicurezza su librerie native (es. torch/faiss). "
                    f"Dettaglio: {cause}."
                ) from exc
        return loaded_index

    def get_catalog_and_faiss_stats(self) -> dict[str, Any]:
        species_db_total = 0
        species_rag_total = 0
        catalog_ok = False
        catalog_error = ""

        try:
            species_db_total, species_rag_total = self.data_storage.get_catalog_species_stats()
            catalog_ok = True
        except Exception as exc:
            catalog_error = f"{type(exc).__name__}: {exc}"

        faiss_ok = False
        faiss_error = ""
        plantclef_images_total = 0
        plantclef_species_total = 0
        leafsnap_images_total = 0
        leafsnap_species_total = 0

        try:
            loaded_index = self.get_index()
            plantclef_labels = list(getattr(loaded_index, "plantclef_labels", []) or [])
            leafsnap_labels = list(getattr(loaded_index, "leafsnap_labels", []) or [])

            plantclef_images_total = len(plantclef_labels)
            plantclef_species_total = len({str(v).strip().lower() for v in plantclef_labels if str(v).strip()})
            leafsnap_images_total = len(leafsnap_labels)
            leafsnap_species_total = len({str(v).strip().lower() for v in leafsnap_labels if str(v).strip()})
            faiss_ok = True
        except Exception as exc:
            faiss_error = f"{type(exc).__name__}: {exc}"

        return {
            "catalog": {
                "ok": catalog_ok,
                "error": catalog_error,
                "species_db_total": species_db_total,
                "species_rag_total": species_rag_total,
            },
            "faiss": {
                "ok": faiss_ok,
                "error": faiss_error,
                "plantclef": {
                    "images_total": plantclef_images_total,
                    "species_total": plantclef_species_total,
                },
                "leafsnap": {
                    "images_total": leafsnap_images_total,
                    "species_total": leafsnap_species_total,
                },
            },
        }

    def _insert_draft_plant_if_missing(self, species_name: str, api_key: str) -> bool:
        inserted = self.data_storage.insert_draft_plant_if_missing(species_name)
        if not inserted:
            return False
        logger.info("Draft plant inserted: '%s' (indexed=0)", species_name)
        return True

    def _species_build_status(self, species_name: str) -> dict[str, Any]:
        key = species_name.strip().lower()
        with self.species_build_jobs_lock:
            payload = self.species_build_jobs.get(key)
            if payload:
                return dict(payload)

        profile = self.data_storage.get_plant_profile_from_db(species_name)
        if profile and profile.get("indexed"):
            return {
                "species": profile.get("species_name") or species_name,
                "status": "completed",
                "started_at": None,
                "finished_at": profile.get("updated_at"),
                "error": None,
                "result": {"indexed": True},
            }

        return {
            "species": species_name,
            "status": "not_started",
            "started_at": None,
            "finished_at": None,
            "error": None,
            "result": None,
        }

    def _set_species_build_job(self, species_name: str, **updates: Any) -> None:
        key = species_name.strip().lower()
        with self.species_build_jobs_lock:
            current = self.species_build_jobs.get(key, {"species": species_name})
            current.update(updates)
            self.species_build_jobs[key] = current

    def _run_species_build_job(self, species_name: str) -> None:
        self._set_species_build_job(
            species_name,
            status="running",
            started_at=datetime.utcnow().isoformat(),
            finished_at=None,
            error=None,
        )

        try:
            from add_species_to_faiss import add_to_faiss, fetch_wiki_image_urls, resolve_title

            langs = tuple(
                x.strip().lower() for x in os.getenv("WIKI_LANGS", "it,en").split(",") if x.strip()
            )
            max_images = max(4, int(os.getenv("RAG_BUILD_MAX_IMAGES", "8")))
            lang, resolved_title = resolve_title(species_name, "", langs)
            image_urls = fetch_wiki_image_urls(resolved_title, lang, max_images=max_images)
            if not image_urls:
                logger.warning(
                    "No image URLs found for '%s' on %s:%s. Continuing build with textual ingestion only.",
                    species_name,
                    lang,
                    resolved_title,
                )

            add_result = add_to_faiss(
                species_name,
                image_urls,
                lang=lang,
                resolved_title=resolved_title,
                model_name=self.model_name,
                index_path=Path(self.index_path),
                cache_path=Path(self.cache_path),
            )

            hf_synced = False
            hf_error = None
            if os.getenv("AUTO_SYNC_HF_ASSETS", "1").strip().lower() in {"1", "true", "yes", "on"}:
                try:
                    from upload_hf_assets import DEFAULT_REPO_ID, upload_assets

                    hf_token = os.getenv("HF_TOKEN", "").strip() or None
                    uploaded = upload_assets(
                        repo_id=os.getenv("HF_ASSETS_DATASET_REPO", DEFAULT_REPO_ID),
                        private=False,
                        include_plants_db=True,
                        skip_missing=True,
                        token=hf_token,
                    )
                    hf_synced = uploaded > 0
                except Exception as exc:
                    hf_error = str(exc)
                    logger.warning("HF sync failed for '%s': %s", species_name, exc)

            self.reset_search_handles()

            self._set_species_build_job(
                species_name,
                status="completed",
                finished_at=datetime.utcnow().isoformat(),
                error=None,
                result={
                    "species": species_name,
                    "add_result": add_result,
                    "hf_synced": hf_synced,
                    "hf_error": hf_error,
                },
            )
            logger.info("Species build completed for '%s'", species_name)
        except Exception as exc:
            self._set_species_build_job(
                species_name,
                status="failed",
                finished_at=datetime.utcnow().isoformat(),
                error=f"{type(exc).__name__}: {exc}",
            )
            logger.exception("Species build failed for '%s': %s", species_name, exc)

    def _ensure_species_build_job(self, species_name: str) -> dict[str, Any]:
        status = self._species_build_status(species_name)
        if status.get("status") in {"queued", "running", "completed"}:
            return status

        self._set_species_build_job(
            species_name,
            species=species_name,
            status="queued",
            started_at=None,
            finished_at=None,
            error=None,
            result=None,
        )

        thread = threading.Thread(
            target=self._run_species_build_job,
            args=(species_name,),
            daemon=True,
            name=f"species-build-{species_name[:24]}",
        )
        thread.start()
        return self._species_build_status(species_name)

    def _species_to_folder_name(self, species_name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", str(species_name or "").lower()).strip("_")

    def _get_species_preview_image_url(self, species_name: str) -> str:
        image_paths = self.data_storage.get_species_images_from_db(species_name)
        for raw_path in image_paths:
            if isinstance(raw_path, str) and raw_path.startswith(("http://", "https://")):
                return raw_path

            normalized_path = self.normalize_image_path(str(raw_path or ""))
            if not normalized_path:
                continue

            local_path = Path("data") / "images" / normalized_path
            if local_path.exists():
                return f"/images/{normalized_path}"

        try:
            collection = self.get_rag_collection()
            res = collection.get(
                where={"species_name": {"$eq": species_name}},
                limit=1,
            )
            metadatas = res.get("metadatas", []) if res else []
            metadata = metadatas[0] if metadatas else {}
            image_paths_json = metadata.get("image_paths", "[]") if metadata else "[]"

            try:
                image_paths = json.loads(image_paths_json)
            except (json.JSONDecodeError, TypeError):
                image_paths = []

            for raw_path in image_paths:
                if isinstance(raw_path, str) and raw_path.startswith(("http://", "https://")):
                    return raw_path

                normalized_path = self.normalize_image_path(str(raw_path or ""))
                if not normalized_path:
                    continue

                local_path = Path("data") / "images" / normalized_path
                if local_path.exists():
                    return f"/images/{normalized_path}"
        except Exception:
            pass

        folder_name = self._species_to_folder_name(species_name)
        if not folder_name:
            return ""

        image_dir = Path("data") / "images" / folder_name
        if not image_dir.exists() or not image_dir.is_dir():
            return ""

        candidates = sorted(
            [
                path
                for path in image_dir.iterdir()
                if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
            ]
        )
        if not candidates:
            return ""

        return f"/images/{folder_name}/{candidates[0].name}"

    def fetch_wikipedia_text_context(self, name: str, lang: str) -> dict[str, Any]:
        base = f"https://{lang}.wikipedia.org"
        wiki_headers = {
            "User-Agent": self.wiki_user_agent,
            "Accept": "application/json",
        }

        with httpx.Client(timeout=10.0, headers=wiki_headers, follow_redirects=True) as client:
            search_resp = client.get(
                f"{base}/w/api.php",
                params={
                    "action": "opensearch",
                    "search": name,
                    "limit": 1,
                    "format": "json",
                },
            )
            titles: list[str] = []
            if search_resp.status_code == 200:
                search_data = search_resp.json()
                titles = search_data[1]

            if not titles:
                query_resp = client.get(
                    f"{base}/w/api.php",
                    params={
                        "action": "query",
                        "list": "search",
                        "srsearch": name,
                        "srlimit": 1,
                        "format": "json",
                    },
                )
                if query_resp.status_code == 200:
                    query_data = query_resp.json()
                    items = query_data.get("query", {}).get("search", [])
                    if items:
                        titles = [items[0].get("title", "")]

            if not titles:
                raise HTTPException(status_code=404, detail=f"Nessuna pagina Wikipedia trovata per '{name}'.")

            page_title = titles[0]
            safe_title = page_title.replace(" ", "_")

            summary_resp = client.get(f"{base}/api/rest_v1/page/summary/{safe_title}")
            summary_resp.raise_for_status()
            summary = summary_resp.json()

            long_resp = client.get(
                f"{base}/w/api.php",
                params={
                    "action": "query",
                    "prop": "extracts",
                    "titles": page_title,
                    "explaintext": 1,
                    "redirects": 1,
                    "format": "json",
                },
            )
            long_text = ""
            if long_resp.status_code == 200:
                long_data = long_resp.json()
                pages = long_data.get("query", {}).get("pages", {})
                if isinstance(pages, dict) and pages:
                    first_page = next(iter(pages.values()))
                    long_text = (first_page.get("extract") or "").strip()

        title = summary.get("title", page_title)
        extract = summary.get("extract", "Nessuna descrizione disponibile.")
        page_url = summary.get("content_urls", {}).get("desktop", {}).get("page", f"{base}/wiki/{safe_title}")

        extended_text = ""
        if long_text:
            if long_text.startswith(extract):
                extended_text = long_text[len(extract):].strip()
            else:
                extended_text = long_text

        thumbnail = summary.get("thumbnail", {}).get("source", "")

        return {
            "title": title,
            "summary": extract,
            "extended_text": extended_text,
            "wikipedia_url": page_url,
            "thumbnail": thumbnail,
        }
