"""Start the agent's HTTP API: uv run python -m siteops_agent.api.main  (host and port from config.yaml's api section)."""

import logging

import uvicorn

from siteops_agent.config import Settings
from siteops_agent.container import build_api


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    uvicorn.run(build_api(settings), host=settings.api.host, port=settings.api.port)


if __name__ == "__main__":
    main()
