"""Authenticated, GET-only endpoints for the patient workspace."""

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.api.auth import verify_api_key
from app.config import settings
from app.patient_records import PatientReadError, get_patient, get_section, search_patients


class PrivatePatientRoute(APIRoute):
    """Apply no-store to success, auth failure and validation/error responses."""

    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                response = await original(request)
            except PatientReadError as error:
                response = JSONResponse({"detail": error.message}, status_code=error.status_code)
            except RequestValidationError:
                response = JSONResponse({"detail": "请求参数格式不正确。"}, status_code=422)
            except HTTPException as error:
                response = JSONResponse({"detail": error.detail}, status_code=error.status_code,
                                        headers=error.headers)
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            return response

        return handler


router = APIRouter(dependencies=[Depends(verify_api_key)], route_class=PrivatePatientRoute)


def get_patient_client():
    # Lazy import keeps pure record extraction/tests independent of credentials.
    from app.fhir_client import fhir_client
    return fhir_client


@router.get("/patients")
async def patient_candidates(q: str = Query(default="", max_length=100),
                             limit: int = Query(default=20, ge=1, le=50),
                             client=Depends(get_patient_client)):
    return await search_patients(client, q, limit)


@router.get("/patients/{patient_id}")
async def patient_overview(patient_id: str, client=Depends(get_patient_client)):
    return await get_patient(client, patient_id)


@router.get("/patients/{patient_id}/{section}")
async def patient_section(patient_id: str, section: str, client=Depends(get_patient_client)):
    return await get_section(client, patient_id, section, base_url=settings.openemr_fhir_url)
