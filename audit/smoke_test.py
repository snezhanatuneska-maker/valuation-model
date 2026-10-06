"""
Audit-only smoke test: exercises every API route in-process (FastAPI
TestClient, no server needed), writes the reference-case PDF to
audit/reference_case_report.pdf, and prints its text for review.

Usage:  python audit/smoke_test.py
Needs:  pip install -r requirements.txt httpx pypdf
Not wired into the app. Uses a throwaway copy of the database.
"""
import copy
import io
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "audit"))

import app as api  # noqa: E402

api.DB_PATH = Path(tempfile.mkdtemp()) / "audit.db"  # never touch data/valuations.db

from fastapi.testclient import TestClient  # noqa: E402
from pypdf import PdfReader  # noqa: E402

from recompute import REFERENCE_CASE  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}{('  -- ' + detail) if detail else ''}")


with TestClient(api.app) as client:
    r = client.get("/health")
    check("GET /health", r.status_code == 200)
    for path in ("/reference-data/options", "/reference-data/industries", "/reference-data/countries",
                 "/reference-data/stages", "/reference-data/scorecard-lookup",
                 "/reference-data/industries/Software (Entertainment)", "/reference-data/countries/Tanzania",
                 "/reference-data/stages/Startup stage"):
        r = client.get(path)
        check(f"GET {path}", r.status_code == 200)

    r = client.post("/valuations/preview", json=REFERENCE_CASE)
    check("POST /valuations/preview", r.status_code == 200,
          f"blended {r.json().get('blended_pre_money_valuation', 0):,.2f}" if r.status_code == 200 else r.text[:200])
    r = client.post("/valuations/preview/scenarios", json=REFERENCE_CASE)
    check("POST /valuations/preview/scenarios", r.status_code == 200 and len(r.json()) == 6)

    r = client.post("/auth/demo-login", json={"email": "audit@example.com"})
    check("POST /auth/demo-login", r.status_code == 200)
    r = client.post("/valuations?user_email=audit@example.com", json=REFERENCE_CASE)
    check("POST /valuations (save)", r.status_code == 201)
    vid = r.json()["id"]
    check("GET /valuations?user_email=", client.get("/valuations?user_email=audit@example.com").status_code == 200)
    check("GET /valuations/{id}", client.get(f"/valuations/{vid}").status_code == 200)
    check("GET /valuations/{id}/scenarios", client.get(f"/valuations/{vid}/scenarios").status_code == 200)
    r = client.get(f"/valuations/{vid}/report")
    check("GET /valuations/{id}/report", r.status_code == 200 and r.content[:4] == b"%PDF")
    check("DELETE /valuations/{id}", client.delete(f"/valuations/{vid}").status_code == 204)

    r = client.post("/valuations/preview/report", json=REFERENCE_CASE)
    check("POST /valuations/preview/report", r.status_code == 200 and r.content[:4] == b"%PDF")
    pdf_path = ROOT / "audit" / "reference_case_report.pdf"
    pdf_path.write_bytes(r.content)

    # Bad inputs should be rejected with 4xx, not crash with 500.
    client_nr = TestClient(api.app, raise_server_exceptions=False)
    for desc, mutate in [
        ("zero revenue", lambda c: c["financial_assumptions"].update(revenue_year1=0)),
        ("capital needed 0", lambda c: c["funding"].update(capital_needed=0)),
        ("time to exit 6", lambda c: c["company_profile"].update(planned_time_to_exit_years=6)),
        ("unknown industry", lambda c: c["company_profile"].update(industry="Nope")),
    ]:
        c = copy.deepcopy(REFERENCE_CASE)
        mutate(c)
        r = client_nr.post("/valuations/preview", json=c)
        check(f"bad input '{desc}' -> 4xx", 400 <= r.status_code < 500, f"got {r.status_code}")

print(f"\n{sum(results)}/{len(results)} checks passed")
print(f"PDF written to {pdf_path.relative_to(ROOT)}\n")
print("=== PDF text ===")
for i, page in enumerate(PdfReader(io.BytesIO(pdf_path.read_bytes())).pages, 1):
    print(f"--- page {i} ---")
    print(page.extract_text())
