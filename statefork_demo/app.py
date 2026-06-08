from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .db_only_arm import DemoError, DoltDbOnlyArm
from .statefork_arm import StateForkArm, StateForkDemoError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "web"

app = FastAPI(title="StateFork vs DB Branch Demo", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

db_arm = DoltDbOnlyArm(PROJECT_ROOT)
statefork_arm = StateForkArm(PROJECT_ROOT)


def arm_for(name: str):
    if name in {"db", "dolt", "db-only"}:
        return db_arm
    if name in {"statefork", "sf"}:
        return statefork_arm
    raise DemoError(f"Unknown arm: {name}")


def call_arm(arm: str, action: str) -> JSONResponse:
    try:
        target = arm_for(arm)
        result = getattr(target, action)()
        return JSONResponse(result)
    except (DemoError, StateForkDemoError) as exc:
        return JSONResponse({"detail": str(exc), "arm": arm, "action": action}, status_code=400)
    except Exception as exc:
        return JSONResponse({"detail": f"Unexpected error: {exc}", "arm": arm, "action": action}, status_code=500)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/setup")
def setup(arm: str = Query(..., pattern="^(db|statefork)$")) -> JSONResponse:
    return call_arm(arm, "setup")


@app.post("/warm")
def warm(arm: str = Query(..., pattern="^(db|statefork)$")) -> JSONResponse:
    return call_arm(arm, "warm")


@app.post("/snapshot")
def snapshot(arm: str = Query(..., pattern="^(db|statefork)$")) -> JSONResponse:
    return call_arm(arm, "snapshot")


@app.post("/fix_a")
def fix_a(arm: str = Query(..., pattern="^(db|statefork)$")) -> JSONResponse:
    return call_arm(arm, "fix_a")


@app.post("/rollback")
def rollback(arm: str = Query(..., pattern="^(db|statefork)$")) -> JSONResponse:
    return call_arm(arm, "rollback")


@app.get("/checkout")
def checkout(arm: str = Query(..., pattern="^(db|statefork)$"), cart_id: int = 1) -> JSONResponse:
    _ = cart_id
    return call_arm(arm, "checkout")


@app.get("/search")
def search(arm: str = Query(..., pattern="^(db|statefork)$"), q: str = "mouse") -> JSONResponse:
    try:
        return JSONResponse(arm_for(arm).search(q))
    except (DemoError, StateForkDemoError) as exc:
        return JSONResponse({"detail": str(exc), "arm": arm, "action": "search"}, status_code=400)
    except Exception as exc:
        return JSONResponse({"detail": f"Unexpected error: {exc}", "arm": arm, "action": "search"}, status_code=500)


@app.get("/product/{product_id}")
def product(product_id: int, arm: str = Query(..., pattern="^(db|statefork)$")) -> JSONResponse:
    try:
        return JSONResponse(arm_for(arm).product(product_id))
    except (DemoError, StateForkDemoError) as exc:
        return JSONResponse({"detail": str(exc), "arm": arm, "action": "product"}, status_code=400)
    except Exception as exc:
        return JSONResponse({"detail": f"Unexpected error: {exc}", "arm": arm, "action": "product"}, status_code=500)


@app.get("/state")
def state(arm: str = Query(..., pattern="^(db|statefork)$")) -> JSONResponse:
    return call_arm(arm, "state")


@app.post("/reset")
def reset() -> dict[str, Any]:
    errors: list[str] = []
    for target in (db_arm, statefork_arm):
        try:
            target.cleanup()
        except Exception as exc:
            errors.append(str(exc))
    return {"status": "reset", "errors": errors}
