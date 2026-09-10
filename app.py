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

from fastapi import APIRouter, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import valuation_engine as ve
import report as pdf_report

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
CREATE TABLE IF NOT EXISTS users (
    email TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
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
        conn.executescript(_SCHEMA)
        # Migration for databases created before accounts existed.
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(valuations)")}
        if "user_email" not in existing_cols:
            conn.execute("ALTER TABLE valuations ADD COLUMN user_email TEXT")


# ----------------------------------------------------------------------------
# Accounts (DEMO ONLY)
#
# This is real, persistent plumbing (a genuine `users` table, valuations are
# genuinely tied to an email) but the "authentication" itself is fake: there
# is no password check anywhere below. Any email is accepted and either
# creates or reuses that user row. This lets the rest of the app (saved
# history scoped per-user) behave like the real thing while login stays a
# one-line swap away from something real (e.g. verifying a password hash or
# a magic-link token) later.
# ----------------------------------------------------------------------------


def upsert_demo_user(email: str) -> dict:
    email = email.strip().lower()
    with _get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
            created_at = datetime.now(timezone.utc).isoformat()
            conn.execute("INSERT INTO users (email, created_at) VALUES (?, ?)", (email, created_at))
            return {"email": email, "created_at": created_at}
        return {"email": row["email"], "created_at": row["created_at"]}


def save_valuation(company_name: str, input_dict: dict, output_dict: dict, user_email: Optional[str] = None) -> str:
    valuation_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    with _get_connection() as conn:
        conn.execute(
            "INSERT INTO valuations (id, created_at, company_name, input_json, output_json, user_email) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (valuation_id, created_at, company_name, json.dumps(input_dict), json.dumps(output_dict),
             user_email.strip().lower() if user_email else None),
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
        "user_email": row["user_email"],
        "input": json.loads(row["input_json"]),
        "output": json.loads(row["output_json"]),
    }


def list_valuations(limit: int = 50, offset: int = 0, user_email: Optional[str] = None) -> list[dict]:
    with _get_connection() as conn:
        if user_email:
            rows = conn.execute(
                "SELECT id, created_at, company_name, user_email FROM valuations "
                "WHERE user_email = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (user_email.strip().lower(), limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, created_at, company_name, user_email FROM valuations "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
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
    user_email: Optional[str] = None


class ValuationRunResponse(BaseModel):
    id: str
    created_at: str
    company_name: Optional[str] = None
    user_email: Optional[str] = None
    output: dict[str, Any]


class ValuationDetailResponse(BaseModel):
    id: str
    created_at: str
    company_name: Optional[str] = None
    user_email: Optional[str] = None
    input: dict[str, Any]
    output: dict[str, Any]


class DemoUser(BaseModel):
    email: str
    created_at: str


# ============================================================================
# SECTION 3 — Routes: /valuations
# ============================================================================

valuations_router = APIRouter(prefix="/valuations", tags=["valuations"])


@valuations_router.post("", response_model=ValuationRunResponse, status_code=201)
def create_valuation(payload: ve.ValuationInput, user_email: Optional[str] = None) -> ValuationRunResponse:
    """Run a full valuation and persist it. Returns the computed result.

    `user_email` is optional and unverified (see the accounts section above) -
    when provided, the valuation is tagged with it so it shows up in that
    user's history.
    """
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
        user_email=user_email,
    )
    record = get_valuation(valuation_id)

    return ValuationRunResponse(
        id=record["id"], created_at=record["created_at"],
        company_name=record["company_name"], user_email=record["user_email"], output=output_dict,
    )


@valuations_router.get("", response_model=list[ValuationSummary])
def list_valuations_route(limit: int = 50, offset: int = 0, user_email: Optional[str] = None) -> list[ValuationSummary]:
    """If `user_email` is given, only that user's saved valuations are returned."""
    return [ValuationSummary(**row) for row in list_valuations(limit=limit, offset=offset, user_email=user_email)]


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


def _scenarios_dict(payload: ve.ValuationInput) -> dict[str, dict]:
    try:
        scenarios = ve.run_valuation_scenarios(payload)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Unresolvable reference data: {e}")
    return {label: dataclasses.asdict(output) for label, output in scenarios.items()}


@valuations_router.post("/preview/scenarios", response_model=dict)
def preview_valuation_scenarios(payload: ve.ValuationInput) -> dict:
    """
    Re-runs the valuation at several Year-1 revenue multipliers (80%-130%)
    without persisting anything. Returns {"80%": <full output>, "90%": ..., ...}
    so the frontend can show how all four methods (and the blend) move
    together, not just one.
    """
    return _scenarios_dict(payload)


@valuations_router.get("/{valuation_id}/scenarios", response_model=dict)
def get_valuation_scenarios(valuation_id: str) -> dict:
    """Same as POST /valuations/preview/scenarios, but re-runs a previously saved valuation's inputs."""
    record = get_valuation(valuation_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No valuation found with id {valuation_id!r}")
    payload = ve.ValuationInput(**record["input"])
    return _scenarios_dict(payload)


def _pdf_response(payload: ve.ValuationInput, output_dict: dict) -> Response:
    """Builds the branded PDF report and wraps it as a one-click file download."""
    input_dict = payload.model_dump()
    try:
        benchmark = ve.industry_benchmarks().get(payload.company_profile.industry, {})
    except Exception:
        benchmark = {}
    try:
        scenarios_dict = {
            label: dataclasses.asdict(out) for label, out in ve.run_valuation_scenarios(payload).items()
        }
    except Exception:
        scenarios_dict = {}
    pdf_bytes = pdf_report.build_pdf_bytes(input_dict, output_dict, benchmark, scenarios_dict)

    company_name = payload.company_profile.company_name or "valuation"
    safe_name = "".join(c if c.isalnum() or c in (" ", "-", "_") else "" for c in company_name).strip() or "valuation"
    filename = f"{safe_name} - Valuation Report.pdf".replace(" ", "-")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@valuations_router.post("/preview/report")
def preview_valuation_report(payload: ve.ValuationInput) -> Response:
    """Runs a valuation (without persisting) and returns the branded PDF report directly."""
    try:
        result = ve.run_valuation(payload)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Unresolvable reference data: {e}")
    return _pdf_response(payload, dataclasses.asdict(result))


@valuations_router.get("/{valuation_id}/report")
def get_valuation_report(valuation_id: str) -> Response:
    """Re-runs a previously saved valuation's inputs and returns the branded PDF report."""
    record = get_valuation(valuation_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No valuation found with id {valuation_id!r}")
    payload = ve.ValuationInput(**record["input"])
    try:
        result = ve.run_valuation(payload)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Unresolvable reference data: {e}")
    return _pdf_response(payload, dataclasses.asdict(result))


# ============================================================================
# SECTION 4 — Routes: /auth (DEMO ONLY - see note above upsert_demo_user)
# ============================================================================

auth_router = APIRouter(prefix="/auth", tags=["auth"])


class DemoLoginRequest(BaseModel):
    email: str


@auth_router.post("/demo-login", response_model=DemoUser)
def demo_login(payload: DemoLoginRequest) -> DemoUser:
    """
    Accepts any non-empty email and no password check whatsoever - this is a
    stand-in for real authentication (password hash + verification, or a
    magic-link/OTP flow), just enough to give each "account" a persistent
    identity so saved valuations can be scoped per-user. Swap this one
    function out first when real login is needed.
    """
    email = (payload.email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Please provide a valid-looking email address.")
    return DemoUser(**upsert_demo_user(email))


# ============================================================================
# SECTION 5 — Routes: /reference-data
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
# SECTION 6 — App
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
app.include_router(auth_router)
app.include_router(reference_router)


@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok"}
