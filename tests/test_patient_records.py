"""Patient routes with synthetic FHIR responses: no .env, services, DB or model."""

import asyncio
import copy
import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import patient_records as records

ROOT = Path(__file__).resolve().parents[1]


def patient(identifier="patient-1", name="测试患者"):
    return {"resourceType": "Patient", "id": identifier,
            "name": [{"text": name}], "birthDate": "1980-01-02", "gender": "female"}


def bundle(*resources, more=False):
    result = {"resourceType": "Bundle", "type": "searchset",
              "entry": [{"resource": resource} for resource in resources]}
    if more:
        result["link"] = [{"relation": "next", "url": "https://untrusted.invalid/page?secret=x"}]
    return result


def http_error(status=503):
    request = httpx.Request("GET", "https://upstream.invalid/?access_token=do-not-leak")
    return httpx.HTTPStatusError("private credentials do-not-leak", request=request,
                                response=httpx.Response(status, request=request))


class FHIRClient:
    """Synthetic test double named to exercise production source labeling."""

    def __init__(self):
        self.calls = []
        self.results = {"Patient": bundle(), "Patient/patient-1": patient(),
                        "MedicationRequest": bundle(), "AllergyIntolerance": bundle(),
                        "Observation": bundle(), "Condition": bundle(), "Encounter": bundle()}

    async def get(self, path, params=None):
        self.calls.append((path, params))
        value = self.results.get(path, http_error(404))
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return await value()
        return copy.deepcopy(value)

    async def post(self, *args, **kwargs):
        pytest.fail("Patient workspace must never write to FHIR")

    create_resource = post


@pytest.fixture
def setup(monkeypatch, tmp_path):
    # app.config resolves .env against cwd. Never import it from the project cwd.
    monkeypatch.chdir(tmp_path)
    from app.config import settings
    monkeypatch.setattr(settings, "api_keys", "fixture-patient-key")
    monkeypatch.setattr(settings, "openemr_fhir_url", "https://ehr.example/fhir")
    name = "app.api._patients_test"
    spec = importlib.util.spec_from_file_location(name, ROOT / "app/api/patients.py")
    routes = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, routes)
    spec.loader.exec_module(routes)
    fhir = FHIRClient()
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[routes.get_patient_client] = lambda: fhir
    with TestClient(app) as client:
        client.headers["X-API-Key"] = "fixture-patient-key"
        yield client, fhir


def test_candidates_keep_namesakes_and_do_not_invent_total(setup):
    client, fhir = setup
    fhir.results["Patient"] = bundle(patient("one"), patient("two"), patient("three"))
    response = client.get("/api/patients", params={"q": "测试患者", "limit": 2})
    data = response.json()
    assert response.status_code == 200
    assert [p["id"] for p in data["items"]] == ["one", "two"]
    assert data["has_more"] is True and "total" not in data
    assert data["source"] == "openemr"
    assert fhir.calls == [("Patient", {"name": "测试患者", "_count": 3})]
    assert response.headers["cache-control"] == "no-store"


def test_exact_id_and_name_fallback(setup):
    client, fhir = setup
    assert client.get("/api/patients?q=patient-1").json()["items"][0]["id"] == "patient-1"
    assert fhir.calls == [("Patient/patient-1", None)]
    fhir.results["Patient"] = bundle(patient("one", "Alex"), patient("two", "Alex"))
    data = client.get("/api/patients?q=Alex").json()
    assert len(data["items"]) == 2
    assert fhir.calls[-2:] == [("Patient/Alex", None), ("Patient", {"name": "Alex", "_count": 21})]


def test_name_probe_http400_falls_back_without_masking_direct_detail_failure(setup):
    client, fhir = setup
    fhir.results["Patient/John"] = http_error(400)
    fhir.results["Patient"] = bundle(patient("one", "John"), patient("two", "John"))
    response = client.get("/api/patients?q=John")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == ["one", "two"]
    assert fhir.calls == [("Patient/John", None), ("Patient", {"name": "John", "_count": 21})]
    response = client.get("/api/patients/John")
    assert response.status_code == 503 and "do-not-leak" not in response.text


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_name_probe_does_not_swallow_auth_or_server_failures(setup, status):
    client, fhir = setup
    fhir.results["Patient/John"] = http_error(status)
    response = client.get("/api/patients?q=John")
    assert response.status_code == 503
    assert fhir.calls == [("Patient/John", None)]
    assert response.headers["cache-control"] == "no-store"
    assert "do-not-leak" not in response.text


def test_no_matches_and_next_link_are_explicit(setup):
    client, fhir = setup
    assert client.get("/api/patients?q=不存在").json()["items"] == []
    fhir.results["Patient"] = bundle(patient(), more=True)
    data = client.get("/api/patients").json()
    assert data["has_more"] is True
    assert all(not path.startswith("http") for path, _ in fhir.calls)
    assert "untrusted.invalid" not in str(data)


def test_fhir_query_operators_are_literal(setup):
    client, fhir = setup
    assert client.get("/api/patients", params={"q": "姓,名|其他$值\\尾"}).status_code == 200
    assert fhir.calls[-1][1]["name"] == "姓\\,名\\|其他\\$值\\\\尾"


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 51}, {"limit": "x"},
                                   {"q": "x" * 101}, {"q": "first\nsecond"}, {"q": "\x00"}])
def test_invalid_query_never_reads_source(setup, params):
    client, fhir = setup
    response = client.get("/api/patients", params=params)
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert not fhir.calls


@pytest.mark.parametrize("path", ["/api/patients", "/api/patients/patient-1",
                                  "/api/patients/patient-1/medications"])
def test_existing_auth_is_required_for_every_endpoint(setup, path):
    client, fhir = setup
    client.headers.pop("X-API-Key")
    response = client.get(path)
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"
    assert not fhir.calls


def test_base_patient_is_selected_by_id_and_exposes_only_contract_fields(setup):
    client, fhir = setup
    fhir.results["Patient/patient-1"].update({"telecom": [{"system": "phone", "value": "fixture-phone"}],
                                           "extension": [{"private": "omit-me"}]})
    data = client.get("/api/patients/patient-1").json()
    assert data == {"id": "patient-1", "name": "测试患者", "birth_date": "1980-01-02",
                    "gender": "female", "phone": "fixture-phone", "email": None,
                    "address": None, "source": "openemr"}
    fhir.results["Patient/patient-1"] = patient("wrong-person")
    assert client.get("/api/patients/patient-1").status_code == 503


def test_missing_patient_and_invalid_id_cannot_look_like_empty_records(setup):
    client, fhir = setup
    assert client.get("/api/patients/missing").status_code == 404
    assert client.get("/api/patients/missing/labs").status_code == 404
    before = len(fhir.calls)
    assert client.get("/api/patients/bad%20id/labs").status_code == 422
    assert client.get("/api/patients/patient-1/unknown").status_code == 404
    assert len(fhir.calls) == before


def test_partial_medication_failure_preserves_successful_allergies(setup):
    client, fhir = setup
    fhir.results["MedicationRequest"] = http_error()
    fhir.results["AllergyIntolerance"] = bundle({"resourceType": "AllergyIntolerance", "id": "a-1",
        "patient": {"reference": "Patient/patient-1"}, "code": {"text": "示例过敏原"},
        "criticality": "high", "reaction": [{"manifestation": [{"text": "示例反应"}]}]})
    response = client.get("/api/patients/patient-1/medications")
    data = response.json()
    assert response.status_code == 200
    assert data["parts"]["medications"]["status"] == "unavailable"
    assert data["parts"]["allergies"]["status"] == "ok"
    assert data["parts"]["allergies"]["items"][0]["reactions"] == ["示例反应"]
    assert "do-not-leak" not in response.text


def test_successful_empty_sources_remain_distinct_from_failures(setup):
    client, fhir = setup
    data = client.get("/api/patients/patient-1/history").json()
    assert all(part == {"status": "ok", "items": [], "has_more": False} for part in data["parts"].values())
    fhir.results["Encounter"] = {"resourceType": "OperationOutcome"}
    data = client.get("/api/patients/patient-1/history").json()
    assert data["parts"]["conditions"]["status"] == "ok"
    assert data["parts"]["encounters"]["status"] == "unavailable"


def test_medications_and_encounters_keep_original_record_values(setup):
    client, fhir = setup
    fhir.results["MedicationRequest"] = bundle({"resourceType": "MedicationRequest", "id": "m-1",
        "subject": {"reference": "Patient/patient-1"}, "status": "active", "intent": "order",
        "medicationCodeableConcept": {"text": "示例药品"}, "dosageInstruction": [{"text": "原始登记用法"}]})
    fhir.results["Encounter"] = bundle({"resourceType": "Encounter", "id": "e-1",
        "subject": {"reference": "Patient/patient-1"}, "status": "finished",
        "type": [{"text": "复诊"}], "period": {"start": "2025-02-03"},
        "reasonCode": [{"text": "原始登记原因"}]}, more=True)
    med = client.get("/api/patients/patient-1/medications").json()["parts"]["medications"]["items"][0]
    assert med["medication"] == "示例药品" and med["dosage"] == "原始登记用法"
    encounter = client.get("/api/patients/patient-1/history").json()["parts"]["encounters"]
    assert encounter["has_more"] is True
    assert encounter["items"][0] == {"id": "e-1", "status": "finished", "type": "复诊",
                                     "start": "2025-02-03", "end": None, "reason": "原始登记原因"}


def test_lab_values_components_and_existing_interpretations_are_preserved(setup):
    client, fhir = setup
    def observation(identifier, category, **extra):
        return {"resourceType": "Observation", "id": identifier,
                "category": [{"coding": [{"code": category}]}],
                "subject": {"reference": "Patient/patient-1"},
                "code": {"text": "检验样例"}, **extra}
    fhir.results["Observation"] = bundle(
        observation("zero", "laboratory", valueQuantity={"value": 0, "unit": "mmol/L"},
                    referenceRange=[{"low": {"value": 0, "unit": "mmol/L"}, "high": {"value": 2}}],
                    interpretation=[{"text": "源系统标记"}]),
        observation("qualitative", "laboratory", valueCodeableConcept={"text": "阴性"}),
        observation("panel", "laboratory", component=[{"code": {"text": "分项"}, "valueString": "已测"}]),
        observation("vital", "vital-signs", valueQuantity={"value": 80}))
    part = client.get("/api/patients/patient-1/labs").json()["parts"]["observations"]
    assert part["status"] == "ok"
    assert [item["id"] for item in part["items"]] == ["zero", "qualitative", "panel"]
    assert part["items"][0]["value"] == 0
    assert part["items"][0]["reference_range"] == "≥ 0 / ≤ 2 mmol/L"
    assert part["items"][0]["interpretation"] == "源系统标记"
    assert part["items"][1]["value"] == "阴性"
    assert part["items"][2]["components"][0]["value"] == "已测"
    assert fhir.calls[-1][1]["category"] == "laboratory"


@pytest.mark.parametrize("association", [None, {}, {"reference": None}, {"reference": 17},
    {"reference": ""}, {"reference": "Patient/someone-else"},
    {"reference": "Practitioner/Patient/patient-1"},
    {"reference": "urn:forged/Patient/patient-1"},
    {"reference": "https://other.example/fhir/Patient/patient-1"},
    {"reference": "https://ehr.example/fhir/Observation/forged/Patient/patient-1"},
    {"reference": "https://ehr.example/fhir/Patient/patient-1?x=1"},
    {"reference": "https://ehr.example/fhir/Patient/patient-1#fragment"},
    {"reference": "https://user:secret@ehr.example/fhir/Patient/patient-1"}])
def test_missing_or_foreign_patient_associations_are_not_rendered(setup, association):
    client, fhir = setup
    fhir.results["Condition"] = bundle({"resourceType": "Condition", "id": "c-1",
                                       "subject": association})
    part = client.get("/api/patients/patient-1/history").json()["parts"]["conditions"]
    assert part["status"] == "unavailable" and part["items"] == []


def test_only_exact_relative_or_trusted_absolute_patient_reference_is_accepted(setup):
    client, fhir = setup
    fhir.results["Condition"] = bundle(
        {"resourceType": "Condition", "id": "relative", "subject": {"reference": "Patient/patient-1"}},
        {"resourceType": "Condition", "id": "absolute", "subject": {"reference": "https://ehr.example/fhir/Patient/patient-1"}})
    part = client.get("/api/patients/patient-1/history").json()["parts"]["conditions"]
    assert part["status"] == "ok"
    assert [item["id"] for item in part["items"]] == ["relative", "absolute"]


@pytest.mark.parametrize("result", [http_error(), {"resourceType": "OperationOutcome"}, {},
                                   {"resourceType": "Bundle", "entry": [None]}])
def test_source_errors_and_malformed_payloads_are_safe(setup, result):
    client, fhir = setup
    fhir.results["Patient"] = result
    response = client.get("/api/patients")
    assert response.status_code == 503
    assert response.json() == {"detail": records.SOURCE_UNAVAILABLE}
    assert response.headers["cache-control"] == "no-store"


def test_timeouts_are_bounded_and_not_retried(setup, monkeypatch):
    client, fhir = setup
    async def delayed():
        await asyncio.sleep(10)
    monkeypatch.setattr(records, "READ_TIMEOUT_SECONDS", 0.01)
    fhir.results["Patient"] = delayed
    assert client.get("/api/patients").status_code == 503
    assert fhir.calls == [("Patient", {"_count": 21})]


def test_mock_source_and_unsupported_encounters_are_honest(setup):
    client, fhir = setup
    class MockFHIRClient(FHIRClient):
        pass
    fhir.__class__ = MockFHIRClient
    data = client.get("/api/patients/patient-1/history").json()
    assert data["source"] == "mock"
    assert data["parts"]["encounters"]["status"] == "unavailable"
    assert not any(path == "Encounter" for path, _ in fhir.calls)
