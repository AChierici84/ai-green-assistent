# AI Green Assistant

API FastAPI + UI web per:
- ricerca specie vegetali simili da immagine (OpenCLIP + FAISS)
- schede pianta con riassunto AI basato su knowledge base RAG
- chatbot di cura botanica con contesto da RAG (fallback Wikipedia)

## Requisiti

- Python 3.10+
- Ambiente virtuale consigliato (`.venv`)
- Dati PlantCLEF in `data/`:
  - `planclef.faiss`
  - `planclef_cache.pt`

## Installazione

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Avvio server

```powershell
python -m uvicorn api:app --reload
```

Server locale:
- API base: `http://localhost:8000`
- UI: `http://localhost:8000/`
- Swagger: `http://localhost:8000/docs`

## Frontend React PWA

E disponibile una PWA React in `pwa-app/` con flusso completo:
- upload immagine e riconoscimento specie (`/search`)
- apertura scheda pianta con estrazioni (`/plant/{name}` + `/plant/{name}/profile`)
- domanda sulla cura (`/chat/plant-care`)

Avvio in sviluppo:

```powershell
cd pwa-app
npm install
npm run dev
```

App locale:
- PWA: `http://localhost:5173`

Configurazione endpoint API (opzionale):
- variabile `VITE_API_BASE` (default `http://localhost:8000`)

Per produzione:

```powershell
cd pwa-app
npm run build
npm run preview
```

## Build della knowledge base RAG (opzionale ma consigliato)

Per costruire/aggiornare il database piante locale (ChromaDB + immagini):

```powershell
python build_plant_rag.py
```

Per recuperare solo le specie non indicizzate (`indexed=0` nel DB SQLite), cercare Wikipedia in piu lingue e tradurre in italiano prima dell'upsert nella RAG:

```powershell
python build_plant_rag.py --from-sqlite-indexed-zero --langs it,en,fr,es,de,pt --translate-non-italian
```

Opzioni utili per il build RAG:

```powershell
# usa un DB SQLite specifico per leggere le specie indexed=0
python build_plant_rag.py --from-sqlite-indexed-zero --sqlite-path data/plants.db

# disattiva traduzione (mantiene il testo nella lingua trovata)
python build_plant_rag.py --no-translate-non-italian

# cambia modello OpenAI per traduzione
python build_plant_rag.py --translation-model gpt-4o-mini
```

Output principali:
- `data/plant_rag/` (database vettoriale persistente)
- `data/images/<specie>/` (immagini scaricate)
- `data/rag_progress.json` (resume del processo)

## Configurazione (variabili ambiente)

Puoi impostare le variabili in `.env` (caricato automaticamente) o via shell.

- `PLANCLEF_INDEX_PATH` (default: `data/planclef.faiss`)
- `PLANCLEF_CACHE_PATH` (default: `data/planclef_cache.pt`)
- `PLANCLEF_MODEL_NAME` (default: `ViT-B-32`)
- `RAG_DB_PATH` (default: `data/plant_rag`)
- `PLANTS_SQLITE_PATH` (default: `data/plants.db`)
- `WIKI_USER_AGENT` (default: `ai-green-assistant/1.0 (contact: local-dev)`)
- `OPENAI_API_KEY` (obbligatoria per `/plant/{name}` e `/chat/plant-care`)
- `OPENAI_MODEL` (default: `gpt-4o-mini`)

Esempio `.env`:

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
PLANCLEF_INDEX_PATH=data/planclef.faiss
PLANCLEF_CACHE_PATH=data/planclef_cache.pt
PLANCLEF_MODEL_NAME=ViT-B-32
RAG_DB_PATH=data/plant_rag
PLANTS_SQLITE_PATH=data/plants.db
WIKI_USER_AGENT=ai-green-assistant/1.0 (contact: local-dev)
```

## Build database SQLite piante

Per creare un database SQLite con tabella `plants`, campo `indexed` (0/1) e campi di cura estratti da RAG + OpenAI:

```powershell
python build_plants_sqlite.py
```

Opzioni utili:

```powershell
# solo prime 20 specie (test rapido)
python build_plants_sqlite.py --limit 20

# ricalcola anche specie gia arricchite
python build_plants_sqlite.py --force-refresh

# disattiva fallback OpenAI generico per campi mancanti
python build_plants_sqlite.py --no-generic-fallback

# disattiva integrazione fonti esterne (RHS, Missouri, EPPO)
python build_plants_sqlite.py --no-external-sources
```

Campi valorizzati quando `indexed=1`:
- `annaffiatura_gg`
- `annaffiatura_time`
- `luce`
- `temperatura`
- `umidita`
- `altezza_media`
- `pulizia`
- `terriccio`
- `concimazione`
- `prevenzione`

Se `OPENAI_API_KEY` non e impostata, lo script compila comunque `indexed` e lascia i campi descrittivi a `NULL`.

Con `--generic-fallback` (attivo di default), se alcuni campi restano `NULL` dopo estrazione da RAG,
lo script esegue una seconda chiamata OpenAI basata su conoscenza generale botanica per tentare di completarli.

Con `--external-sources` (attivo di default), lo script prova anche a integrare evidenze da:
- RHS (cura pratica)
- Missouri Botanical Garden (cura pratica)
- EPPO (prevenzione fitosanitaria)

Infine usa OpenAI come normalizzatore finale dei dati aggregati (RAG + fonti esterne) verso il JSON strutturato del DB.

## Endpoint API

### 1) Health

- Metodo: `GET`
- Path: `/health`

Esempio risposta:

```json
{
  "status": "ok",
  "model": "ViT-B-32",
  "search_backend_ready": true
}
```

### 2) Stato backend ricerca immagine

- Metodo: `GET`
- Path: `/search/status`

Restituisce diagnostica modulo/file (`torch`, `faiss`, `open_clip`, presenza index/cache).

### 3) Ricerca immagini simili

- Metodo: `POST`
- Path: `/search`
- Query:
  - `k` (default `5`, min `1`, max `50`)
- Body: `multipart/form-data` con `file=<immagine>`

Esempio:

```bash
curl -X POST "http://localhost:8000/search?k=5" -F "file=@foto_pianta.jpg"
```

Esempio risposta:

```json
{
  "results": [
    {"species": "Rosa canina", "score": 0.9212},
    {"species": "Prunus spinosa", "score": 0.8731}
  ]
}
```

### 4) Scheda pianta (RAG + OpenAI, fallback Wikipedia)

- Metodo: `GET`
- Path: `/plant/{name}`
- Query:
  - `lang` (default `it`, usata nel fallback Wikipedia)

Esempio:

```bash
curl "http://localhost:8000/plant/Rosa%20canina?lang=it"
```

Esempio risposta:

```json
{
  "title": "Rosa canina",
  "common_name": "Rosa canina",
  "markdown": "# Rosa canina\n...",
  "summary": "...",
  "images": ["/images/images/rosa_canina/xxx.jpg"],
  "source": "rag"
}
```

### 5) Profilo strutturato da plants.db

- Metodo: `GET`
- Path: `/plant/{name}/profile`

Restituisce i campi salvati nel database SQLite `plants.db` per la specie richiesta.

Esempio:

```bash
curl "http://localhost:8000/plant/Rosa%20canina/profile"
```

Esempio risposta:

```json
{
  "species_name": "Rosa canina",
  "indexed": true,
  "annaffiatura_gg": 4,
  "annaffiatura_time": "mattino",
  "luce": "piena luce",
  "temperatura": "temperata",
  "umidita": "media",
  "altezza_media": "2-3 m",
  "pulizia": "rimuovere foglie secche",
  "terriccio": "ben drenato",
  "concimazione": "primavera",
  "prevenzione": "controllare afidi e oidio",
  "updated_at": "2026-04-29T10:03:16.214041+00:00"
}
```

### 6) Chatbot cura pianta

- Metodo: `POST`
- Path: `/chat/plant-care`
- Body JSON:

```json
{
  "plant_name": "Rosa canina",
  "question": "Ogni quanto devo annaffiarla in primavera?",
  "lang": "it"
}
```

Esempio risposta:

```json
{
  "plant": "Rosa canina",
  "common_name": "",
  "question": "Ogni quanto devo annaffiarla in primavera?",
  "answer": "...",
  "source": "RAG",
  "source_url": "",
  "model": "gpt-4o-mini"
}
```

### 6) Servizio immagini locali

- Metodo: `GET`
- Path: `/images/{full_path}`

Serve le immagini presenti sotto `data/images`.

## UI

La UI servita da `/` include tre tab:
- Ricerca per immagine
- Info su una pianta
- Chatbot cura

## Note operative

- Al primo avvio il caricamento del modello puo richiedere tempo.
- Se policy aziendali bloccano `pip.exe`, usa `python -m pip ...`.
- Se Windows App Control blocca librerie native (`torch`, `faiss`), `/search` puo rispondere `503`.
- Gli endpoint che usano OpenAI richiedono `OPENAI_API_KEY` valida.

## Struttura progetto (sintesi)

```text
ai-green-assistent/
  api.py
  build_plant_rag.py
  plentclef.py
  ui.html
  requirements.txt
  unique_species_labels.csv
  data/
    planclef.faiss
    planclef_cache.pt
    plant_rag/
    images/
```
