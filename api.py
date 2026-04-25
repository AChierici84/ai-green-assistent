import os
import tempfile
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from openai import OpenAI
from pydantic import BaseModel, Field

load_dotenv()

INDEX_PATH = os.getenv("PLANCLEF_INDEX_PATH", "data/planclef.faiss")
CACHE_PATH = os.getenv("PLANCLEF_CACHE_PATH", "data/planclef_cache.pt")
MODEL_NAME = os.getenv("PLANCLEF_MODEL_NAME", "ViT-B-32")
WIKI_USER_AGENT = os.getenv(
    "WIKI_USER_AGENT",
    "ai-green-assistant/1.0 (contact: local-dev)",
)
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

index: Any = None

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


@app.get("/plant/{name}")
def plant_info(
    name: str,
    lang: str = Query(default="it", description="Codice lingua Wikipedia (es. it, en, fr)"),
):
    try:
        wiki_data = fetch_wikipedia_text_context(name, lang)
        base = f"https://{lang}.wikipedia.org"
        safe_title = wiki_data["title"].replace(" ", "_")
        wiki_headers = {
            "User-Agent": WIKI_USER_AGENT,
            "Accept": "application/json",
        }

        images: list[str] = []
        with httpx.Client(timeout=10.0, headers=wiki_headers, follow_redirects=True) as client:
            media_resp = client.get(f"{base}/api/rest_v1/page/media-list/{safe_title}")
            if media_resp.status_code == 200:
                media_data = media_resp.json()
                for item in media_data.get("items", []):
                    if item.get("type") == "image":
                        src = item.get("srcset", [{}])[-1].get("src") or item.get("src", "")
                        if src:
                            if src.startswith("//"):
                                src = "https:" + src
                            images.append(src)
                    if len(images) >= 3:
                        break

            summary_resp = client.get(f"{base}/api/rest_v1/page/summary/{safe_title}")
            if summary_resp.status_code == 200:
                summary = summary_resp.json()
            else:
                summary = {}
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(e))

    title = wiki_data["title"]
    extract = wiki_data["summary"]
    extended_text = wiki_data["extended_text"]
    page_url = wiki_data["wikipedia_url"]

    thumbnail = summary.get("thumbnail", {}).get("source", "")
    if thumbnail and thumbnail not in images:
        images.insert(0, thumbnail)
        images = images[:3]

    md_lines = [f"# {title}\n"]
    if images:
        img_tags = "".join(
            f'<img src="{url}" alt="{title}" width="280" style="margin:4px"/>'
            for url in images
        )
        md_lines.append(img_tags + "\n")

    md_lines.append(extract + "\n")
    md_lines.append(f"\n---\n**Fonte:** [Wikipedia — {title}]({page_url})")
    markdown = "\n".join(md_lines)

    return JSONResponse(
        content={
            "title": title,
            "markdown": markdown,
            "summary": extract,
            "extended_text": extended_text,
            "wikipedia_url": page_url,
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
        wiki_data = fetch_wikipedia_text_context(payload.plant_name, payload.lang)
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"Errore nel recupero contesto Wikipedia: {e}")

    context_text = (wiki_data.get("summary", "") + "\n\n" + wiki_data.get("extended_text", "")).strip()
    if len(context_text) > 8000:
        context_text = context_text[:8000] + "\n..."

    try:
        client = OpenAI(api_key=api_key)
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
                    "content": (
                        f"Pianta: {wiki_data['title']}\n"
                        f"Domanda: {payload.question}\n\n"
                        "Contesto Wikipedia:\n"
                        f"{context_text}\n\n"
                        "Rispondi con:\n"
                        "1) Risposta breve\n"
                        "2) Cosa fare oggi\n"
                        "3) Errori da evitare"
                    ),
                },
            ],
        )
        answer = completion.choices[0].message.content or ""
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Errore chiamata OpenAI: {e}")

    return JSONResponse(
        content={
            "plant": wiki_data["title"],
            "question": payload.question,
            "answer": answer.strip(),
            "source": wiki_data["wikipedia_url"],
            "model": OPENAI_MODEL,
        }
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=False)
