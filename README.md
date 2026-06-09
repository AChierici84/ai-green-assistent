---
title: Clorofilla
sdk: docker
app_port: 7860
colorFrom: green
colorTo: blue
pinned: false
---

# Clorofilla

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

## Deploy su Hugging Face Spaces (Docker)

Questa repo e pronta per Spaces in modalita Docker:
- file `Dockerfile` alla root
- API FastAPI + frontend PWA serviti dallo stesso container
- porta esposta: `7860`

Passi:

1. Crea un nuovo Space su Hugging Face con SDK `Docker`.
2. Collega/pusha questa repository nello Space.
3. In `Settings -> Secrets` aggiungi almeno:
  - `OPENAI_API_KEY`
  - `GOOGLE_CLIENT_ID` (se vuoi login Google)
4. Opzionali in `Variables/Secrets`:
  - `OPENAI_MODEL`
  - `REQUIRE_GOOGLE_AUTH` (consigliato `1` in produzione)
  - `RAG_DB_PATH` (default `data/plant_rag`)
  - `MYSQL_USER`, `MYSQL_DATABASE`, `DB_PASSWORD`
  - `MYSQL_USE_UNIX_SOCKET` + `MYSQL_UNIX_SOCKET` (se usi socket)
5. Avvia il build dello Space.

Note operative:
- In Spaces il frontend viene compilato nel Docker build e servito dalla API.
- Se usi Google login, configura gli URI autorizzati nel progetto Google OAuth con il dominio dello Space (https://<user>-<space>.hf.space).
- Per usare ricerca immagini, tutti gli artifact FAISS/cache e il database ChromaDB vengono scaricati automaticamente dal dataset Hugging Face `AChierici84/GreenAssistent-assets` se non sono gia presenti nei path configurati.

## Frontend React PWA

E disponibile una PWA React in `pwa-app/` con flusso completo:
- upload immagine e riconoscimento specie (`/search`)
- apertura scheda pianta con estrazioni (`/plant/{name}` + `/plant/{name}/profile`)
- domanda sulla cura (`/chat/plant-care`)
- salvataggio "Le tue piante" con nome personalizzato (`/user/plants`)

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
- variabile `VITE_GOOGLE_CLIENT_ID` (obbligatoria per login Google nella PWA)

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
- `image_paths` nel RAG (URL immagini Wikipedia salvati nei metadati)
- `data/rag_progress.json` (resume del processo)

Migrazione una tantum da path locali a URL remoti:

```bash
python migrate_rag_image_paths_to_urls.py --dry-run --limit 10
python migrate_rag_image_paths_to_urls.py
```

## Configurazione (variabili ambiente)

Puoi impostare le variabili in `.env` (caricato automaticamente) o via shell.

- `PLANCLEF_INDEX_PATH` (default: `data/planclef.faiss`)
- `PLANCLEF_CACHE_PATH` (default: `data/planclef_cache.pt`)
- `PLANCLEF_MODEL_NAME` (default: `ViT-B-32`)
- `LEAFSNAP_INDEX_PATH` (default: `data/leafsnap.faiss`, oppure `/data/greenassistent-assets/...` su Spaces con persistent storage)
- `LEAFSNAP_CACHE_PATH` (default: `data/leafsnap_cache.pt`, oppure `/data/greenassistent-assets/...` su Spaces con persistent storage)
- `HF_ASSETS_DATASET_REPO` (default: `AChierici84/GreenAssistent-assets`)
- `RAG_DB_PATH` (default: `data/plant_rag`)
- `WIKI_USER_AGENT` (default: `clorofilla/1.0 (contact: local-dev)`)
- `OPENAI_API_KEY` (obbligatoria per `/chat/plant-care`; opzionale per `/plant/{name}`)
- `OPENAI_MODEL` (default: `gpt-4o-mini`)
- `OPENAI_VISION_MODEL` (default: `gpt-4o`, usato per fallback visivo su `/search`)
- `PWA_DIST_DIR` (default: `pwa-app/dist`)
- `PLANT_CARD_CACHE_ENABLED` (default: `1`)
- `FAISS_CONFIDENCE_THRESHOLD` (default: `0.82`)
- `FAISS_AMBIGUITY_MARGIN` (default: `0.015`)
- `RRF_AMBIGUITY_MARGIN` (default: `0.0025`)
- `FORCE_OPENAI_FALLBACK` (default: `0`)
- `MYSQL_ENABLED` (opzionale; se `1/true/yes/on` forza l'attivazione MySQL)
- `MYSQL_USER` (richiesta per la connessione MySQL)
- `MYSQL_DATABASE` (richiesta per la connessione MySQL)
- `MYSQL_PASSWORD` oppure `DB_PASSWORD` (password raw, senza URL encoding)
- `MYSQL_USE_UNIX_SOCKET` (default: `0`; se `1/true/yes/on` usa socket Unix)
- `MYSQL_UNIX_SOCKET` (obbligatoria se `MYSQL_USE_UNIX_SOCKET=1`)
- `MYSQL_HOST` (default: `localhost`, usata se non si usa socket Unix)
- `MYSQL_PORT` (default: `3306`, usata se non si usa socket Unix)
- `GOOGLE_CLIENT_ID` (uno o piu client id Google OAuth separati da virgola)
- `REQUIRE_GOOGLE_AUTH` (default: `0`; se `1/true/yes/on` richiede Bearer Google token)
- `ADMIN_USERS` (email admin separate da virgola per endpoint amministrativi)
- `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` (fallback upload foto utente)
- `LOG_LEVEL` (default: `INFO`)
- `LOG_DIR` (default: `logs`)
- `LOG_FILE` (default: `api.log`)

Esempio `.env`:

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
PLANCLEF_INDEX_PATH=data/planclef.faiss
PLANCLEF_CACHE_PATH=data/planclef_cache.pt
PLANCLEF_MODEL_NAME=ViT-B-32
RAG_DB_PATH=data/plant_rag
WIKI_USER_AGENT=clorofilla/1.0 (contact: local-dev)
GOOGLE_CLIENT_ID=xxxxxxxxxxxx-abcdefg.apps.googleusercontent.com
REQUIRE_GOOGLE_AUTH=0
# MySQL a variabili separate (consigliato su Plesk)
MYSQL_USER=clorousr_
MYSQL_DATABASE=clorodb_
DB_PASSWORD=change_me
MYSQL_USE_UNIX_SOCKET=1
MYSQL_UNIX_SOCKET=/var/lib/mysql/mysql.sock

# Admin console (opzionale)
ADMIN_USERS=admin@example.com

# Logging (opzionale)
LOG_LEVEL=INFO
LOG_DIR=logs
LOG_FILE=api.log

# Ricerca/fallback GPT (opzionale)
FAISS_CONFIDENCE_THRESHOLD=0.82
FAISS_AMBIGUITY_MARGIN=0.015
RRF_AMBIGUITY_MARGIN=0.0025
FORCE_OPENAI_FALLBACK=0
OPENAI_VISION_MODEL=gpt-4o

# Cache schede e percorso build PWA (opzionale)
PLANT_CARD_CACHE_ENABLED=1
PWA_DIST_DIR=pwa-app/dist

# Se MYSQL_USE_UNIX_SOCKET=0 usa TCP (default host/port)
# MYSQL_HOST=127.0.0.1
# MYSQL_PORT=3306
```

Configurazione MySQL nel backend (post-refactor):
- Connessione gestita tramite variabili `MYSQL_*`.
- Se `MYSQL_ENABLED` non e impostata, la presenza di `MYSQL_USER` + `MYSQL_DATABASE` abilita automaticamente MySQL.
- Con `MYSQL_ENABLED=1`, MySQL diventa obbligatoria e il backend risponde `503` se la configurazione e incompleta.

Variabili `.env` lato PWA (`pwa-app/.env`):

```env
VITE_API_BASE=http://localhost:8000
VITE_GOOGLE_CLIENT_ID=xxxxxxxxxxxx-abcdefg.apps.googleusercontent.com
```

Monitoraggio errori con Sentry (opzionale):
- Dove vedi gli errori: dashboard web su `https://sentry.io` nel progetto creato.
- Alert: configurabili da Sentry su email/Slack/Teams.

Variabili backend (`.env`):

```env
SENTRY_DSN=https://<public_key>@o<org_id>.ingest.sentry.io/<project_id>
SENTRY_ENVIRONMENT=production
SENTRY_RELEASE=clorofilla-backend@1.0.0
SENTRY_TRACES_SAMPLE_RATE=0.05
SENTRY_PROFILES_SAMPLE_RATE=0.0
```

Variabili frontend PWA (`pwa-app/.env`):

```env
VITE_SENTRY_DSN=https://<public_key>@o<org_id>.ingest.sentry.io/<project_id>
VITE_SENTRY_ENVIRONMENT=production
VITE_SENTRY_RELEASE=clorofilla-pwa@1.0.0
VITE_SENTRY_TRACES_SAMPLE_RATE=0.05
```

Note:
- Se `SENTRY_DSN`/`VITE_SENTRY_DSN` sono vuote, l'integrazione resta disattivata.
- Consigliato usare due progetti separati in Sentry: uno per backend e uno per frontend.
- Evita di inviare dati sensibili nei breadcrumb o nei payload applicativi.

Nota autenticazione:
- endpoint login: `POST /auth/google` (valida l'id_token Google)
- con `REQUIRE_GOOGLE_AUTH=0` i token Bearer sono opzionali
- con `REQUIRE_GOOGLE_AUTH=1` gli endpoint principali (`/search`, `/plant/*`, `/chat/plant-care`, `/species/*`) richiedono login
- endpoint sempre autenticati: `POST /user/plants`, `GET /user/plants`

## Build database SQLite piante

Nota: questa pipeline genera/arricchisce un DB SQLite locale utile per ingest e manutenzione dati.
La API runtime post-refactor usa connessione MySQL configurata via variabili `MYSQL_*`.

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
La API e stata rifattorizzata in servizi separati (`app_config.py`, `data_storage.py`, `google_auth.py`, `species.py`) mantenendo `api.py` come orchestratore dei route handler.

### Endpoint principali

- `GET /health` - stato servizio + disponibilita backend ricerca
- `GET /search/status` - diagnostica moduli (`torch/faiss/open_clip`) e file indice/cache
- `GET /app-config` - configurazione pubblica frontend (`google_client_id`, `require_google_auth`)
- `POST /search` - riconoscimento immagine con fallback GPT vision quando il match FAISS e ambiguo
- `GET /plant/{name}` - scheda pianta da RAG, fallback Wikipedia, fallback bozza DB
- `GET /plant/{name}/profile` - profilo strutturato dal DB (`plants`)
- `POST /chat/plant-care` - risposta OpenAI con contesto RAG/Wikipedia + profilo DB
- `GET /species/previews` - URL preview immagini per specie
- `GET /species/common-names` - nomi comuni da metadati RAG
- `GET /species/{name}/build-status` - stato build asincrona specie bozza
- `GET /images/{full_path}` - serving immagini locali sotto `data/images`

### Endpoint autenticazione e utente

- `POST /auth/google` - valida token Google e registra utente se nuovo
- `POST /user/plants` - salva una pianta utente (auth richiesta)
- `GET /user/plants` - elenco piante utente (auth richiesta)
- `DELETE /user/plants/{plant_id}` - elimina pianta utente (auth richiesta)
- `PATCH /user/plants/{plant_id}/first-watering-date` - aggiorna prima annaffiatura (auth richiesta)
- `POST /user/plants/{plant_id}/photo` - upload foto su Google Drive utente con fallback Cloudinary (auth richiesta)
- `POST /recognitions/log` - log riconoscimento (guest o utente autenticato)

Per salvare le foto nel Drive del singolo utente, il frontend invia anche l'header `X-Google-Access-Token`
ottenuto con scope OAuth `https://www.googleapis.com/auth/drive.file`. Se manca o fallisce l'upload Drive,
il backend tenta automaticamente Cloudinary (se configurato).

### Endpoint amministrativi

- `GET /admin/console` - dashboard admin con statistiche utenti, riconoscimenti e inventario
- `POST /admin/species/build` - avvia build/rebuild asincrona per una specie (auth admin richiesta)
- `GET /debug/routes` - elenco route registrate

### Endpoint PWA/static

- `GET /` - landing page classica (`ui.html`)
- `GET /app` e `GET /app/` - PWA React buildata (`pwa-app/dist/index.html`)
- `GET /sw.js`, `GET /registerSW.js`, `GET /manifest.webmanifest`, `GET /favicon.ico`

Esempio risposta aggiornata di `POST /search`:

```json
{
  "results": [
    {
      "species": "Rosa canina",
      "score": 0.9212,
      "is_draft": false,
      "build_status": {
        "species": "Rosa canina",
        "status": "completed"
      }
    }
  ],
  "gpt_fallback_used": false,
  "recognition_ms": 842
}
```

`GET /plant/{name}` usa cache tabella `plant_cards_cache` (se `PLANT_CARD_CACHE_ENABLED=1`) e include `build_status` nella risposta.

## UI

- `GET /` serve la landing classica (`ui.html`).
- La UI principale e la PWA React servita su `GET /app`.
- In sviluppo frontend separato: `cd pwa-app ; npm run dev` su `http://localhost:5173`.

## Note operative

- Al primo avvio il caricamento del modello puo richiedere tempo.
- Se policy aziendali bloccano `pip.exe`, usa `python -m pip ...`.
- Se Windows App Control blocca librerie native (`torch`, `faiss`), `/search` puo rispondere `503`.
- `/chat/plant-care` richiede `OPENAI_API_KEY` valida.
- `/plant/{name}` funziona anche senza key OpenAI (riassunto locale ridotto), ma con qualita inferiore.

## Struttura progetto (post-refactor)

```text
ai-green-assistent/
  api.py
  app_config.py
  api_logging.py
  data_storage.py
  db_config.py
  google_auth.py
  species.py
  openai_gpt.py
  build_plant_rag.py
  plentclef.py
  ui.html
  pwa-app/
  requirements.txt
  unique_species_labels.csv
  data/
    planclef.faiss
    planclef_cache.pt
    plant_rag/
    images/
```
