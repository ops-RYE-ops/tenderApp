#!/usr/bin/env python3
"""
Vercel FastAPI spike — proves the two Phase-1 unknowns before we build the real
backend on top:

  1. GET /api/health   — the Python runtime deploys and runs on Vercel at all.
  2. GET /api/db-check — a Vercel function can reach the Retool DB over SSL and
                         see the schema we just created.

Deliberately throwaway and self-contained: it imports none of the pipeline code
and writes nothing. Once these two endpoints go green on the deployed URL, we
know the stack works and can start wrapping process_quote / assemble_tender /
build_dashboard as real endpoints.

Vercel auto-detects the `app` instance below (a supported root entrypoint), so
no vercel.json or routing config is needed for the spike. The DB connection
string is read from the RETOOL_DATABASE_URL environment variable set in the
Vercel project settings — never hard-coded, never committed.
"""
import os
import sys

from fastapi import FastAPI

app = FastAPI(title="RYE Tender Tool API (Phase-1 spike)")


@app.get("/")
def root():
    return {"ok": True, "service": "rye-tender-tool", "note": "spike — see /api/health"}


@app.get("/api/health")
def health():
    """Liveness check: if this returns, the Python runtime deployed and runs."""
    return {
        "ok": True,
        "service": "rye-tender-tool",
        "python_version": sys.version.split()[0],
    }


def _with_sslmode(dsn: str) -> str:
    """Ensure the connection enforces SSL, whichever form the DSN is in.

    Retool DB only accepts encrypted connections. Retool's own connection URL
    usually already includes sslmode=require; this just guarantees it.
    """
    if "sslmode" in dsn:
        return dsn
    if "://" in dsn:  # URL form: postgres://...
        return dsn + ("&" if "?" in dsn else "?") + "sslmode=require"
    return dsn + " sslmode=require"  # keyword form: host=... dbname=...


@app.get("/api/db-check")
def db_check():
    """Connectivity check: can a Vercel function reach the Retool DB + see our schema?

    Returns the public tables and the tenders row count. Read-only. On failure
    it returns the error text (not a 500) so the cause is visible in the browser.
    """
    dsn = os.environ.get("RETOOL_DATABASE_URL")
    if not dsn:
        return {"ok": False, "error": "RETOOL_DATABASE_URL is not set in the Vercel project's environment variables"}
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
    except Exception as e:  # surface the reason rather than a bare 500
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
