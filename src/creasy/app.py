from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from creasy import __version__
from creasy.api.dashboard import attach_spa, router as dashboard_router
from creasy.api.health import router as health_router
from creasy.api.webhook import router as webhook_router
from creasy.config import Config, load_config
from creasy.gitlab.client import GitLabClient
from creasy.jobs.manager import Manager
from creasy.jobs.worker import OpenCodeRunner
from creasy.logging import setup_logging
from creasy.workspace.store import WorkspaceStore


def create_app(config: Config | None = None) -> FastAPI:
    cfg = config or load_config()
    log = setup_logging(cfg.log_level, cfg.log_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        gitlab = GitLabClient(cfg.gitlab_url, cfg.gitlab_token)
        workspaces = WorkspaceStore(cfg.data_dir / "workspace_meta")
        runner = OpenCodeRunner(cfg, workspaces, gitlab)
        manager = Manager(cfg, runner, workspaces=workspaces)
        app.state.config = cfg
        app.state.manager = manager
        app.state.gitlab = gitlab
        app.state.bot_user_id = gitlab.current_user_id()
        log.info("creasy %s starting host=%s port=%s", __version__, cfg.host, cfg.port)
        manager.boot()
        yield
        manager.shutdown()
        gitlab.close()
        log.info("creasy stopped")

    app = FastAPI(title="creasy", version=__version__, lifespan=lifespan)
    app.state.config = cfg
    app.include_router(health_router)
    app.include_router(webhook_router)
    app.include_router(dashboard_router)
    attach_spa(app)
    return app


def main() -> None:
    import uvicorn

    cfg = load_config()
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level=cfg.log_level.lower())


if __name__ == "__main__":
    main()
