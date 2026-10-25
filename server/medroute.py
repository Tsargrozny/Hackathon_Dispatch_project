from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from . import keystore

PAGES = Path(__file__).resolve().parent.parent / "static" / "medroute"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}

router = APIRouter()


def is_local(req: Request) -> bool:
    if any(h in req.headers for h in ("x-forwarded-for", "x-forwarded-host", "x-real-ip")):
        return False
    return bool(req.client) and req.client.host in LOCAL_HOSTS


def local_only():
    return JSONResponse({"ok": False, "error": "Settings can only be changed from this laptop (http://localhost:8000/settings)."}, 403)


@router.get("/settings")
async def settings_page(req: Request):
    if not is_local(req):
        return local_only()
    return FileResponse(PAGES / "settings.html")


@router.get("/api/settings")
async def settings_get():
    return {"fields": keystore.public_fields(), "keys": keystore.status(), "env_file": keystore.ENV_PATH.exists()}


@router.post("/api/settings")
async def settings_post(req: Request):
    if not is_local(req):
        return local_only()
    body = await req.json()
    changed = keystore.save(body.get("values") or {}, body.get("clear") or [])
    return {"ok": True, "changed": changed, "keys": keystore.status()}
