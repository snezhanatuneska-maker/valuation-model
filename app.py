"""
Startup Valuation API - single-file FastAPI app.

Wraps the valuation_engine package with HTTP access and SQLite
persistence. Organized into sections below (database, schemas, routes)
rather than split across files, so the whole API is one file to read,
copy, or upload.

There are no user accounts yet: each browser has an anonymous owner ID,
and saved valuations are listed and deleted per owner ID.

Run with:  uvicorn app:app --reload
Docs at:   http://localhost:8000/docs
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
        # Migration for databases created before saved valuations had an owner.
        # Rows saved under the old demo email login keep their user_email
        # column and simply no longer appear in anyone's list.
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(valuations)")}
        if "owner_id" not in existing_cols:
            conn.execute("ALTER TABLE valuations ADD COLUMN owner_id TEXT")


def save_valuation(company_name: str, input_dict: dict, output_dict: dict, owner_id: Optional[str] = None) -> str:
    valuation_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    with _get_connection() as conn:
        conn.execute(
            "INSERT INTO valuations (id, created_at, company_name, input_json, output_json, owner_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (valuation_id, created_at, company_name, json.dumps(input_dict), json.dumps(output_dict),
             owner_id or None),
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
        "owner_id": row["owner_id"],
        "input": json.loads(row["input_json"]),
        "output": json.loads(row["output_json"]),
    }


def list_valuations(owner_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
    """Valuations saved by one owner (browser). Without an owner ID nothing is
    listed, so nobody can browse other people's valuations."""
    if not owner_id:
        return []
    with _get_connection() as conn:
        rows = conn.execute(
            "SELECT id, created_at, company_name FROM valuations "
            "WHERE owner_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (owner_id, limit, offset),
        ).fetchall()
    return [dict(row) for row in rows]


def delete_valuation(valuation_id: str, owner_id: Optional[str]) -> bool:
    """Deletes a valuation only for the owner that saved it."""
    with _get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM valuations WHERE id = ? AND owner_id IS ? ", (valuation_id, owner_id or None))
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
    owner_id: Optional[str] = None
    input: dict[str, Any]
    output: dict[str, Any]


# ============================================================================
# SECTION 3 — Routes: /valuations
# ============================================================================


def _with_valuation_date(payload: ve.ValuationInput) -> ve.ValuationInput:
    """Pins the valuation date (default: today) so a saved valuation re-runs identically later."""
    if payload.company_profile.valuation_date is None:
        payload = payload.model_copy(deep=True)
        payload.company_profile.valuation_date = date.today()
    return payload


def _run(fn, *args):
    """Runs an engine call and turns every failure into a plain-language HTTP error
    (never a bare 500), so the wizard can show the user what to fix."""
    try:
        return fn(*args)
    except ve.ValuationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except KeyError as e:
        # Unknown industry, country, stage, region or questionnaire answer.
        raise HTTPException(status_code=400, detail=f"Unrecognised input: {e.args[0] if e.args else e}")
    except (ValueError, ZeroDivisionError, OverflowError) as e:
        raise HTTPException(status_code=422, detail=f"These inputs can't be valued: {e}")

valuations_router = APIRouter(prefix="/valuations", tags=["valuations"])


@valuations_router.post("", response_model=ValuationRunResponse, status_code=201)
def create_valuation(payload: ve.ValuationInput, owner_id: Optional[str] = None) -> ValuationRunResponse:
    """Run a full valuation and persist it. Returns the computed result.

    `owner_id` is the browser's anonymous ID; the valuation then shows up in
    that browser's "Past valuations".
    """
    payload = _with_valuation_date(payload)
    result = _run(ve.run_valuation, payload)

    output_dict = dataclasses.asdict(result)
    input_dict = payload.model_dump(mode="json")

    valuation_id = save_valuation(
        company_name=payload.company_profile.company_name,
        input_dict=input_dict,
        output_dict=output_dict,
        owner_id=owner_id,
    )
    record = get_valuation(valuation_id)

    return ValuationRunResponse(
        id=record["id"], created_at=record["created_at"],
        company_name=record["company_name"], output=output_dict,
    )


@valuations_router.get("", response_model=list[ValuationSummary])
def list_valuations_route(owner_id: str = "", limit: int = 50, offset: int = 0) -> list[ValuationSummary]:
    """The valuations saved from one browser (`owner_id`)."""
    return [ValuationSummary(**row) for row in list_valuations(owner_id, limit=limit, offset=offset)]


@valuations_router.get("/{valuation_id}", response_model=ValuationDetailResponse)
def get_valuation_route(valuation_id: str) -> ValuationDetailResponse:
    record = get_valuation(valuation_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No valuation found with id {valuation_id!r}")
    return ValuationDetailResponse(**record)


@valuations_router.delete("/{valuation_id}", status_code=204, response_model=None)
def delete_valuation_route(valuation_id: str, owner_id: Optional[str] = None) -> None:
    if not delete_valuation(valuation_id, owner_id):
        raise HTTPException(status_code=404, detail=f"No valuation found with id {valuation_id!r}")


@valuations_router.post("/preview", response_model=dict)
def preview_valuation(payload: ve.ValuationInput) -> dict:
    """Same computation as POST /valuations but does NOT persist anything."""
    return dataclasses.asdict(_run(ve.run_valuation, _with_valuation_date(payload)))


class ProjectionPreviewInput(BaseModel):
    company_profile: ve.CompanyProfile
    operating_performance: ve.OperatingPerformance
    financial_assumptions: ve.FinancialAssumptions


@valuations_router.post("/preview/projections", response_model=dict)
def preview_projections(payload: ProjectionPreviewInput) -> dict:
    """The 5-year projection exactly as the engine builds it (for the wizard's
    preview table, so the browser never re-implements the math)."""
    def build():
        bench = ve._BenchmarkRecorder(payload.company_profile.industry,
                                      payload.company_profile.business_territory_region)
        proj = ve.build_projections(payload.company_profile, payload.financial_assumptions,
                                    payload.operating_performance, bench)
        return {"projections": dataclasses.asdict(proj),
                "benchmarks_used": {k: dataclasses.asdict(v) for k, v in bench.used.items()}}
    return _run(build)


def _scenarios_dict(payload: ve.ValuationInput) -> dict[str, dict]:
    scenarios = _run(ve.run_valuation_scenarios, _with_valuation_date(payload))
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
    return _scenarios_dict(_saved_input(record))


def _saved_input(record: dict) -> ve.ValuationInput:
    """Rebuilds a saved valuation's input. Records saved before the valuation date
    existed are dated by the day they were saved."""
    data = record["input"]
    data["company_profile"].setdefault("valuation_date", None)
    if data["company_profile"]["valuation_date"] is None:
        data["company_profile"]["valuation_date"] = record["created_at"][:10]
    try:
        return ve.ValuationInput(**data)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"This saved valuation's inputs are no longer valid: {e}")


@valuations_router.post("/{valuation_id}/rerun", response_model=dict)
def rerun_valuation(valuation_id: str) -> dict:
    """Re-runs a saved valuation's inputs with the current methodology (what History > View shows)."""
    record = get_valuation(valuation_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No valuation found with id {valuation_id!r}")
    payload = _saved_input(record)
    return {"input": payload.model_dump(mode="json"),
            "output": dataclasses.asdict(_run(ve.run_valuation, payload))}


def _pdf_response(payload: ve.ValuationInput, output_dict: dict) -> Response:
    """Builds the branded PDF report and wraps it as a one-click file download."""
    input_dict = payload.model_dump(mode="json")
    try:
        benchmark = ve.resolved_industry_benchmarks(payload.company_profile.industry)
    except Exception:
        benchmark = {}
    try:
        scenarios_dict = {
            label: dataclasses.asdict(out) for label, out in ve.run_valuation_scenarios(payload).items()
        }
    except Exception:  # scenarios are optional extras; the report still builds without them
        scenarios_dict = {}
    stage_params = ve.stage_parameters().get(payload.company_profile.company_stage)
    pdf_bytes = pdf_report.build_pdf_bytes(input_dict, output_dict, benchmark, scenarios_dict,
                                           ve.data_sources(), stage_params)

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
    payload = _with_valuation_date(payload)
    result = _run(ve.run_valuation, payload)
    return _pdf_response(payload, dataclasses.asdict(result))


@valuations_router.get("/{valuation_id}/report")
def get_valuation_report(valuation_id: str) -> Response:
    """Re-runs a previously saved valuation's inputs and returns the branded PDF report."""
    record = get_valuation(valuation_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No valuation found with id {valuation_id!r}")
    payload = _saved_input(record)
    result = _run(ve.run_valuation, payload)
    return _pdf_response(payload, dataclasses.asdict(result))


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


@reference_router.get("/stage-descriptions")
def get_stage_descriptions() -> dict:
    return ve.stage_descriptions()


@reference_router.get("/stages/{stage}")
def get_stage_detail(stage: str) -> dict:
    try:
        return ve.get_stage_params(stage)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown stage {stage!r}")


@reference_router.get("/industries/{industry}")
def get_industry_detail(industry: str) -> dict:
    """Benchmarks with the engine's fallbacks applied (no raw "NA" values)."""
    try:
        return ve.resolved_industry_benchmarks(industry)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown industry {industry!r}")


@reference_router.get("/countries/{country}")
def get_country_detail(country: str) -> dict:
    try:
        return ve.get_country(country)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown country {country!r}")


@reference_router.get("/sources")
def get_sources() -> dict:
    """Where every benchmark and assumption comes from, and the data date."""
    return ve.data_sources()


@reference_router.get("/default-region/{country}")
def get_default_region(country: str) -> dict:
    try:
        return {"region": ve.default_region_for_country(country)}
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

def _readable_validation_errors(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = [str(x) for x in err.get("loc", []) if x not in ("body",)]
        field = loc[-1].replace("_", " ") if loc else "input"
        msg = err.get("msg", "is invalid").removeprefix("Value error, ")
        parts.append(f"{field}: {msg}")
    return "Please check these inputs - " + "; ".join(parts)


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": _readable_validation_errors(exc)})


app.include_router(valuations_router)
app.include_router(reference_router)


@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok"}
