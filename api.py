import json
import os
import tempfile
from pathlib import Path
from typing import Any

import chromadb
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from openai import OpenAI
from pydantic import BaseModel, Field

load_dotenv()

INDEX_PATH = os.getenv("PLANCLEF_INDEX_PATH", "data/planclef.faiss")
CACHE_PATH = os.getenv("PLANCLEF_CACHE_PATH", "data/planclef_cache.pt")
MODEL_NAME = os.getenv("PLANCLEF_MODEL_NAME", "ViT-B-32")
RAG_DB_PATH = os.getenv("RAG_DB_PATH", "data/plant_rag")
WIKI_USER_AGENT = os.getenv(
    "WIKI_USER_AGENT",
    "ai-green-assistant/1.0 (contact: local-dev)",
)
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

index: Any = None
rag_collection: Any = None


def get_rag_collection():
    """Get or initialize the ChromaDB collection for plant RAG."""
    global rag_collection
    if rag_collection is None:
        try:
            client = chromadb.PersistentClient(path=RAG_DB_PATH)
            rag_collection = client.get_collection(
                name="plants",
            )
        except Exception as e:
            raise RuntimeError(f"Impossibile caricare il database RAG delle piante: {e}")
    return rag_collection

app = FastAPI(title="PlantCLEF Image Search API")


def get_search_backend_status():
    checks: dict[str, str] = {}
    for module_name in ("torch", "faiss", "open_clip"):
        try:
            __import__(module_name)
            checks[module_name] = "ok"
        except Exception as e:
            checks[module_name] = f"{type(e).__name__}: {e}"

    files = {
        "index_exists": os.path.exists(INDEX_PATH),
        "cache_exists": os.path.exists(CACHE_PATH),
        "index_path": INDEX_PATH,
        "cache_path": CACHE_PATH,
    }

    native_ok = all(value == "ok" for value in checks.values())
    ready = native_ok and files["index_exists"] and files["cache_exists"]
    return {"ready": ready, "modules": checks, "files": files}


class PlantChatRequest(BaseModel):
    plant_name: str = Field(..., min_length=2, description="Nome comune o scientifico della pianta")
    question: str = Field(..., min_length=3, description="Domanda sulla cura della pianta")
    lang: str = Field("it", description="Lingua Wikipedia da usare per il contesto")


def fetch_wikipedia_text_context(name: str, lang: str):
    base = f"https://{lang}.wikipedia.org"
    wiki_headers = {
        "User-Agent": WIKI_USER_AGENT,
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
        titles = []
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

    return {
        "title": title,
        "summary": extract,
        "extended_text": extended_text,
        "wikipedia_url": page_url,
    }


def get_index():
    global index
    if index is None:
        try:
            from plentclef import PlentClefIndex

            index = PlentClefIndex(
                model_name=MODEL_NAME,
                index_path=INDEX_PATH,
                index_cache=CACHE_PATH,
            )
        except Exception as e:
            cause = f"{type(e).__name__}: {e}"
            raise RuntimeError(
                "Impossibile inizializzare il motore di ricerca immagini. "
                "Probabile blocco di sicurezza su librerie native (es. torch/faiss). "
                f"Dettaglio: {cause}."
            ) from e
    return index


@app.post("/search")
async def search_similar(
    file: UploadFile = File(..., description="Immagine della pianta da ricercare"),
    k: int = Query(default=5, ge=1, le=50, description="Numero di risultati da restituire"),
):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Il file caricato non è un'immagine valida.")

    suffix = os.path.splitext(file.filename or "")[1] or ".jpg"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        loaded_index = get_index()
        results = loaded_index.search(tmp_path, loaded_index.plantclef_labels, k=k)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    return JSONResponse(
        content={
            "results": [
                {"species": species, "score": float(score)}
                for species, score, _ in results
            ]
        }
    )


@app.get("/health")
def health():
    status = get_search_backend_status()
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "search_backend_ready": status["ready"],
    }


@app.get("/search/status")
def search_status():
    return get_search_backend_status()


@app.get("/", response_class=HTMLResponse)
def ui():
    with open(os.path.join(os.path.dirname(__file__), "ui.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/images/{full_path:path}")
def get_image(full_path: str):
    """Serve local plant images from the RAG data directory."""
    try:
        file_path = Path("data") / full_path
        file_path = file_path.resolve()
        
        # Security check: ensure the path is within data/images
        data_images_path = (Path("data") / "images").resolve()
        if not str(file_path).startswith(str(data_images_path)):
            raise HTTPException(status_code=403, detail="Accesso negato.")
        
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Immagine non trovata.")
        
        return FileResponse(file_path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore nel caricamento immagine: {e}")


@app.get("/plant/{name}")
def plant_info(
    name: str,
    lang: str = Query(default="it", description="Codice lingua Wikipedia (es. it, en, fr)"),
):
    """Recupera informazioni su una pianta dalla RAG con riassunto OpenAI."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY non configurata. Imposta la variabile ambiente e riprova.",
        )

    try:
        # Query the RAG collection to find documents matching the plant name
        collection = get_rag_collection()
        
        # Search for documents where species_name matches
        results = collection.get(
            where={"species_name": {"$eq": name}},
            limit=20,  # Get multiple chunks for better context
        )
        
        if not results or not results.get("documents"):
            # Plant not found in RAG, try fallback to Wikipedia
            try:
                wiki_data = fetch_wikipedia_text_context(name, lang)
                title = wiki_data["title"]
                extract = wiki_data["summary"]
                common_name = ""
                image_paths = []
                rag_used = False
            except Exception:
                raise HTTPException(
                    status_code=404, 
                    detail=f"Pianta '{name}' non trovata nella RAG o in Wikipedia."
                )
        else:
            rag_used = True
            # Extract metadata from the first result
            metadatas = results.get("metadatas", [])
            first_meta = metadatas[0] if metadatas else {}
            
            title = first_meta.get("species_name", name)
            common_name = first_meta.get("common_name", "")
            image_paths_json = first_meta.get("image_paths", "[]")
            
            try:
                image_paths = json.loads(image_paths_json)
            except (json.JSONDecodeError, TypeError):
                image_paths = []
            
            # Combine chunks for OpenAI context (up to 6000 chars)
            documents = results.get("documents", [])
            combined_text = "\n\n".join(documents[:10])  # Use up to 10 chunks
            if len(combined_text) > 6000:
                combined_text = combined_text[:6000] + "\n..."
            
            # Generate summary using OpenAI
            try:
                client = OpenAI(api_key=api_key)
                completion = client.chat.completions.create(
                    model=OPENAI_MODEL,
                    temperature=0.3,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Sei un botanico esperto. Genera un riassunto conciso e affascinante "
                                "della pianta in base al testo fornito. Includi: descrizione, habitat, "
                                "caratteristiche distintive e usi. Rispondi in italiano."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"Crea un riassunto affascinante della pianta '{title}'.\n\n"
                                f"Testo di riferimento:\n{combined_text}"
                            ),
                        },
                    ],
                )
                extract = completion.choices[0].message.content or ""
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"Errore nella generazione del riassunto: {e}")
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore nel recupero informazioni pianta: {e}")

    # Build image URLs/paths
    images: list[str] = []
    base_url = ""
    data_dir = Path("data")
    
    for img_path in image_paths[:3]:  # Show up to 3 images
        # Try as local file first
        local_path = data_dir / img_path
        if local_path.exists():
            # Convert to URL path for serving
            images.append(f"/images/{img_path.replace(chr(92), '/')}")
        else:
            # Could be a URL
            if img_path.startswith("http"):
                images.append(img_path)

    # Build markdown response
    md_lines = [f"# {title}\n"]
    
    if common_name:
        md_lines.append(f"**Nome comune:** {common_name}\n")
    
    if images:
        img_tags = "".join(
            f'<img src="{url}" alt="{title}" width="280" style="margin:4px;border-radius:8px"/>'
            for url in images
        )
        md_lines.append(img_tags + "\n")

    md_lines.append(extract + "\n")
    
    source_info = "Fonte: Database RAG" if rag_used else "Fonte: Wikipedia"
    md_lines.append(f"\n---\n{source_info}")
    
    markdown = "\n".join(md_lines)

    return JSONResponse(
        content={
            "title": title,
            "common_name": common_name,
            "markdown": markdown,
            "summary": extract,
            "images": images,
            "source": "rag" if rag_used else "wikipedia",
        }
    )


@app.post("/chat/plant-care")
def plant_care_chat(payload: PlantChatRequest):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY non configurata. Imposta la variabile ambiente e riprova.",
        )

    try:
        # Try to get context from RAG first
        collection = get_rag_collection()
        results = collection.get(
            where={"species_name": {"$eq": payload.plant_name}},
            limit=15,  # Get multiple chunks for comprehensive context
        )
        
        if results and results.get("documents"):
            # Use RAG context
            documents = results.get("documents", [])
            context_text = "\n\n".join(documents)
            if len(context_text) > 8000:
                context_text = context_text[:8000] + "\n..."
            
            metadatas = results.get("metadatas", [])
            plant_title = metadatas[0].get("species_name", payload.plant_name) if metadatas else payload.plant_name
            common_name = metadatas[0].get("common_name", "") if metadatas else ""
            source_info = "RAG"
            source_url = ""
        else:
            # Fallback to Wikipedia if not found in RAG
            wiki_data = fetch_wikipedia_text_context(payload.plant_name, payload.lang)
            context_text = (wiki_data.get("summary", "") + "\n\n" + wiki_data.get("extended_text", "")).strip()
            if len(context_text) > 8000:
                context_text = context_text[:8000] + "\n..."
            plant_title = wiki_data["title"]
            common_name = ""
            source_info = "Wikipedia"
            source_url = wiki_data.get("wikipedia_url", "")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"Errore nel recupero contesto pianta: {e}")

    try:
        client = OpenAI(api_key=api_key)
        
        # Build user message with plant info
        user_message = f"Pianta: {plant_title}"
        if common_name:
            user_message += f" ({common_name})"
        user_message += f"\nDomanda: {payload.question}\n\n"
        user_message += f"Contesto dalla base di dati:\n{context_text}\n\n"
        user_message += (
            "Rispondi con:\n"
            "1) Risposta breve\n"
            "2) Cosa fare oggi\n"
            "3) Errori da evitare"
        )
        
        completion = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0.3,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Sei un assistente botanico pratico e chiaro. "
                        "Rispondi in italiano con consigli concreti per la cura della pianta "
                        "(irrigazione, luce, terreno, potatura, parassiti, stagionalita). "
                        "Se l'informazione non e certa, dichiaralo esplicitamente. "
                        "Non dare indicazioni mediche per persone o animali."
                    ),
                },
                {
                    "role": "user",
                    "content": user_message,
                },
            ],
        )
        answer = completion.choices[0].message.content or ""
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Errore chiamata OpenAI: {e}")

    return JSONResponse(
        content={
            "plant": plant_title,
            "common_name": common_name,
            "question": payload.question,
            "answer": answer.strip(),
            "source": source_info,
            "source_url": source_url,
            "model": OPENAI_MODEL,
        }
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=False)
