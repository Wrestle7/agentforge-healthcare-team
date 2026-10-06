"""Static UI registration, independent of model and database initialization."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles


class WorkspaceStaticFiles(StaticFiles):
    """Avoid heuristic caching of mutable, unbundled workspace modules."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"
        return response


def mount_frontends(app: FastAPI, project_root: Path) -> None:
    """Call after API routers; explicit entry points precede static assets."""
    classic = project_root / "frontend-v2"
    workspace = project_root / "frontend-v3"

    if classic.is_dir():
        @app.get("/classic", include_in_schema=False)
        async def classic_redirect():
            return RedirectResponse("/classic/", status_code=308)

        app.mount("/classic", StaticFiles(directory=str(classic), html=True), name="classic")

    if workspace.is_dir():
        @app.get("/", include_in_schema=False)
        async def landing_page():
            return FileResponse(workspace / "landing.html", headers={"Cache-Control": "no-store"})

        @app.get("/workspace", include_in_schema=False)
        async def workspace_redirect():
            return RedirectResponse("/workspace/", status_code=308)

        @app.get("/workspace/", include_in_schema=False)
        async def workspace_page():
            return FileResponse(workspace / "index.html", headers={"Cache-Control": "no-store"})

        app.mount("/", WorkspaceStaticFiles(directory=str(workspace), html=True), name="workspace")
