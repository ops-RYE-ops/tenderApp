#!/usr/bin/env python3
"""
RYE Tender Tool — Vercel backend (FastAPI).

Thin HTTP layer over the deterministic pipeline. Every endpoint IMPORTS and calls
the existing scripts in pipeline/ — it never re-implements extraction, mapping or
cost logic (the spec's "functions import the scripts, never paraphrase them").

Vercel auto-detects the `app` instance below (root entrypoint). The pipeline dir
is put on sys.path so the scripts import exactly as they do in the tests and CLI.

Endpoints (built incrementally):
  GET  /api/health    — liveness (Python runtime).
  GET  /api/db-check  — Retool DB reachable over SSL + schema visible.
  POST /api/inspect   — read an uploaded quote: sheets, first rows, header
                        candidates. Pure, no network, no LLM. Backs the mapping
                        review screen.

Config via env vars (set in Vercel project settings, never in code):
  RETOOL_DATABASE_URL  — Postgres connection string for the Retool DB.
  ANTHROPIC_API_KEY    — (later, for /map) Claude key.
  ANTHROPIC_BASE_URL   — (optional) route Claude via the Vercel AI Gateway.
  ANTHROPIC_MODEL      — (optional) mapping model; defaults to claude-sonnet-5.
"""
import os
import sys
import tempfile

from fastapi import FastAPI, File, HTTPException, UploadFile

# Make the deterministic pipeline importable (same trick as tests/ and the CLI).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline"))

app = FastAPI(title="RYE Tender Tool API")

# Quote file types the extractor can read.
ALLOWED_EXT = {".xlsx", ".xlsm", ".csv"}


# --- diagnostics -----------------------------------------------------------

@app.get("/")
def root():
    return {"ok": True, "service": "rye-tender-tool", "see": "/docs"}


@app.get("/api/health")
def health():
    """Liveness: if this returns, the Python runtime deployed and runs."""
    return {"ok": True, "service": "rye-tender-tool", "python_version": sys.version.split()[0]}


def _with_sslmode(dsn: str) -> str:
    if "sslmode" in dsn:
        return dsn
    if "://" in dsn:
        return dsn + ("&" if "?" in dsn else "?") + "sslmode=require"
    return dsn + " sslmode=require"


@app.get("/api/db-check")
def db_check():
    """Read-only check that a Vercel function can reach the Retool DB + see the schema."""
    dsn = os.environ.get("RETOOL_DATABASE_URL")
    if not dsn:
        return {"ok": False, "error": "RETOOL_DATABASE_URL is not set in the Vercel env vars"}
    try:
        import psycopg2

        conn = psycopg2.connect(_with_sslmode(dsn), connect_timeout=10)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "select table_name from information_schema.tables "
                    "where table_schema = 'public' order by table_name;"
                )
                tables = [r[0] for r in cur.fetchall()]
                cur.execute("select count(*) from tenders;")
                tenders_rows = cur.fetchone()[0]
        finally:
            conn.close()
        return {"ok": True, "tables": tables, "tenders_rows": tenders_rows}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# --- helpers ---------------------------------------------------------------

async def _save_upload(file: UploadFile) -> tuple[str, str]:
    """Persist an uploaded quote to a temp file, preserving its extension.

    Returns (temp_path, original_filename). Vercel functions get an ephemeral,
    writable /tmp, which is exactly what the file-reading scripts expect: they
    take a path, not a stream. Caller is responsible for deleting temp_path.
    """
    filename = file.filename or "upload"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext or '(none)'}'. Upload .xlsx, .xlsm or .csv.",
        )
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    fd, tmp_path = tempfile.mkstemp(suffix=ext)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return tmp_path, filename


# --- /inspect --------------------------------------------------------------

@app.post("/api/inspect")
async def inspect(file: UploadFile = File(...)):
    """Read an uploaded quote and return its structure for the mapping screen.

    Per sheet: ranked header-row candidates, the best-guess header row, and the
    first ~15 rows. Pure — no network, no LLM, no values leave. This is what the
    team confirms/overrides before /map or /extract runs.
    """
    import map_headers as mh

    tmp_path, filename = await _save_upload(file)
    try:
        inspection = mh.inspect_file(tmp_path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not read '{filename}': {type(e).__name__}: {e}")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    inspection["path"] = filename  # show the real upload name, not the temp name
    return inspection
