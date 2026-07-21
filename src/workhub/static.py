from pathlib import Path

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


class SpaStaticFiles(StaticFiles):
    """Serve Vite assets and fall back to index.html for client-side routes."""

    def __init__(self, directory: Path) -> None:
        super().__init__(directory=directory, html=True)

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            normalized_path = str(scope.get("path", path)).lstrip("/")
            if (
                exc.status_code != 404
                or normalized_path == "api"
                or normalized_path.startswith(("api/", "assets/"))
            ):
                raise
            return await super().get_response("index.html", scope)
