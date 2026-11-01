from fastapi import APIRouter


def router() -> APIRouter:
    routes = APIRouter()

    @routes.get("/health")
    def health() -> dict[str, str]:
        """Liveness for compose's health checks: the process answers. Needs no key."""
        return {"status": "ok"}

    return routes
