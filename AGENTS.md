# AGENTS.md

## Cursor Cloud specific instructions

### Product overview

Single-service **Flask REST API** (`licencias-bot`) for Sniper Pro license management. No frontend, Docker, Makefile, or automated test/lint configuration in this repo.

### Services

| Service | Required | Notes |
|---------|----------|-------|
| PostgreSQL | Yes | Persists license data; tables auto-created on app startup via `db.create_all()` |
| Flask API (`app.py`) | Yes | Dev entry point; listens on `0.0.0.0:$PORT` (default **5000**) |
| AWS S3 | No | Only needed for `POST /generar_descarga` presigned URLs |

### One-time VM setup (not in update script)

These system packages are required but are **not** reinstalled by the VM update script:

- `postgresql` / `postgresql-client` — database server
- `python3.12-venv` — virtualenv support

PostgreSQL may not auto-start after install. Start it with:

```bash
sudo pg_ctlcluster 16 main start
```

Local dev database (created once):

```bash
sudo -u postgres psql -c "CREATE USER licencias WITH PASSWORD 'licencias' CREATEDB;"
sudo -u postgres psql -c "CREATE DATABASE licencias OWNER licencias;"
```

### Running the API

```bash
export DATABASE_URL=postgresql+psycopg2://licencias:licencias@localhost:5432/licencias
source .venv/bin/activate
python app.py
```

`AWS_ACCESS_KEY` and `AWS_SECRET_KEY` can be dummy values for local dev unless testing `/generar_descarga`.

### Lint / test / build

Not configured in this repository. There are no pytest, ruff, flake8, or CI scripts to run.

### Smoke test (core license flow)

```bash
curl -s -X POST http://localhost:5000/trial \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","device_id":"device-1","mercado":"binance"}'

curl -s http://localhost:5000/estadisticas
```

### Gotchas

- `DATABASE_URL` is **required** at startup; the app crashes if unset (`db_url.startswith(...)` on `None`).
- `Procfile` uses `gunicorn app:app` for production; local dev uses `python app.py`.
- boto3 S3 client is initialized at import time but only fails on actual S3 API calls.
