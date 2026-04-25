# AI Green Assistant

API FastAPI + UI web per:
- cercare specie vegetali simili partendo da una foto
- ottenere un riassunto da Wikipedia (in Markdown) con immagini

Il core di ricerca usa la classe `PlentClefIndex` in `plentclef.py` con embedding OpenCLIP + indice FAISS.

## Requisiti

- Python 3.10+
- Ambiente virtuale consigliato (`.venv`)
- File dati presenti in `data/`:
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
- UI test: `http://localhost:8000/`
- Swagger docs: `http://localhost:8000/docs`

## Configurazione (variabili ambiente)

Puoi sovrascrivere i percorsi/model senza modificare il codice:

- `PLANCLEF_INDEX_PATH` (default: `data/planclef.faiss`)
- `PLANCLEF_CACHE_PATH` (default: `data/planclef_cache.pt`)
- `PLANCLEF_MODEL_NAME` (default: `ViT-B-32`)
- `OPENAI_API_KEY` (obbligatoria per endpoint chat)
- `OPENAI_MODEL` (default: `gpt-4o-mini`)

Puoi impostarle anche in un file `.env` nella root del progetto (caricato automaticamente all'avvio):

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
PLANCLEF_INDEX_PATH=data/planclef.faiss
PLANCLEF_CACHE_PATH=data/planclef_cache.pt
PLANCLEF_MODEL_NAME=ViT-B-32
```

Esempio PowerShell:

```powershell
$env:PLANCLEF_MODEL_NAME = "ViT-B-32"
$env:PLANCLEF_INDEX_PATH = "data/planclef.faiss"
$env:PLANCLEF_CACHE_PATH = "data/planclef_cache.pt"
python -m uvicorn api:app --reload
```

## Endpoint API

### 1) Health

- Metodo: `GET`
- Path: `/health`

Risposta esempio:

```json
{
  "status": "ok",
  "model": "ViT-B-32"
}
```

### 2) Ricerca immagini simili

- Metodo: `POST`
- Path: `/search`
- Parametri query:
  - `k` (opzionale, default `5`, min `1`, max `50`)
- Body: `multipart/form-data` con campo `file` (immagine)

Esempio con curl:

```bash
curl -X POST "http://localhost:8000/search?k=5" \
  -F "file=@foto_pianta.jpg"
```

Risposta esempio:

```json
{
  "results": [
    {"species": "Rosa canina", "score": 0.9212},
    {"species": "Prunus spinosa", "score": 0.8731}
  ]
}
```

### 3) Info pianta da Wikipedia

- Metodo: `GET`
- Path: `/plant/{name}`
- Parametri query:
  - `lang` (opzionale, default `it`; es: `it`, `en`, `fr`)

Esempio:

```bash
curl "http://localhost:8000/plant/Rosa%20canina?lang=it"
```

Risposta esempio:

```json
{
  "title": "Rosa canina",
  "markdown": "# Rosa canina\n\n<img src=\"...\" .../>\n...",
  "wikipedia_url": "https://it.wikipedia.org/wiki/Rosa_canina"
}
```

## UI di test

La UI e servita da `GET /` e permette:
- tab Ricerca per immagine: upload + `k` + visualizzazione risultati
- tab Info pianta: ricerca nome + lingua Wikipedia + immagini + link fonte

## Note utili

- Al primo avvio il caricamento modello puo richiedere tempo.
- Se usi policy aziendali che bloccano `pip.exe`, usa sempre `python -m pip ...`.
- L endpoint `/plant/{name}` dipende da Wikipedia: errori rete/rate limit possono causare risposte 4xx/5xx.
- Se Windows App Control blocca DLL/PYD native (es. `torch`/`faiss`), l endpoint `/search` risponde con errore 503 finche le librerie non vengono consentite da policy.

## Struttura progetto

```text
ai-green-assistent/
  api.py
  plentclef.py
  ui.html
  requirements.txt
  data/
    planclef.faiss
    planclef_cache.pt
  Riconoscimento_specie.ipynb
```
