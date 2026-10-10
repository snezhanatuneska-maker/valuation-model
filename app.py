"""
Startup Valuation API - single-file FastAPI app.

Wraps the valuation_engine package with HTTP access and SQLite
persistence. Organized into sections below (database, schemas, routes)
rather than split across files, so the whole API is one file to read,
copy, or upload.

Demo mode (the default): nothing is ever written to disk. The wizard only
uses the /valuations/preview* routes, which compute and return results
without storing them, and the routes that save, list, read or delete
valuations answer 404. Set VALUATION_MODEL_STORE_VALUATIONS=1 to switch saving
back on (there are no user accounts yet: each browser has an anonymous
owner ID, and saved valuations are listed and deleted per owner ID).

Run with:  uvicorn app:app --reload
Wizard at: http://localhost:8000  (index.html, served by this app)
Docs at:   http://localhost:8000/docs
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import sqlite3
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
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

# Off unless explicitly switched on: the public demo stores no inputs at all.
STORE_VALUATIONS = os.environ.get("VALUATION_MODEL_STORE_VALUATIONS") == "1"


def _require_storage() -> None:
    """Guards every route that saves or reads stored valuations."""
    if not STORE_VALUATIONS:
        raise HTTPException(status_code=404, detail="Saving valuations is switched off in this demo; "
                                                    "nothing you enter is stored.")

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
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _get_connection() as conn:
        conn.executescript(_SCHEMA)
        # Migration for databases created before saved valuations had an owner.
        # Rows saved under the old demo email login keep their user_email
        # column and simply no longer appear in anyone's list.
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(valuations)")}
        if "owner_id" not in existing_cols:
            conn.execute("ALTER TABLE valuations ADD COLUMN owner_id TEXT")
        # The revenue scenarios are stored with the result, so a saved report never changes later.
        if "scenarios_json" not in existing_cols:
            conn.execute("ALTER TABLE valuations ADD COLUMN scenarios_json TEXT")


def save_valuation(company_name: str, input_dict: dict, output_dict: dict, owner_id: Optional[str] = None,
                   scenarios_dict: Optional[dict] = None) -> str:
    valuation_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    with _get_connection() as conn:
        conn.execute(
            "INSERT INTO valuations (id, created_at, company_name, input_json, output_json, owner_id, scenarios_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (valuation_id, created_at, company_name, json.dumps(input_dict), json.dumps(output_dict),
             owner_id or None, None if scenarios_dict is None else json.dumps(scenarios_dict)),
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
        # None for valuations saved before the scenarios were stored with them.
        "scenarios": json.loads(row["scenarios_json"]) if row["scenarios_json"] else None,
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
        raise HTTPException(status_code=400, detail=ve._t("Unrecognised input: ", "Unbekannte Angabe: ")
                            + str(e.args[0] if e.args else e))
    except (ValueError, ZeroDivisionError, OverflowError) as e:
        raise HTTPException(status_code=422, detail=ve._t("These inputs can't be valued: ",
                                                          "Mit diesen Angaben ist keine Bewertung möglich: ") + str(e))

valuations_router = APIRouter(prefix="/valuations", tags=["valuations"])


@valuations_router.post("", response_model=ValuationRunResponse, status_code=201, dependencies=[Depends(_require_storage)])
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
        scenarios_dict=_scenarios_dict(payload),
    )
    record = get_valuation(valuation_id)

    return ValuationRunResponse(
        id=record["id"], created_at=record["created_at"],
        company_name=record["company_name"], output=output_dict,
    )


@valuations_router.get("", response_model=list[ValuationSummary], dependencies=[Depends(_require_storage)])
def list_valuations_route(owner_id: str = "", limit: int = 50, offset: int = 0) -> list[ValuationSummary]:
    """The valuations saved from one browser (`owner_id`)."""
    return [ValuationSummary(**row) for row in list_valuations(owner_id, limit=limit, offset=offset)]


@valuations_router.get("/{valuation_id}", response_model=ValuationDetailResponse, dependencies=[Depends(_require_storage)])
def get_valuation_route(valuation_id: str) -> ValuationDetailResponse:
    record = get_valuation(valuation_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No valuation found with id {valuation_id!r}")
    return ValuationDetailResponse(**record)


@valuations_router.delete("/{valuation_id}", status_code=204, response_model=None, dependencies=[Depends(_require_storage)])
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


@valuations_router.get("/{valuation_id}/scenarios", response_model=dict, dependencies=[Depends(_require_storage)])
def get_valuation_scenarios(valuation_id: str) -> dict:
    """The revenue scenarios saved with a valuation (valuations saved before scenarios were stored are re-run)."""
    record = get_valuation(valuation_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No valuation found with id {valuation_id!r}")
    if record["scenarios"] is not None:
        return record["scenarios"]
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


@valuations_router.post("/{valuation_id}/rerun", response_model=dict, dependencies=[Depends(_require_storage)])
def rerun_valuation(valuation_id: str) -> dict:
    """Re-runs a saved valuation's inputs with the current methodology (what History > View shows)."""
    record = get_valuation(valuation_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No valuation found with id {valuation_id!r}")
    payload = _saved_input(record)
    return {"input": payload.model_dump(mode="json"),
            "output": dataclasses.asdict(_run(ve.run_valuation, payload))}


def _pdf_response(payload: ve.ValuationInput, output_dict: dict, scenarios_dict: Optional[dict] = None) -> Response:
    """Builds the branded PDF report and wraps it as a one-click file download."""
    input_dict = payload.model_dump(mode="json")
    try:
        benchmark = ve.resolved_industry_benchmarks(payload.company_profile.industry)
    except Exception:
        benchmark = {}
    if scenarios_dict is None:
        try:
            scenarios_dict = {
                label: dataclasses.asdict(out) for label, out in ve.run_valuation_scenarios(payload).items()
            }
        except Exception:  # scenarios are optional extras; the report still builds without them
            scenarios_dict = {}
    stage_params = ve.stage_parameters().get(payload.company_profile.company_stage)
    pdf_bytes = pdf_report.build_pdf_bytes(input_dict, output_dict, benchmark, scenarios_dict,
                                           ve.data_sources(), stage_params,
                                           ve.country_specific(payload.company_profile.country))

    # HTTP headers only carry plain ASCII, so umlauts are spelled out (ü -> ue) and other symbols dropped,
    # the same way the web page names the download.
    company_name = payload.company_profile.company_name or "valuation"
    for umlaut, plain in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"), ("ß", "ss")):
        company_name = company_name.replace(umlaut, plain)
    safe_name = "".join(c for c in company_name if (c.isascii() and c.isalnum()) or c in (" ", "-", "_"))
    safe_name = "-".join(safe_name.split())[:80].strip("-") or "valuation"
    filename = f"{safe_name}-{ve._t('Valuation-Report', 'Bewertungsbericht')}.pdf"

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


@valuations_router.get("/{valuation_id}/report", dependencies=[Depends(_require_storage)])
def get_valuation_report(valuation_id: str) -> Response:
    """The branded PDF report of a saved valuation, with the figures as they were saved (a later data
    refresh or method change doesn't alter them; POST /{id}/rerun recalculates with today's data).
    Valuations saved before the scenarios were stored with them are re-run."""
    record = get_valuation(valuation_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No valuation found with id {valuation_id!r}")
    payload = _saved_input(record)
    if record["scenarios"] is not None:
        return _pdf_response(payload, record["output"], record["scenarios"])
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
    return sorted(ve.country_data().keys(), key=ve.name_sort_key)


@reference_router.get("/stages")
def get_stages() -> list[str]:
    return list(ve.stage_parameters().keys())


@reference_router.get("/stage-descriptions")
def get_stage_descriptions() -> dict:
    """One-line definition of each stage (German with ?lang=de)."""
    return ve.stage_descriptions()


@reference_router.get("/labels")
def get_labels() -> dict:
    """Display names for stages, regions, countries and questionnaire answers (German with ?lang=de).
    The wizard sends back the English values; only what it shows changes."""
    return ve.ui_labels()


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
    """Country risk and tax data, plus (Germany) the country's own risk-free rate,
    tax schedule and stage benchmarks under "country_specific"."""
    try:
        detail = dict(ve.get_country(country))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown country {country!r}")
    specific = ve.country_specific(country)
    if specific:
        detail["country_specific"] = specific
    return detail


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
    if STORE_VALUATIONS:
        init_db()
    yield


app = FastAPI(
    title="Startup Valuation API",
    description=(
        "Runs the Scorecard / Venture Capital / Comparables (EV/EBITDA multiple) / DCF "
        "blended valuation model. Wraps the pure-Python valuation_engine package "
        "with persistence and HTTP access."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Restricted to the deployed frontend + localhost (for local dev/testing). "null" is the origin browsers send
# for a page opened straight from disk (index.html double-clicked); the API stores nothing and needs no login,
# so accepting it opens nothing that a plain HTTP call couldn't already reach.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://snezhanatuneska-maker.github.io",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "null",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

class LanguageMiddleware:
    """Runs each request in the language of its ?lang= parameter (en, the default, or de), so every message
    the engine, the API and the PDF write comes out in that language."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        lang = "en"
        if scope["type"] == "http":
            lang = parse_qs(scope.get("query_string", b"").decode("latin-1")).get("lang", ["en"])[0]
        with ve.language(lang):
            await self.app(scope, receive, send)


app.add_middleware(LanguageMiddleware)

# German names of the input fields, for validation messages.
_FIELD_NAMES_DE = {
    "company_name": "Firmenname", "country": "Land", "num_founders": "Anzahl der Gründer",
    "num_employees": "Anzahl der Mitarbeitenden", "year_of_incorporation": "Gründungsjahr", "company_stage": "Phase",
    "committed_capital": "zugesagtes Kapital", "industry": "Branche", "business_territory_region": "Region",
    "planned_time_to_exit_years": "geplante Jahre bis zum Exit", "valuation_date": "Bewertungsdatum",
    "dcf_tax_rate_override": "Steuersatz", "trade_tax_hebesatz": "Hebesatz",
    "benchmark_pre_money_override": "eigener Vergleichswert",
    "current_revenue_last_12_months": "Umsatz der letzten 12 Monate", "current_ebitda": "EBITDA der letzten 12 Monate",
    "cash_available": "liquide Mittel", "current_ppe_value": "Sachanlagen",
    "annual_recurring_revenue": "jährlich wiederkehrender Umsatz (ARR)", "revenue_year1": "Umsatz in Jahr 1",
    "revenue_by_year": "Umsatz pro Jahr",
    "revenue_growth_rates": "Wachstumsraten", "capex_by_year": "Investitionen",
    "existing_debt_balance": "bestehende Schulden", "target_ebitda_margin_override": "Ziel-EBITDA-Marge",
    "ownership_pct": "Beteiligung", "name": "Name", "capital_needed": "Kapitalbedarf",
    "use_of_funds": "Mittelverwendung", "number_of_existing_shares": "Anzahl bestehender Anteile",
}
# German wording of the validation messages (pydantic's and the engine's own).
_MESSAGES_DE = [
    (r"^should be greater than or equal to (.+)$", r"muss mindestens \1 sein"),
    (r"^should be greater than (.+)$", r"muss größer als \1 sein"),
    (r"^should be less than or equal to (.+)$", r"darf höchstens \1 sein"),
    (r"^should be less than (.+)$", r"muss kleiner als \1 sein"),
    (r"^is required$", "fehlt"),
    (r"^should be a valid number.*$", "muss eine Zahl sein"),
    (r"^should be a finite number$", "muss eine endliche Zahl sein"),
    (r"^should be a valid integer.*$", "muss eine ganze Zahl sein"),
    (r"^should be a valid date.*$", "muss ein gültiges Datum sein"),
    (r"^should be a valid string$", "muss ein Text sein"),
    (r"^String should have at least 1 character$", "darf nicht leer sein"),
    (r"^revenue_growth_rates must have exactly 4 values.*$", "es müssen genau 4 Werte sein (für J2 bis J5)"),
    (r"^each growth rate must be above -100% and at most 1000%$",
     "jede Wachstumsrate muss über -100 % und höchstens bei 1000 % liegen"),
    (r"^revenue_by_year must have exactly 5 values.*$", "es müssen genau 5 Werte sein (für J1 bis J5)"),
    (r"^revenue can't be negative$", "der Umsatz kann nicht negativ sein"),
    (r"^revenue can be at most (.+)$", r"der Umsatz darf höchstens \1 betragen"),
    (r"^enter revenue for at least one of the five years.*$",
     "geben Sie für mindestens eines der fünf Jahre einen Umsatz ein; die Cashflow-Methoden brauchen einen Umsatzplan"),
    (r"^once sales have started, every later year needs revenue above 0$",
     "nach dem Umsatzbeginn braucht jedes weitere Jahr einen Umsatz über 0"),
    (r"^capex_by_year must have exactly 5 values.*$", "es müssen genau 5 Werte sein (für J1 bis J5)"),
    (r"^capex can't be negative$", "Investitionen können nicht negativ sein"),
    (r"^capex can be at most (.+)$", r"Investitionen dürfen höchstens \1 betragen"),
    (r"^use-of-funds amounts can't be negative$", "Beträge der Mittelverwendung können nicht negativ sein"),
    (r"^use-of-funds amounts can be at most (.+)$", r"Beträge der Mittelverwendung dürfen höchstens \1 betragen"),
    (r"^with no revenue, EBITDA can't be positive.*$",
     "ohne Umsatz kann das EBITDA nicht positiv sein; geben Sie 0 oder Ihren operativen Verlust als negative Zahl ein"),
    (r"^EBITDA can't be larger than revenue.*$",
     "das EBITDA kann nicht größer als der Umsatz sein (es ist, was vom Umsatz nach den operativen Kosten bleibt)"),
]


def _readable_validation_errors(exc: RequestValidationError) -> str:
    german = ve.current_language() == "de"
    parts = []
    for err in exc.errors():
        loc = [x for x in err.get("loc", []) if x != "body"]
        names = [x for x in loc if isinstance(x, str)]
        key = names[-1] if names else None
        field = (_FIELD_NAMES_DE.get(key, key.replace("_", " ")) if german else key.replace("_", " ")) if key \
            else ve._t("input", "Eingabe")
        if loc and isinstance(loc[-1], int):  # an item in a list, e.g. the 2nd growth rate
            field += ve._t(f" (item {loc[-1] + 1})", f" (Eintrag {loc[-1] + 1})")
        msg = err.get("msg", "is invalid").removeprefix("Value error, ")
        msg = msg.replace("Input should be", "should be").replace("Field required", "is required")
        if german:
            for pattern, repl in _MESSAGES_DE:
                if re.match(pattern, msg):
                    msg = re.sub(pattern, repl, msg)
                    break
        msg = re.sub(r"\b\d{5,}\b", lambda m: ve._num(int(m.group())), msg)  # 10000000 -> 10,000,000 / 10.000.000
        if german:  # amounts the engine already wrote with English separators
            msg = re.sub(r"\d{1,3}(?:,\d{3})+", lambda m: m.group().replace(",", "."), msg)
        parts.append(f"{field}: {msg}")
    text = "; ".join(parts)
    return ve._t("Please check these inputs: ", "Bitte prüfen Sie diese Angaben: ") + text + (
        "" if text.endswith(".") else ".")


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": _readable_validation_errors(exc)})


app.include_router(valuations_router)
app.include_router(reference_router)


@app.get("/", include_in_schema=False)
def wizard() -> FileResponse:
    """The web wizard, so a local run needs only `uvicorn app:app` and http://localhost:8000."""
    return FileResponse(Path(__file__).resolve().parent / "index.html", media_type="text/html")


@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok"}
