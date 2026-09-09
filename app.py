"""
Startup Valuation API - single-file FastAPI app.

Wraps the valuation_engine package with HTTP access and SQLite
persistence. Organized into sections below (database, schemas, routes)
rather than split across files, so the whole API is one file to read,
copy, or upload.

Run with:  uvicorn app:app --reload
Docs at:   http://localhost:8000/docs
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import valuation_engine as ve

# ============================================================================
# SECTION 1 — Database
#
# Minimal SQLite persistence for valuation runs. One row per run: the raw
# input JSON (so a run can be audited/replayed) and the computed output
# JSON. No ORM - intentionally small.
# ============================================================================

DB_PATH = Path(__file__).parent / "data" / "valuations.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS valuations (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    company_name TEXT,
    input_json TEXT NOT NULL,
    output_json TEXT NOT NULL
);
"""


@contextmanager
def _get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _get_connection() as conn:
        conn.execute(_SCHEMA)


def save_valuation(company_name: str, input_dict: dict, output_dict: dict) -> str:
    valuation_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    with _get_connection() as conn:
        conn.execute(
            "INSERT INTO valuations (id, created_at, company_name, input_json, output_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (valuation_id, created_at, company_name, json.dumps(input_dict), json.dumps(output_dict)),
        )
    return valuation_id


def get_valuation(valuation_id: str) -> Optional[dict]:
    with _get_connection() as conn:
        row = conn.execute("SELECT * FROM valuations WHERE id = ?", (valuation_id,)).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "company_name": row["company_name"],
        "input": json.loads(row["input_json"]),
        "output": json.loads(row["output_json"]),
    }


def list_valuations(limit: int = 50, offset: int = 0) -> list[dict]:
    with _get_connection() as conn:
        rows = conn.execute(
            "SELECT id, created_at, company_name FROM valuations ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [dict(row) for row in rows]


def delete_valuation(valuation_id: str) -> bool:
    with _get_connection() as conn:
        cursor = conn.execute("DELETE FROM valuations WHERE id = ?", (valuation_id,))
    return cursor.rowcount > 0


# ============================================================================
# SECTION 2 — Response schemas
#
# Nested valuation output is kept as `dict` (rather than redeclaring every
# field from the engine's dataclasses) since the engine already fully
# validates and computes that structure - the API's job is to persist and
# transport it, not re-validate it.
# ============================================================================


class ValuationSummary(BaseModel):
    id: str
    created_at: str
    company_name: Optional[str] = None


class ValuationRunResponse(BaseModel):
    id: str
    created_at: str
    company_name: Optional[str] = None
    output: dict[str, Any]


class ValuationDetailResponse(BaseModel):
    id: str
    created_at: str
    company_name: Optional[str] = None
    input: dict[str, Any]
    output: dict[str, Any]


# ============================================================================
# SECTION 3 — Routes: /valuations
# ============================================================================

valuations_router = APIRouter(prefix="/valuations", tags=["valuations"])


@valuations_router.post("", response_model=ValuationRunResponse, status_code=201)
def create_valuation(payload: ve.ValuationInput) -> ValuationRunResponse:
    """Run a full valuation and persist it. Returns the computed result."""
    try:
        result = ve.run_valuation(payload)
    except KeyError as e:
        # Raised by reference-data lookups when e.g. an unknown industry,
        # country, stage, or dropdown option text was submitted.
        raise HTTPException(status_code=400, detail=f"Unresolvable reference data: {e}")

    output_dict = dataclasses.asdict(result)
    input_dict = payload.model_dump()

    valuation_id = save_valuation(
        company_name=payload.company_profile.company_name,
        input_dict=input_dict,
        output_dict=output_dict,
    )
    record = get_valuation(valuation_id)

    return ValuationRunResponse(
        id=record["id"], created_at=record["created_at"],
        company_name=record["company_name"], output=output_dict,
    )


@valuations_router.get("", response_model=list[ValuationSummary])
def list_valuations_route(limit: int = 50, offset: int = 0) -> list[ValuationSummary]:
    return [ValuationSummary(**row) for row in list_valuations(limit=limit, offset=offset)]


@valuations_router.get("/{valuation_id}", response_model=ValuationDetailResponse)
def get_valuation_route(valuation_id: str) -> ValuationDetailResponse:
    record = get_valuation(valuation_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No valuation found with id {valuation_id!r}")
    return ValuationDetailResponse(**record)


@valuations_router.delete("/{valuation_id}", status_code=204, response_model=None)
def delete_valuation_route(valuation_id: str) -> None:
    if not delete_valuation(valuation_id):
        raise HTTPException(status_code=404, detail=f"No valuation found with id {valuation_id!r}")


@valuations_router.post("/preview", response_model=dict)
def preview_valuation(payload: ve.ValuationInput) -> dict:
    """Same computation as POST /valuations but does NOT persist anything."""
    try:
        result = ve.run_valuation(payload)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Unresolvable reference data: {e}")
    return dataclasses.asdict(result)


# ============================================================================
# SECTION 4 — Routes: /reference-data
# ============================================================================

reference_router = APIRouter(prefix="/reference-data", tags=["reference-data"])


@reference_router.get("/options")
def get_categorical_options() -> dict:
    """Valid values for country / industry / business_territory_region / company_stage."""
    return ve.categorical_options()


@reference_router.get("/industries")
def get_industries() -> list[str]:
    return sorted(ve.industry_benchmarks().keys())


@reference_router.get("/countries")
def get_countries() -> list[str]:
    return sorted(ve.country_data().keys())


@reference_router.get("/stages")
def get_stages() -> list[str]:
    return list(ve.stage_parameters().keys())


@reference_router.get("/stages/{stage}")
def get_stage_detail(stage: str) -> dict:
    try:
        return ve.get_stage_params(stage)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown stage {stage!r}")


@reference_router.get("/industries/{industry}")
def get_industry_detail(industry: str) -> dict:
    try:
        return ve.industry_benchmarks()[industry]
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown industry {industry!r}")


@reference_router.get("/countries/{country}")
def get_country_detail(country: str) -> dict:
    try:
        return ve.get_country(country)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown country {country!r}")


@reference_router.get("/scorecard-lookup")
def get_scorecard_lookup() -> dict:
    """
    criterion -> {option_text: score}. A frontend can use this to render
    each Scorecard questionnaire dropdown with its exact valid option text
    (the engine matches on exact string, so the form must offer these
    exact options - not free text).
    """
    return ve.scorecard_qualitative_lookup()


# ============================================================================
# SECTION 5 — App
# ============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Startup Valuation API",
    description=(
        "Runs the Scorecard / Venture Capital / DCF Multiples / DCF blended "
        "valuation model. Wraps the pure-Python valuation_engine package "
        "with persistence and HTTP access."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Restricted to the deployed frontend + localhost (for local dev/testing).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://snezhanatuneska-maker.github.io",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(valuations_router)
app.include_router(reference_router)


@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok"}
