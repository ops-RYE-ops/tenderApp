-- RYE tender tool — Retool DB schema (Phase 0, immediate next step).
-- Postgres. The canonical tender JSON (schema/tender.schema.json) is stored
-- verbatim in tenders.payload; the scalar columns are denormalised copies of
-- top-level payload fields so the register can list/filter without opening JSONB.
--
-- Version, never overwrite: one row per (id, version). The register shows the
-- latest version per id (see the view below).

create extension if not exists pgcrypto;  -- for gen_random_uuid()

create table if not exists tenders (
    id            uuid        not null,                 -- payload.id (stable across versions)
    version       integer     not null check (version >= 1),
    client_name   text        not null,
    utility       text        not null default 'electricity',
    tender_label  text        not null,
    status        text        not null check (status in ('draft','published','expired')),
    created_at    timestamptz not null default now(),
    created_by    text        not null,
    expires_at    date,
    slug          text,
    url_uuid      uuid,                                 -- the unguessable link secret
    dashboard_url text,
    payload       jsonb       not null,                 -- full canonical tender (source of truth)
    primary key (id, version)
);

create index if not exists tenders_url_uuid_idx on tenders (url_uuid);
create index if not exists tenders_client_idx    on tenders (client_name);
create index if not exists tenders_payload_gin   on tenders using gin (payload);

-- Latest version per tender — what the team register reads.
create or replace view tenders_latest as
select distinct on (id) *
from tenders
order by id, version desc;

-- Learned-mappings cache: repeat supplier layouts skip the LLM.
create table if not exists supplier_mappings (
    id                 uuid        primary key default gen_random_uuid(),
    supplier           text        not null,
    layout_fingerprint text        not null,   -- hash of the header signature
    mapping            jsonb       not null,    -- a validated mapping.json
    confirmed_by       text,
    created_at         timestamptz not null default now(),
    unique (supplier, layout_fingerprint)
);
