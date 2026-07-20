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
  POST /api/assemble  — merge extractResults + incumbent (from sites.csv) + meta into
                        a canonical tender (assemble_tender.assemble), validate it,
                        and write a versioned row to the Retool `tenders` table.
  POST /api/render    — render a canonical tender to dashboard HTML
                        (build_dashboard.render_tender). Takes a stored tender by id
                        (+ optional version) OR an inline tender JSON. Returns HTML
                        inline; static publish + UUID link is Phase 3.

Team UI: a vanilla single-page wizard (no build step) served from web/ at /app.
Access control is an app-level HTTP Basic gate (see team_gate below): one Vercel
project serves BOTH the private team app (/app + /api) and the PUBLIC client
dashboards (/d/<slug>/<uuid>), and Vercel Deployment Protection can't make a public
path exception on Pro — so the gate lives here and exempts the public route. Leave
Vercel Deployment Protection OFF.

Config via env vars (set in Vercel project settings, never in code):
  TEAM_ACCESS_KEY      — Basic-auth password for the team app (/api + /app). Unset
                         = open (local dev + tests). The public /d/* route + health
                         are always exempt.
  RETOOL_DATABASE_URL  — Postgres connection string for the tender store.
  ANTHROPIC_API_KEY    — (later, for /map) Claude key.
  ANTHROPIC_BASE_URL   — (optional) route Claude via the Vercel AI Gateway.
  ANTHROPIC_MODEL      — (optional) mapping model; defaults to claude-sonnet-5.
"""
import base64
import datetime
import json
import os
import secrets
import shutil
import sys
import tempfile
import uuid
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Make the deterministic pipeline importable (same trick as tests/ and the CLI).
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_BASE_DIR, "pipeline"))

app = FastAPI(title="RYE Tender Tool API")

# Quote file types the extractor can read.
ALLOWED_EXT = {".xlsx", ".xlsm", ".csv"}


# --- team gate (app-level, public client pages exempt) ----------------------
#
# One Vercel project serves BOTH the private team app (/app + /api) and the
# PUBLIC client dashboards (/d/<slug>/<uuid>). Vercel Deployment Protection can't
# make a public path exception on Pro, so the privacy boundary lives here: when
# TEAM_ACCESS_KEY is set, everything requires HTTP Basic auth (password == the
# key; username ignored) EXCEPT the public client route, health, and the root.
# Unset = open, so local dev + tests run unchanged. The browser handles the
# Basic prompt, so there's no unlock screen to maintain.
_GATE_OPEN_PATHS = {"/", "/api/health", "/favicon.ico"}
_GATE_PUBLIC_PREFIXES = ("/d/",)  # published client dashboards — must stay public


@app.middleware("http")
async def team_gate(request: Request, call_next):
    key = os.environ.get("TEAM_ACCESS_KEY")
    path = request.url.path
    gated = (
        key
        and path not in _GATE_OPEN_PATHS
        and not any(path.startswith(p) for p in _GATE_PUBLIC_PREFIXES)
    )
    if gated:
        supplied = ""
        hdr = request.headers.get("authorization", "")
        if hdr.startswith("Basic "):
            try:
                supplied = base64.b64decode(hdr[6:]).decode("utf-8", "ignore").partition(":")[2]
            except Exception:
                supplied = ""
        if not secrets.compare_digest(supplied, key):
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="RYE Tender Tool", charset="UTF-8"'},
                content="Authentication required.",
            )
    return await call_next(request)


def _norm_supplier(name: Optional[str]) -> Optional[str]:
    """Trim + collapse whitespace so 'Urban  Chain ' and 'Urban Chain' share a cache key.

    Cache hygiene (see HANDOVER): the supplier_mappings key match is EXACT, so
    stray spaces mean needless repeat LLM calls. Case is preserved — these are
    display names — and the UI's controlled dropdown is the real fix; this just
    stops whitespace variants slipping through.
    """
    if not name:
        return None
    return " ".join(name.split()) or None


# --- diagnostics -----------------------------------------------------------

@app.get("/")
def root():
    """Send visitors straight to the team app; the bare domain shouldn't show JSON."""
    return RedirectResponse(url="/app/")


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


# --- tenders table (versioned canonical store) -----------------------------

def _next_version(tender_id: str) -> Optional[int]:
    """Next version for a tender id (max existing + 1; 1 if new). None if no DB.

    'Version, never overwrite': each save is a new (id, version) row. Returns None
    only when there's no DB configured, so callers can decide how to degrade.
    """
    conn = _db_connect()
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("select coalesce(max(version), 0) from tenders where id = %s;", (tender_id,))
            return cur.fetchone()[0] + 1
    finally:
        conn.close()


def _write_tender(tender: dict) -> None:
    """Insert one canonical tender as a new versioned row in the Retool DB.

    Scalar columns are denormalised copies of top-level payload fields (so the
    register lists/filters without opening JSONB); `payload` holds the full tender.
    """
    from psycopg2.extras import Json

    conn = _db_connect()
    if conn is None:
        raise HTTPException(status_code=503, detail="RETOOL_DATABASE_URL is not set — cannot save the tender.")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "insert into tenders (id, version, client_name, utility, tender_label, status, "
                "created_at, created_by, expires_at, slug, url_uuid, dashboard_url, payload) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);",
                (
                    tender["id"], tender["version"], tender["client_name"],
                    tender.get("utility", "electricity"), tender["tender_label"],
                    tender.get("status", "draft"), tender.get("created_at"),
                    tender.get("created_by"), tender.get("expires_at"), tender.get("slug"),
                    tender.get("url_uuid"), tender.get("dashboard_url"), Json(tender),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _get_tender(tender_id: str, version: Optional[int] = None) -> Optional[dict]:
    """Fetch a stored tender's payload by id — latest version, or a specific one."""
    conn = _db_connect()
    if conn is None:
        raise HTTPException(status_code=503, detail="RETOOL_DATABASE_URL is not set — cannot fetch a tender by id.")
    try:
        with conn.cursor() as cur:
            if version is not None:
                cur.execute("select payload from tenders where id = %s and version = %s;", (tender_id, version))
            else:
                cur.execute("select payload from tenders where id = %s order by version desc limit 1;", (tender_id,))
            row = cur.fetchone()
            return row[0] if row else None  # jsonb -> dict
    finally:
        conn.close()


def _get_tender_by_uuid(url_uuid: str) -> Optional[dict]:
    """Latest version of the tender that owns this url_uuid — the public link key.

    Two-step on purpose: find the tender id that ever used this uuid, then return
    its LATEST version. That way a revoke (which writes a new version with a fresh
    uuid) makes the old link dead — the caller checks the latest payload's url_uuid
    still equals the requested one, and after a rotation it won't. Returns None if
    no tender ever used the uuid (or no DB).
    """
    conn = _db_connect()
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("select id from tenders where url_uuid = %s limit 1;", (url_uuid,))
            row = cur.fetchone()
            if not row:
                return None
            cur.execute(
                "select payload from tenders where id = %s order by version desc limit 1;",
                (row[0],),
            )
            latest = cur.fetchone()
            return latest[0] if latest else None
    finally:
        conn.close()


def _list_tenders() -> Optional[list]:
    """Latest version per tender (the `tenders_latest` view), for the team register.

    Returns the denormalised scalar columns plus site/offer counts and the
    recommended supplier — everything the register lists without opening the full
    JSONB payload. None when no DB is configured (the caller degrades to an empty
    register, like /api/suppliers).
    """
    conn = _db_connect()
    if conn is None:
        return None
    cols = ["id", "client_name", "tender_label", "utility", "status", "version",
            "created_at", "created_by", "expires_at", "slug", "url_uuid",
            "dashboard_url", "sites", "quotes", "recommended_supplier"]
    try:
        with conn.cursor() as cur:
            cur.execute(
                "select id, client_name, tender_label, utility, status, version, "
                "created_at, created_by, expires_at, slug, url_uuid, dashboard_url, "
                "coalesce(jsonb_array_length(payload->'sites'), 0), "
                "coalesce(jsonb_array_length(payload->'quotes'), 0), "
                "payload->'recommended'->>'supplier' "
                "from tenders_latest order by created_at desc;"
            )
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


@app.get("/api/tenders")
def tenders():
    """The team register: the latest version of every saved tender.

    Read-only listing over the `tenders_latest` view. Degrades to an empty list
    with a note when no DB is configured or reachable, so the UI's register screen
    renders cleanly in local dev. Timestamps/dates are JSON-encoded by FastAPI.
    """
    try:
        rows = _list_tenders()
    except Exception as e:
        return {"tenders": [], "note": f"DB unavailable ({type(e).__name__}) — no register."}
    if rows is None:
        return {"tenders": [], "note": "RETOOL_DATABASE_URL is not set — no register available."}
    return {"ok": True, "tenders": rows}


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

    supplier = _norm_supplier(supplier)
    tmp_path, filename = await _save_upload(file)
    notes: list[str] = []
    try:
        inspection = mh.inspect_file(tmp_path)
        inspection["path"] = filename
        fingerprint = mh.layout_fingerprint(inspection)

        # Flag stacked tables early (the review shows notes) — extract will refuse
        # these, so surface it before the user maps columns.
        for s in inspection["sheets"]:
            if s.get("extra_header_rows"):
                notes.append(
                    f"Sheet '{s['name']}' looks like it has more than one table "
                    f"(headers repeat at row {s['extra_header_rows'][0]}). Split it to "
                    f"one rate table per sheet before extracting."
                )

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
                # Cached sheet names can be stale — the fingerprint ignores sheet
                # names (they carry the date), so a re-dated re-tender of the same
                # supplier template hits this cached mapping but its sheet names no
                # longer exist in the new file. Re-point them at this upload before
                # returning, so /extract doesn't KeyError on a missing worksheet.
                mapping = mh.resync_sheets(mapping, inspection)
                have = {s["name"] for s in inspection["sheets"]}
                missing = [s for s in (mapping.get("sheets") or []) if s not in have]
                if missing:
                    notes.append(
                        "cached mapping still references sheet(s) not in this file: "
                        + ", ".join(missing)
                        + " — set the sheet list on the review screen before extracting"
                    )
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
    supplier = _norm_supplier(body.supplier)
    if not supplier:
        raise HTTPException(status_code=400, detail="supplier is required to save a mapping.")
    _cache_put(supplier, body.layout_fingerprint, body.mapping, body.confirmed_by)
    return {
        "ok": True,
        "saved": True,
        "supplier": supplier,
        "layout_fingerprint": body.layout_fingerprint,
    }


@app.get("/api/suppliers")
def suppliers():
    """Distinct supplier names from the learned-mappings cache.

    Powers the UI's controlled supplier dropdown — the cache key match is exact,
    so picking from this list (rather than free-typing) is what makes repeat
    layouts actually hit the cache. Degrades to an empty list with a note when
    no DB is configured or reachable.
    """
    try:
        conn = _db_connect()
    except Exception as e:
        return {"suppliers": [], "note": f"DB unavailable ({type(e).__name__}) — type the supplier name instead."}
    if conn is None:
        return {"suppliers": [], "note": "RETOOL_DATABASE_URL is not set — type the supplier name instead."}
    try:
        with conn.cursor() as cur:
            cur.execute("select distinct supplier from supplier_mappings order by supplier;")
            return {"suppliers": [r[0] for r in cur.fetchall()]}
    finally:
        conn.close()


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
            # A SystemExit here is a deliberate, user-facing message (e.g. the
            # multi-table-sheet guidance) — surface it verbatim, not type-prefixed.
            msg = str(e) if isinstance(e, SystemExit) else f"{type(e).__name__}: {e}"
            raise HTTPException(status_code=422, detail=f"Couldn't process '{filename}': {msg}")

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


# --- /assemble -------------------------------------------------------------

def _extract_mpxns(extracts: list) -> set:
    """Every meter point mentioned across the extracts (for incumbent filtering)."""
    out = set()
    for e in extracts:
        for s in e.get("sites", []):
            if s.get("mpxn"):
                out.add(str(s["mpxn"]))
        for q in e.get("quotes", []):
            for ln in q.get("lines", []):
                if ln.get("mpxn"):
                    out.add(str(ln["mpxn"]))
    return out


@app.post("/api/assemble")
async def assemble_endpoint(
    extracts: str = Form(...),
    meta: str = Form(...),
    sites_csv: Optional[UploadFile] = File(None),
    persist: bool = Form(True),
):
    """Stitch extractResults + incumbent + meta into a canonical tender, and save it.

    `extracts` is a JSON array of extractResult objects (the /extract outputs).
    `meta` is a JSON object (client_name and tender_label required; optional id,
    version, status, utility, expires_at, day_split, recommended, rye_fee, notes,
    …). An optional `sites_csv` provides the incumbent contract (its rate columns +
    incumbentSupplier), joined on MPAN and restricted to this tender's meters.

    Assembles via assemble_tender.assemble (moves NO values; stamps meta), validates
    against the canonical schema, then writes a new versioned row to the Retool
    `tenders` table — version, never overwrite. Set persist=false to assemble +
    validate without writing (useful before a DB is wired, or for a dry run).
    """
    import assemble_tender as at

    try:
        extracts_obj = json.loads(extracts)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"`extracts` is not valid JSON: {e}")
    if isinstance(extracts_obj, dict):
        extracts_obj = [extracts_obj]
    if not isinstance(extracts_obj, list) or not extracts_obj:
        raise HTTPException(status_code=400, detail="`extracts` must be a non-empty array of extractResult objects.")
    try:
        meta_obj = json.loads(meta)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"`meta` is not valid JSON: {e}")
    if not isinstance(meta_obj, dict) or not meta_obj.get("client_name") or not meta_obj.get("tender_label"):
        raise HTTPException(status_code=400, detail="`meta` must include client_name and tender_label.")

    warnings: list[str] = []

    # Build the incumbent from sites.csv, scoped to this tender's meters + client.
    incumbent = None
    csv_path = None
    if sites_csv is not None and sites_csv.filename:
        csv_path, _ = await _save_upload(sites_csv)
    try:
        if csv_path:
            try:
                incumbent = at.incumbent_from_sites_csv(
                    csv_path,
                    client_name=meta_obj.get("client_name"),
                    mpxns=_extract_mpxns(extracts_obj),
                )
            except (Exception, SystemExit) as e:
                raise HTTPException(status_code=422, detail=f"Could not read incumbent from sites.csv: {type(e).__name__}: {e}")
            if incumbent is None:
                warnings.append("sites.csv had no incumbent rate data for this tender's meters — assembling with no incumbent.")
            elif incumbent.get("supplier") == "Various":
                warnings.append("Meters span multiple incumbent suppliers — incumbent shown as 'Various'.")
            elif incumbent.get("supplier") == "Unknown":
                warnings.append("Incumbent rates present but no incumbentSupplier named — shown as 'Unknown'.")

        # Version, never overwrite: bump to the next version for an existing id.
        if meta_obj.get("id") and persist:
            nv = _next_version(meta_obj["id"])
            if nv is not None:
                meta_obj["version"] = nv

        try:
            tender = at.assemble(extracts_obj, meta_obj, incumbent=incumbent)
            # The shared sites.csv is RYE's authoritative site reference: overlay its
            # site names + EAC/kVA onto the merged sites here too, so they win even if
            # the file wasn't present when the quotes were extracted.
            if csv_path:
                at.apply_site_reference(tender["sites"], csv_path)
            at.validate_tender(tender)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Assembly/validation failed: {type(e).__name__}: {e}")

        persisted = False
        if persist:
            _write_tender(tender)
            persisted = True
        else:
            warnings.append("persist=false — tender assembled and validated but NOT written to the DB.")

        return {
            "ok": True,
            "persisted": persisted,
            "id": tender["id"],
            "version": tender["version"],
            "status": tender["status"],
            "slug": tender.get("slug"),
            "url_uuid": tender.get("url_uuid"),
            "dashboard_url": tender.get("dashboard_url"),
            "counts": {
                "sites": len(tender.get("sites", [])),
                "quotes": len(tender.get("quotes", [])),
                "incumbent_lines": len((incumbent or {}).get("lines", [])),
            },
            "incumbent_supplier": (incumbent or {}).get("supplier"),
            "warnings": warnings,
            "tender": tender,
        }
    finally:
        if csv_path:
            try:
                os.unlink(csv_path)
            except OSError:
                pass


# --- /cost -----------------------------------------------------------------

@app.post("/api/cost")
async def cost(extracts: str = Form(...), sites_csv: Optional[UploadFile] = File(None)):
    """Rank the extracted offers by all-in cost, deterministically.

    Backs the assemble screen's "which offers to show the client" tick-list: the
    team needs to see each offer's standardised annual cost and which is cheapest
    BEFORE choosing the (up to 2) featured offers. Costs are computed by the
    EXISTING cost engine (`build_dashboard.compute_offer`) — never re-implemented
    in the browser — so the ranking can't drift from what /render will show.

    Input: `extracts` — a JSON array of extractResult objects (the /extract
    outputs), same shape /assemble takes. No LLM, no DB, no persistence.

    For each offer returns the standardised annual cost (energy + all standing/
    capacity/network/meter charges, on one consumption basis) and effective p/kWh,
    plus `covers_all_sites` (an offer missing meters for some sites isn't a like-
    for-like comparison). The cheapest FULL-COVERAGE offer is flagged `cheapest`
    (that's the price-based recommendation); offers are returned sorted
    full-coverage-first, then cheapest-first. Uses the standing day/weekend splits.
    """
    import assemble_tender as at
    import build_dashboard as bd

    try:
        extracts_obj = json.loads(extracts)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"`extracts` is not valid JSON: {e}")
    if isinstance(extracts_obj, dict):
        extracts_obj = [extracts_obj]
    if not isinstance(extracts_obj, list) or not extracts_obj:
        raise HTTPException(status_code=400, detail="`extracts` must be a non-empty array of extractResult objects.")

    # Assemble a throwaway tender (never persisted) so the offers, site facts and
    # the standing splits are exactly what /assemble + /render would use.
    try:
        tender = at.assemble(extracts_obj, {"client_name": "_", "tender_label": "_"})
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not read the extracts: {type(e).__name__}: {e}")

    # Overlay the shared sites.csv (authoritative EAC/kVA) so the ranking matches the
    # costs /render will show — same as /assemble does. Names don't affect cost, but
    # a db EAC does, so keep the two in step.
    if sites_csv is not None and sites_csv.filename:
        ref_path, _ = await _save_upload(sites_csv)
        try:
            at.apply_site_reference(tender["sites"], ref_path)
        except (Exception, SystemExit) as e:
            raise HTTPException(status_code=422, detail=f"Could not read sites.csv: {type(e).__name__}: {e}")
        finally:
            try:
                os.unlink(ref_path)
            except OSError:
                pass

    sites = {s.get("mpxn"): s for s in tender.get("sites", [])}
    site_mpxns = {m for m in sites if m}
    quotes = tender.get("quotes", [])
    offers = []
    work = tempfile.mkdtemp(prefix="rye-cost-")
    try:
        for i, q in enumerate(quotes):
            if not q.get("lines"):
                label = " — ".join(x for x in (q.get("supplier"), q.get("term")) if x) or f"offer {i + 1}"
                raise HTTPException(
                    status_code=422,
                    detail=f"Offer '{label}' has no priced rows — check the mapping "
                           f"and header row for that sheet (a sheet with two stacked "
                           f"tables is the usual cause).",
                )
            csv_path = os.path.join(work, f"offer-{i}.csv")
            bd._write_offer_csv(csv_path, q.get("lines", []), sites)
            entry = {"_csv_path": csv_path, "_id": f"offer-{i}",
                     "supplier": q.get("supplier"), "term": q.get("term", "")}
            if q.get("category"):
                entry["category"] = q["category"]
            if q.get("charge_basis"):
                entry["charge_basis"] = q["charge_basis"]
            try:
                computed = bd.compute_offer(entry, tender)
            except (Exception, SystemExit) as e:
                raise HTTPException(status_code=422,
                                    detail=f"Costing '{q.get('supplier')}' failed: {type(e).__name__}: {e}")
            line_mpxns = {ln.get("mpxn") for ln in q.get("lines", []) if ln.get("mpxn")}
            offers.append({
                "index": i,
                "supplier": q.get("supplier"),
                "term": q.get("term", ""),
                "category": computed.get("category"),
                "annual_cost": computed.get("total"),
                "effective_pkwh": (computed.get("perKwh") or {}).get("effective"),
                "covers_all_sites": site_mpxns.issubset(line_mpxns),
                "warnings": computed.get("warnings", []),
                "cheapest": False,
            })
    finally:
        shutil.rmtree(work, ignore_errors=True)

    # Cheapest among full-coverage offers (fall back to all if none cover every site).
    pool = [o for o in offers if o["covers_all_sites"]] or offers
    pool = [o for o in pool if o["annual_cost"] is not None]
    if pool:
        cheapest = min(pool, key=lambda o: o["annual_cost"])
        cheapest["cheapest"] = True
    offers.sort(key=lambda o: (not o["covers_all_sites"],
                               o["annual_cost"] if o["annual_cost"] is not None else float("inf")))

    return {
        "ok": True,
        "site_count": len(site_mpxns),
        "eac_total": round(sum((s.get("eac") or 0) for s in sites.values()), 2),
        "day_split": tender.get("day_split"),
        "weekend_split": tender.get("weekend_split"),
        "offers": offers,
    }


# --- /render ---------------------------------------------------------------

class RenderBody(BaseModel):
    tender_id: Optional[str] = None
    version: Optional[int] = None
    tender: Optional[dict[str, Any]] = None


@app.post("/api/render")
def render_endpoint(body: RenderBody):
    """Render a canonical tender to the client dashboard HTML.

    Supply EITHER `tender_id` (fetched from the Retool `tenders` table — latest
    version, or `version` if given) OR an inline `tender` object. Returns the
    dashboard HTML inline (text/html); the cost engine is build_dashboard, reused
    unchanged via `render_tender`. Static publishing to the per-client UUID URL is
    Phase 3 — this first cut returns the HTML so the pipeline is complete and
    testable end-to-end.
    """
    import build_dashboard as bd

    if bool(body.tender_id) == bool(body.tender):
        raise HTTPException(status_code=400, detail="Provide exactly one of `tender_id` or `tender`.")

    tender = body.tender
    if body.tender_id:
        tender = _get_tender(body.tender_id, body.version)
        if tender is None:
            raise HTTPException(status_code=404, detail=f"No tender found for id={body.tender_id}"
                                + (f" version={body.version}" if body.version is not None else ""))

    if not isinstance(tender, dict) or not tender.get("quotes"):
        raise HTTPException(status_code=400, detail="Tender has no quotes to render.")

    try:
        html = bd.render_tender(tender)
    except (Exception, SystemExit) as e:
        raise HTTPException(status_code=422, detail=f"Render failed: {type(e).__name__}: {e}")
    return HTMLResponse(content=html)


# --- publish / revoke / public client link ---------------------------------

_NOINDEX = {"X-Robots-Tag": "noindex, nofollow"}


class PublishBody(BaseModel):
    tender_id: str
    version: Optional[int] = None


class RevokeBody(BaseModel):
    tender_id: str


def _is_expired(expires_at) -> bool:
    """True if an ISO date (YYYY-MM-DD) is strictly before today (UTC)."""
    if not expires_at:
        return False
    try:
        d = datetime.date.fromisoformat(str(expires_at)[:10])
        return d < datetime.datetime.now(datetime.timezone.utc).date()
    except ValueError:
        return False


@app.post("/api/publish")
def publish_endpoint(body: PublishBody, request: Request):
    """Publish a tender: mint/keep its unguessable link and mark it live.

    Writes a NEW version (version-never-overwrite) with status=published, a slug +
    url_uuid (minted if absent), and the full dashboard_url pointing at the public
    route on this deployment. Returns the client link. The link is public because
    the team gate exempts /d/*.
    """
    import assemble_tender as at

    tender = _get_tender(body.tender_id, body.version)
    if tender is None:
        raise HTTPException(status_code=404, detail=f"No tender found for id={body.tender_id}")
    if not tender.get("quotes"):
        raise HTTPException(status_code=400, detail="Tender has no quotes — nothing to publish.")

    tender["status"] = "published"
    if not tender.get("url_uuid"):
        tender["url_uuid"] = str(uuid.uuid4())
    if not tender.get("slug"):
        tender["slug"] = at.slugify(tender.get("client_name")) or "client"
    nv = _next_version(tender["id"])
    if nv is not None:
        tender["version"] = nv
    tender["created_at"] = at._now_rfc3339_z()
    base = str(request.base_url).rstrip("/")
    tender["dashboard_url"] = f"{base}/d/{tender['slug']}/{tender['url_uuid']}"

    try:
        at.validate_tender(tender)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Publish validation failed: {type(e).__name__}: {e}")
    _write_tender(tender)
    return {
        "ok": True, "id": tender["id"], "version": tender["version"], "status": "published",
        "url": tender["dashboard_url"], "url_uuid": tender["url_uuid"], "slug": tender["slug"],
        "expires_at": tender.get("expires_at"),
    }


@app.post("/api/revoke")
def revoke_endpoint(body: RevokeBody):
    """Revoke a published link: rotate url_uuid + set back to draft.

    Writes a new version with a fresh url_uuid, so the old link stops resolving
    (the public route checks the LATEST version's uuid). Re-publishing mints a new
    link. This is the leaked-link kill switch from the build spec.
    """
    import assemble_tender as at

    tender = _get_tender(body.tender_id)
    if tender is None:
        raise HTTPException(status_code=404, detail=f"No tender found for id={body.tender_id}")
    tender["url_uuid"] = str(uuid.uuid4())
    tender["status"] = "draft"
    tender["dashboard_url"] = None
    nv = _next_version(tender["id"])
    if nv is not None:
        tender["version"] = nv
    tender["created_at"] = at._now_rfc3339_z()
    try:
        at.validate_tender(tender)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Revoke validation failed: {type(e).__name__}: {e}")
    _write_tender(tender)
    return {"ok": True, "revoked": True, "id": tender["id"], "version": tender["version"], "status": "draft"}


def _client_message_html(title: str, body: str) -> str:
    """Minimal branded standalone page for the expired / not-found client states."""
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='robots' content='noindex, nofollow'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{title}</title><style>"
        "body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;"
        "background:#000;color:#ededed;font-family:ui-sans-serif,system-ui,sans-serif}"
        ".card{max-width:420px;padding:32px;text-align:center}"
        ".logo{width:28px;height:28px;border-radius:6px;background:#fff;color:#000;font-weight:700;"
        "display:flex;align-items:center;justify-content:center;margin:0 auto 16px}"
        "h1{font-size:18px;font-weight:600;margin:0 0 8px}p{color:#a1a1a1;font-size:14px;line-height:1.5;margin:0}"
        "</style></head><body><div class='card'><div class='logo'>R</div>"
        f"<h1>{title}</h1><p>{body}</p></div></body></html>"
    )


@app.get("/d/{slug}/{url_uuid}")
def public_dashboard(slug: str, url_uuid: str):
    """The public client dashboard, reached by the unguessable link. No auth.

    Serves the rendered dashboard only when the LATEST version of the tender still
    carries this url_uuid AND is published AND not past expires_at. Otherwise a
    plain expired / not-found page. Always noindex. `slug` is cosmetic — the uuid
    is the secret.
    """
    import build_dashboard as bd

    tender = _get_tender_by_uuid(url_uuid)
    if not tender or tender.get("url_uuid") != url_uuid or tender.get("status") != "published":
        return HTMLResponse(
            _client_message_html("Link unavailable",
                                 "This tender link is no longer active. Please contact RYE for an up-to-date link."),
            status_code=404, headers=_NOINDEX,
        )
    if _is_expired(tender.get("expires_at")):
        return HTMLResponse(
            _client_message_html("This quote has expired",
                                 "The pricing in this tender is no longer valid. Please contact RYE for a refreshed quote."),
            status_code=200, headers=_NOINDEX,
        )
    try:
        html = bd.render_tender(tender)
    except (Exception, SystemExit) as e:
        raise HTTPException(status_code=500, detail=f"Could not render the dashboard: {type(e).__name__}: {e}")
    return HTMLResponse(content=html, headers=_NOINDEX)


# --- team UI (static, no build step) ----------------------------------------
# Vanilla single-page wizard served by this same app — one repo, one deploy.
# Mounted last so it can never shadow an /api route. html=True serves index.html
# at /app/. (Same pattern as assets/dashboard_template.html: repo files are
# bundled into the Vercel function and readable at runtime.)

_WEB_DIR = os.path.join(_BASE_DIR, "web")
if os.path.isdir(_WEB_DIR):
    app.mount("/app", StaticFiles(directory=_WEB_DIR, html=True), name="app")
