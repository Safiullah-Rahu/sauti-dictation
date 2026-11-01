"""FastAPI application factory."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, api_admin, api_user
from .backends.base import Backends
from .config import Config, load_config
from .db import Store
from .dictation import DictationSocket, purge_results
from .pipeline import Pipeline
from .scheduler import Priority, Stage
from .state import AppState

log = logging.getLogger("sauti")
WEB = Path(__file__).parent / "web"


def build_backends(cfg: Config) -> Backends:
    if cfg.asr.backend == "fake":
        from .backends.fake import FakeAsr
        asr = FakeAsr()
    elif cfg.asr.backend == "faster_whisper":
        from .backends.asr_faster_whisper import FasterWhisperBackend
        asr = FasterWhisperBackend(cfg.asr)
    else:
        raise ValueError(f"unknown ASR backend {cfg.asr.backend!r}")

    llm = None
    if cfg.llm.backend == "openai_compat":
        from .backends.llm_openai import OpenAICompatLlm
        llm = OpenAICompatLlm(cfg.llm)
    elif cfg.llm.backend == "fake":
        from .backends.fake import FakeLlm
        llm = FakeLlm()
    elif cfg.llm.backend != "none":
        raise ValueError(f"unknown LLM backend {cfg.llm.backend!r}")

    translate = None
    if cfg.translate.backend == "ct2":
        from .backends.mt_ct2 import Ct2Translator
        translate = Ct2Translator(cfg.translate)
    elif cfg.translate.backend not in ("llm", "none", "fake"):
        raise ValueError(f"unknown translation backend {cfg.translate.backend!r}")
    return Backends(asr=asr, llm=llm, translate=translate)


def _stages(cfg: Config, backends: Backends) -> tuple[Stage, Stage | None, Stage | None]:
    sc = cfg.scheduler
    limits = {Priority.FINAL: sc.max_queued_finals, Priority.PARTIAL: max(2, cfg.asr.workers * 2),
              Priority.BATCH: sc.max_queued_batch}
    per_user = {Priority.FINAL: sc.max_queued_per_user, Priority.PARTIAL: 1, Priority.BATCH: 2}
    asr = Stage("asr", cfg.asr.workers, max_queued=limits, max_per_user=per_user, use_threads=True,
                partial_max_age_s=sc.partial_max_age_s)
    llm = Stage("llm", cfg.llm.max_concurrency, max_queued=limits, max_per_user=per_user,
                use_threads=False) if backends.llm else None
    mt = Stage("translate", cfg.translate.workers, max_queued=limits, max_per_user=per_user,
               use_threads=False) if backends.translate else None
    return asr, llm, mt


async def _maintenance(st: AppState) -> None:
    tick = 0
    while True:
        try:
            purge_results(st)
            st.pairing.purge()
            if st.backends.llm is not None:
                st.llm_healthy = await st.backends.llm.healthy()
            if tick % 20 == 0:  # every ~10 minutes
                await asyncio.to_thread(st.store.purge_expired_history)
                await asyncio.to_thread(st.store.purge_tokens)
                await asyncio.to_thread(st.store.purge_events)
        except Exception:
            log.exception("maintenance step failed")
        tick += 1
        await asyncio.sleep(30)


def create_app(cfg: Config | None = None, backends: Backends | None = None) -> FastAPI:
    cfg = cfg or load_config()
    logging.basicConfig(level=cfg.server.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        b = backends or await asyncio.to_thread(build_backends, cfg)
        store = Store(cfg.data_path / "sauti.db")
        asr, llm, mt = _stages(cfg, b)
        for stage in (asr, llm, mt):
            if stage:
                stage.start()
        st = AppState(cfg, store, b, asr, llm, mt, Pipeline(cfg, b, asr, llm, mt))
        app.state.sauti = st
        if b.llm is not None:
            st.llm_healthy = await b.llm.healthy()
            if not st.llm_healthy:
                log.warning("LLM backend not reachable at %s - clean mode will use rule-based cleanup",
                            getattr(cfg.llm, "base_url", "?"))
        task = asyncio.create_task(_maintenance(st))
        log.info("Sauti %s ready on %s:%s", __version__, cfg.server.host, cfg.server.port)
        try:
            yield
        finally:
            task.cancel()
            for stage in st.stages():
                await stage.stop()
            if b.llm is not None and hasattr(b.llm, "close"):
                await b.llm.close()
            store.close()

    app = FastAPI(title="Sauti", version=__version__, lifespan=lifespan, docs_url="/api/docs", redoc_url=None)
    app.add_exception_handler(api_user.ApiError, api_user.api_error_handler)
    app.include_router(api_user.router)
    app.include_router(api_admin.router)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if not request.url.path.startswith("/api/docs"):
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; connect-src 'self' ws: wss:; img-src 'self' data:; style-src 'self'; "
                "script-src 'self'; frame-ancestors 'none'")
        if request.url.path.startswith("/v1/"):
            response.headers.setdefault("Cache-Control", "no-store")
        elif request.url.path in ("/app", "/admin") or request.url.path.startswith("/static/"):
            # Revalidate on every load so an update is picked up immediately (ETag keeps it cheap).
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    @app.websocket("/v1/dictate")
    async def dictate(ws: WebSocket):
        await DictationSocket(ws.app.state.sauti, ws).serve()

    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/app")

    @app.get("/app", include_in_schema=False)
    async def user_portal():
        return FileResponse(WEB / "app.html")

    @app.get("/admin", include_in_schema=False)
    async def admin_dashboard():
        return FileResponse(WEB / "admin.html")

    return app
