"""Deterministic, bounded FHIR reads for the patient workspace.

No agent tools, model calls, database connections or EHR write methods are used.
An unavailable source is never represented as a successful empty collection.
"""

import asyncio
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.tools.fhir_helpers import (
    extract_allergy,
    extract_condition,
    extract_medication_request,
    extract_patient_summary,
)

# A lookup may perform an identity read followed by a parallel section batch.
# Keep their combined deadline below the frontend's 15-second request deadline.
READ_TIMEOUT_SECONDS = 6
SECTION_LIMIT = 100
PATIENT_ID = re.compile(r"[A-Za-z0-9.-]{1,64}\Z")
SOURCE_UNAVAILABLE = "患者数据源暂时不可用，请稍后重试。"


class PatientReadError(Exception):
    def __init__(self, status_code: int = 503, message: str = SOURCE_UNAVAILABLE,
                 *, upstream_status: int | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        # Internal control flow only; routes never serialize upstream details.
        self.upstream_status = upstream_status


def source_name(client) -> str | None:
    name = type(client).__name__
    return "mock" if name == "MockFHIRClient" else "openemr" if name == "FHIRClient" else None


def validate_patient_id(patient_id: str) -> str:
    if not PATIENT_ID.fullmatch(patient_id) or patient_id in {".", ".."}:
        raise PatientReadError(422, "患者 ID 格式不正确。")
    return patient_id


def validate_query(query: str) -> str:
    if len(query) > 100 or any(ord(c) < 32 or ord(c) == 127 for c in query):
        raise PatientReadError(422, "请输入不超过 100 个字符的患者姓名或 ID。")
    return query.strip()


def _search_literal(value: str) -> str:
    # Escape FHIR search-value operators; never interpolate a query into a URL.
    return re.sub(r"([\\,$|])", r"\\\1", value)


def _is_missing(error: Exception, client, path: str) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code == 404
    # The existing in-memory provider predates typed HTTP errors.
    return source_name(client) == "mock" and str(error) == f"{path} not found"


async def _read(client, path: str, params: dict | None = None) -> dict:
    try:
        result = await asyncio.wait_for(client.get(path, params), READ_TIMEOUT_SECONDS)
        if not isinstance(result, dict) or result.get("resourceType") == "OperationOutcome":
            raise PatientReadError()
        return result
    except PatientReadError:
        raise
    except Exception as error:
        # Never return exception text: upstream URLs can include credentials.
        if path.startswith("Patient/") and _is_missing(error, client, path):
            raise PatientReadError(404, "未找到这位患者，请重新选择。", upstream_status=404) from None
        upstream_status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
        raise PatientReadError(upstream_status=upstream_status) from None


def _bundle(result: dict, resource_type: str, limit: int) -> tuple[list[dict], bool]:
    # MockFHIRClient returns entry-only bundles; real FHIR returns resourceType.
    if result.get("resourceType") not in (None, "Bundle"):
        raise PatientReadError()
    if "entry" not in result and result.get("resourceType") != "Bundle":
        raise PatientReadError()
    entries = result.get("entry", [])
    if not isinstance(entries, list):
        raise PatientReadError()
    resources = []
    for entry in entries:
        resource = entry.get("resource") if isinstance(entry, dict) else None
        if not isinstance(resource, dict) or resource.get("resourceType") != resource_type:
            raise PatientReadError()
        resources.append(resource)
    links = result.get("link", [])
    if not isinstance(links, list):
        raise PatientReadError()
    has_more = len(resources) > limit or any(
        isinstance(link, dict) and link.get("relation") == "next" and link.get("url")
        for link in links
    )
    # Do not follow next URLs supplied by an upstream service.
    return resources[:limit], bool(has_more)


def _patient_summary(resource: dict) -> dict:
    if resource.get("resourceType") != "Patient":
        raise PatientReadError()
    identifier = resource.get("id")
    if not isinstance(identifier, str) or not PATIENT_ID.fullmatch(identifier):
        raise PatientReadError()
    try:
        result = extract_patient_summary(resource)
        # FHIR HumanName.text is valid when given/family are absent.
        if result["name"] == "Unknown":
            result["name"] = (resource.get("name") or [{}])[0].get("text") or "姓名未提供"
        return result
    except (AttributeError, TypeError, KeyError, IndexError):
        raise PatientReadError() from None


async def get_patient(client, patient_id: str) -> dict:
    validate_patient_id(patient_id)
    resource = await _read(client, f"Patient/{patient_id}")
    result = _patient_summary(resource)
    if result["id"] != patient_id:
        raise PatientReadError()
    return {**result, "source": source_name(client)}


async def search_patients(client, query: str = "", limit: int = 20) -> dict:
    query = validate_query(query)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        raise PatientReadError(422, "每次读取数量须为 1 至 50。")
    # Exact resource-ID lookup is unambiguous. Name queries return every candidate
    # on the bounded page; they never silently select the first matching person.
    if query and PATIENT_ID.fullmatch(query) and query not in {".", ".."}:
        try:
            patient = await get_patient(client, query)
        except PatientReadError as error:
            # Some OpenEMR installations reject non-UUID IDs with HTTP 400.
            # That means the Latin-name probe can continue, not that an actual
            # authentication, timeout, malformed response or server error can.
            if error.upstream_status not in (400, 404):
                raise
        else:
            patient.pop("source", None)
            return {"items": [_candidate(patient)], "query": query, "limit": limit,
                    "has_more": False, "source": source_name(client)}
    params = {"_count": limit + 1}
    if query:
        params["name"] = _search_literal(query)
    result = await _read(client, "Patient", params)
    resources, has_more = _bundle(result, "Patient", limit)
    items = [_candidate(_patient_summary(resource)) for resource in resources]
    return {"items": items, "query": query, "limit": limit,
            "has_more": has_more, "source": source_name(client)}


def _candidate(patient: dict) -> dict:
    return {key: patient.get(key) for key in ("id", "name", "birth_date", "gender")}


def _concept(value: dict | None) -> str | None:
    if not value:
        return None
    return value.get("text") or next((c.get("display") or c.get("code")
                                     for c in value.get("coding", []) if c.get("display") or c.get("code")), None)


def _codes(concepts: list) -> list[str]:
    return [coding["code"] for concept in concepts for coding in concept.get("coding", [])
            if isinstance(coding.get("code"), str)]


def _reference_range(resource: dict) -> str | None:
    values = []
    for reference in resource.get("referenceRange", []):
        if reference.get("text"):
            values.append(reference["text"])
        else:
            low, high = reference.get("low", {}), reference.get("high", {})
            parts = []
            if low.get("value") is not None:
                parts.append(f"≥ {low['value']}")
            if high.get("value") is not None:
                parts.append(f"≤ {high['value']}")
            if parts:
                values.append(" / ".join(parts) + f" {low.get('unit') or high.get('unit') or ''}".rstrip())
    return "; ".join(values) or None


def _observation_value(resource: dict) -> dict:
    quantity = resource.get("valueQuantity", {})
    value = quantity.get("value")
    if value is None:
        value = resource.get("valueString") or _concept(resource.get("valueCodeableConcept"))
    if value is None and "valueBoolean" in resource:
        value = "true" if resource["valueBoolean"] else "false"
    return {"test_name": _concept(resource.get("code")), "value": value,
            "unit": quantity.get("unit") or quantity.get("code"),
            "reference_range": _reference_range(resource)}


def _observation(resource: dict) -> dict:
    codes = resource.get("code", {}).get("coding", [])
    return {"id": resource.get("id"), **_observation_value(resource),
            "test_code": codes[0].get("code") if codes else None,
            "status": resource.get("status"),
            "date": resource.get("effectiveDateTime") or resource.get("effectivePeriod", {}).get("start") or resource.get("issued"),
            "interpretation": "; ".join(filter(None, (_concept(v) for v in resource.get("interpretation", [])))) or None,
            "category": _codes(resource.get("category", [])),
            "components": [_observation_value(c) for c in resource.get("component", [])]}


def _allergy(resource: dict) -> dict:
    return {**extract_allergy(resource), "reactions": [
        _concept(manifestation) for reaction in resource.get("reaction", [])
        for manifestation in reaction.get("manifestation", []) if _concept(manifestation)]}


def _encounter(resource: dict) -> dict:
    period = resource.get("period", {})
    return {"id": resource.get("id"), "status": resource.get("status"),
            "type": "; ".join(filter(None, (_concept(v) for v in resource.get("type", [])))) or None,
            "start": period.get("start"), "end": period.get("end"),
            "reason": "; ".join(filter(None, (_concept(v) for v in resource.get("reasonCode", [])))) or None}


SECTIONS = {
    "medications": {"medications": ("MedicationRequest", extract_medication_request),
                    "allergies": ("AllergyIntolerance", _allergy)},
    "labs": {"observations": ("Observation", _observation)},
    "history": {"conditions": ("Condition", extract_condition),
                "encounters": ("Encounter", _encounter)},
}


def _matches_patient_reference(reference: Any, patient_id: str, base_url: str | None) -> bool:
    expected = f"Patient/{patient_id}"
    if not isinstance(reference, str):
        return False
    if reference == expected:
        return True
    if not base_url:
        return False
    try:
        target, base = urlsplit(reference), urlsplit(base_url)
        # Absolute references must identify the resource on the configured FHIR
        # server and exact base path. A matching suffix alone is insufficient.
        if target.scheme not in {"http", "https"} or base.scheme not in {"http", "https"}:
            return False
        if not target.hostname or not base.hostname or target.username or target.password:
            return False
        if target.query or target.fragment or base.query or base.fragment:
            return False
        origin = lambda url: (url.scheme, url.hostname, url.port or (443 if url.scheme == "https" else 80))
        return (origin(target) == origin(base)
                and target.path == f"{base.path.rstrip('/')}/{expected}")
    except ValueError:
        return False


async def _part(client, patient_id: str, resource_type: str, extract, base_url: str | None) -> dict:
    if source_name(client) == "mock" and resource_type == "Encounter":
        # The built-in mock has no Encounter collection. Its generic empty list
        # is not evidence that a patient has no encounters.
        return {"status": "unavailable", "items": [], "has_more": False,
                "message": "模拟数据源未提供就诊记录。"}
    params: dict[str, Any] = {"patient": patient_id, "_count": SECTION_LIMIT + 1}
    if resource_type == "Observation":
        params["category"] = "laboratory"
    try:
        resources, has_more = _bundle(await _read(client, resource_type, params), resource_type, SECTION_LIMIT)
        for resource in resources:
            association = resource.get("patient" if resource_type == "AllergyIntolerance" else "subject")
            owner = association.get("reference") if isinstance(association, dict) else None
            if not _matches_patient_reference(owner, patient_id, base_url):
                raise PatientReadError()
        if resource_type == "Observation":
            # The existing mock ignores category. Avoid presenting known vitals
            # as lab results even if an upstream ignores the search parameter.
            resources = [r for r in resources if not _codes(r.get("category", []))
                         or "laboratory" in _codes(r.get("category", []))]
        items = [extract(resource) for resource in resources]
        return {"status": "ok", "items": items, "has_more": has_more}
    except Exception:
        return {"status": "unavailable", "items": [], "has_more": False,
                "message": SOURCE_UNAVAILABLE}


async def get_section(client, patient_id: str, section: str, *, base_url: str | None = None) -> dict:
    validate_patient_id(patient_id)
    if section not in SECTIONS:
        raise PatientReadError(404, "未找到这个患者资料栏目。")
    await get_patient(client, patient_id)  # Empty records must not disguise a missing patient.
    specs = SECTIONS[section]
    results = await asyncio.gather(*(_part(client, patient_id, kind, extract, base_url)
                                     for kind, extract in specs.values()))
    return {"patient_id": patient_id, "section": section, "source": source_name(client),
            "parts": dict(zip(specs, results))}
