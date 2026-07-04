# Mishka Hub server

Local FastAPI backend: recommendations, TMDB metadata/posters, UK streaming
availability, and (later) Letterboxd sync.

## Setup

```bash
cd apps/server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # then paste your TMDB token into .env
```

## Run

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

- Health check: http://127.0.0.1:8000/api/health
- Interactive API docs: http://127.0.0.1:8000/docs
- TMDB smoke test: http://127.0.0.1:8000/api/tmdb/search?q=dune

## Layout

```
app/
  main.py            FastAPI app + CORS + lifespan
  config.py          settings from .env (MISHKA_* vars)
  clients/tmdb.py    TMDB async client (search, metadata, GB watch providers, posters)
  routers/           HTTP endpoints (health, tmdb; auth/films/recommend to come)
../data/             SQLite db (created later)
```
