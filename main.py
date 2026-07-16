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
  POST /api/map       — propose a mapping.json for an uploaded quote. Cache
                        lookup by supplier + layout fingerprint first; on a miss,
                        the single Claude call (map_headers.propose_mapping).
                        Returns the mapping + sample values for confirm/override.
  POST /api/map/confirm — save a confirmed/overridden mapping to the learned-
                        mappings cache so the same layout skips the LLM next time.
  POST /api/extract   — run the deterministic extractor (process_quote.run) with a
                        confirmed mapping + optional site-reference CSV → the
                        canonical extractResult ({sites, quotes}). No LLM. Flags any
                        meter points with no site-reference match.

Config via env vars (set in Vercel project settings, never in code):
  RETOOL_DATABASE_URL  — Postgres connection string for the Retool DB.
  ANTHROPIC_API_KEY    — (later, for /map) Claude key.
  ANTHROPIC_BASE_URL   — (optional) route Claude via the Vercel AI Gateway.
  ANTHROPIC_MODEL      — (optional) mapping model; defaults to claude-sonnet-5.
"""
import json
import os
import shutil
import sys
import tempfile
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

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


# --- learned-mappings cache (supplier_mappings table) ----------------------

def _db_connect():
    """Open a short-lived SSL connection to the Retool DB, or None if unconfigured.

    /map degrades gracefully without a DB: it just can't consult or write the
    cache, so it always goes to the LLM. That keeps mapping usable in local dev
    where RETOOL_DATABASE_URL may be unset.
    """
    dsn = os.environ.get("RETOOL_DATABASE_URL")
    if not dsn:
        return None
    import psycopg2

    return psycopg2.connect(_with_sslmode(dsn), connect_timeout=10)


def _cache_get(supplier: str, fingerprint: str) -> Optional[dict]:
    """Return a cached mapping for (supplier, fingerprint), or None on a miss."""
    conn = _db_connect()
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "select mapping from supplier_mappings "
                "where supplier = %s and layout_fingerprint = %s limit 1;",
                (supplier, fingerprint),
            )
            row = cur.fetchone()
            return row[0] if row else None  # jsonb comes back as a dict
    finally:
        conn.close()


def _cache_put(supplier: str, fingerprint: str, mapping: dict, confirmed_by: Optional[str]) -> None:
    """Upsert a confirmed mapping. One row per (supplier, layout_fingerprint)."""
    from psycopg2.extras import Json

    conn = _db_connect()
    if conn is None:
        raise HTTPException(
            status_code=503,
            detail="RETOOL_DATABASE_URL is not set — cannot save to the mappings cache.",
        )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "insert into supplier_mappings (supplier, layout_fingerprint, mapping, confirmed_by) "
                "values (%s, %s, %s, %s) "
                "on conflict (supplier, layout_fingerprint) do update set "
                "mapping = excluded.mapping, confirmed_by = excluded.confirmed_by, "
                "created_at = now();",
                (supplier, fingerprint, Json(mapping), confirmed_by),
            )
        conn.commit()
    finally:
        conn.close()


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


# --- /map ------------------------------------------------------------------

@app.post("/api/map")
async def map_quote(
    file: UploadFile = File(...),
    supplier: Optional[str] = Form(None),
    sample_rows: int = Form(3),
):
    """Propose a mapping for an uploaded quote — cache first, then Claude.

    Steps: inspect the file (pure) → compute the layout fingerprint → if a
    supplier is given and this (supplier, fingerprint) is in the cache, return
    that mapping and skip the LLM; otherwise make the single Claude call. Either
    way, return the mapping plus per-field sample values so a human can confirm
    or override before /extract runs. Confirmed mappings are saved back via
    /api/map/confirm. AI maps, code moves numbers: the model only ever named the
    columns; the sample values are read here deterministically and never returned
    to it.
    """
    import map_headers as mh

    tmp_path, filename = await _save_upload(file)
    notes: list[str] = []
    try:
        inspection = mh.inspect_file(tmp_path)
        inspection["path"] = filename
        fingerprint = mh.layout_fingerprint(inspection)

        mapping = None
        source = None
        if supplier:
            try:
                mapping = _cache_get(supplier, fingerprint)
            except Exception as e:  # a cache read must never break mapping
                notes.append(f"cache lookup failed ({type(e).__name__}); falling back to the LLM")
                mapping = None
            if mapping is not None:
                source = "cache"
        else:
            notes.append("no supplier provided — cache lookup skipped; saving will need a supplier")

        if mapping is None:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise HTTPException(
                    status_code=503,
                    detail="No cached mapping for this layout and ANTHROPIC_API_KEY is not set, "
                           "so Claude can't be called. Set the key in the Vercel env vars.",
                )
            try:
                mapping = mh.propose_mapping(inspection, supplier=supplier, sample_rows=sample_rows)
            except HTTPException:
                raise
            except (Exception, SystemExit) as e:
                raise HTTPException(status_code=502, detail=f"Mapping call failed: {type(e).__name__}: {e}")
            source = "llm"

        return {
            "source": source,                       # 'cache' or 'llm'
            "cache_hit": source == "cache",
            "supplier": supplier,
            "layout_fingerprint": fingerprint,
            "file": filename,
            "mapping": mapping,
            "sample_values": mh.sample_values(inspection, mapping),
            "sheets": [                              # light context for the review UI
                {"name": s["name"],
                 "header_row_best_guess": s["header_row_best_guess"],
                 "headers": s["headers"]}
                for s in inspection["sheets"]
            ],
            "notes": notes,
        }
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


class ConfirmMappingBody(BaseModel):
    supplier: str
    layout_fingerprint: str
    mapping: dict[str, Any]
    confirmed_by: Optional[str] = None


@app.post("/api/map/confirm")
def confirm_mapping(body: ConfirmMappingBody):
    """Save a confirmed/overridden mapping to the learned-mappings cache.

    Upserts on (supplier, layout_fingerprint) so re-confirming updates in place.
    Next time the same supplier layout is uploaded, /api/map serves this from the
    cache and skips the LLM entirely — the spend-saving path in the build spec.
    """
    if not body.mapping.get("columns"):
        raise HTTPException(status_code=400, detail="mapping.columns is required to save a usable mapping.")
    _cache_put(body.supplier, body.layout_fingerprint, body.mapping, body.confirmed_by)
    return {
        "ok": True,
        "saved": True,
        "supplier": body.supplier,
        "layout_fingerprint": body.layout_fingerprint,
    }


# --- /extract --------------------------------------------------------------

@app.post("/api/extract")
async def extract(
    file: UploadFile = File(...),
    mapping: str = Form(...),
    supplier: Optional[str] = Form(None),
    site_reference: Optional[UploadFile] = File(None),
):
    """Extract canonical lines from a quote using a confirmed mapping.

    Thin wrapper over `process_quote.run` — the deterministic extractor. No LLM,
    no network beyond the upload: the mapping (already confirmed via /map) names
    the columns, and Python copies the actual values verbatim. Returns the
    canonical `extractResult` ({sites, quotes}) that /assemble later stitches into
    a full tender.

    `mapping` is the confirmed mapping.json as a form field (string). An optional
    `site_reference` CSV joins meter points to RYE's site names on MPxN; any meter
    point with no match is returned in `unmatched_mpxn` so the team can resolve it
    rather than it being silently accepted (a spec requirement).
    """
    import process_quote as pq

    try:
        mapping_obj = json.loads(mapping)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"`mapping` is not valid JSON: {e}")
    if not isinstance(mapping_obj, dict) or not mapping_obj.get("columns"):
        raise HTTPException(status_code=400, detail="`mapping` must be an object with a non-empty `columns`.")

    quote_path, filename = await _save_upload(file)
    ref_path = None
    if site_reference is not None and site_reference.filename:
        ref_path, _ = await _save_upload(site_reference)
    out_dir = tempfile.mkdtemp(prefix="rye-extract-")
    try:
        try:
            _written, extract_result, unmatched = pq.run(
                quote_path, mapping_obj, out_dir,
                db_csv=ref_path, supplier=supplier, emit_csv=False,
            )
        except HTTPException:
            raise
        except (Exception, SystemExit) as e:
            raise HTTPException(status_code=422, detail=f"Extraction failed for '{filename}': {type(e).__name__}: {e}")

        extract_result.pop("_json_path", None)  # a temp path — meaningless to the caller
        sites = extract_result.get("sites", [])
        quotes = extract_result.get("quotes", [])
        return {
            "ok": True,
            "file": filename,
            "supplier": supplier or mapping_obj.get("supplier"),
            "extract_result": extract_result,
            "counts": {
                "sites": len(sites),
                "quotes": len(quotes),
                "lines": sum(len(q.get("lines", [])) for q in quotes),
            },
            "unmatched_mpxn": sorted(unmatched),
            "site_reference_used": ref_path is not None,
        }
    finally:
        for p in (quote_path, ref_path):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass
        shutil.rmtree(out_dir, ignore_errors=True)
