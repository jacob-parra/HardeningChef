"""HardeningChef web app."""

from __future__ import annotations

import os
import re
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .catalog import ValidationError, load_catalog
from .generator import generate
from .store import ScriptStore

HERE = Path(__file__).resolve().parent


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


SHARE_ENABLED = os.environ.get("HC_SHARE_ENABLED", "1").lower() not in ("0", "false", "no")
SHARE_TTL = _env_int("HC_SHARE_TTL", 600)
SHARE_MAX = _env_int("HC_SHARE_MAX", 500)
RATE_PER_MIN = _env_int("HC_RATE_LIMIT", 30)
PUBLIC_URL = os.environ.get("HC_PUBLIC_URL", "").rstrip("/")
MAX_BODY = 64 * 1024

store = ScriptStore(ttl=SHARE_TTL, max_entries=SHARE_MAX)
app = FastAPI(title="HardeningChef", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

_hits: dict[str, deque] = defaultdict(deque)


def _rate_limited(ip: str) -> bool:
    if RATE_PER_MIN <= 0:
        return False
    now = time.monotonic()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_PER_MIN:
        return True
    q.append(now)
    if len(_hits) > 10_000:
        _hits.clear()
    return False


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        return JSONResponse({"detail": "request too large"}, status_code=413)
    resp = await call_next(request)
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    )
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    return resp


class GenerateRequest(BaseModel):
    os: str = Field(max_length=40)
    config: dict = Field(default_factory=dict)
    share: bool = True


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(HERE / "templates" / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


@app.get("/api/catalog")
def catalog():
    data = load_catalog().public()
    data["share"] = {"enabled": SHARE_ENABLED, "ttl": SHARE_TTL}
    return data


@app.post("/api/generate")
def api_generate(req: GenerateRequest, request: Request):
    ip = request.client.host if request.client else "?"
    if _rate_limited(ip):
        raise HTTPException(429, "Too many requests; wait a minute and try again.")
    try:
        result = generate(req.os, req.config)
    except ValidationError as e:
        return JSONResponse({"detail": "invalid selection", "errors": e.errors}, status_code=422)

    out = {k: result[k] for k in ("script", "filename", "sha256", "profile", "warnings")}
    if SHARE_ENABLED and req.share:
        token, expires = store.put(result["script"], result["sha256"], result["filename"])
        base = PUBLIC_URL or str(request.base_url).rstrip("/")
        out["share"] = {"url": f"{base}/s/{token}", "expires_at": int(expires), "ttl": SHARE_TTL}
    return out


_TOKEN = re.compile(r"[A-Za-z0-9_-]{20,40}")


def _lookup(token: str):
    entry = store.get(token) if _TOKEN.fullmatch(token) else None
    if entry is None:
        raise HTTPException(404, "This script link has expired or never existed. Generate a new one.")
    return entry


_NO_CACHE = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"}


@app.get("/s/{token}.sha256", response_class=PlainTextResponse)
def shared_sha(token: str):
    e = _lookup(token)
    return PlainTextResponse(f"{e.sha256}  {e.filename}\n", headers=_NO_CACHE)


@app.get("/s/{token}", response_class=PlainTextResponse)
def shared_script(token: str):
    e = _lookup(token)
    headers = {**_NO_CACHE, "Content-Disposition": f'inline; filename="{e.filename}"'}
    return PlainTextResponse(e.script, headers=headers)
