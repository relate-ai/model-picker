from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
STATIC_DIR = PROJECT_DIR / "static"
DATA_DIR = Path(os.environ.get("MODEL_PICKER_DATA", "/app/data"))
HOST_BASE = Path(os.environ.get("RELATE_HOST_BASE", "/host/relate-skills"))

CATALOG_CANDIDATES = [
    Path(os.environ.get("CATALOG_PATH", "")),
    HOST_BASE / "dispatch" / "catalog" / "current.json",
    DATA_DIR / "catalog" / "current.json",
    APP_DIR / "data" / "current.json",
]
REFRESH_STATE_CANDIDATES = [
    HOST_BASE / "refresh" / "state.json",
    DATA_DIR / "refresh" / "state.json",
]
ROUTING_MATRIX_CANDIDATES = [
    HOST_BASE / "dispatch" / "routing-matrix.json",
    DATA_DIR / "routing-matrix.json",
    APP_DIR / "data" / "routing-matrix.json",
]
TELEMETRY_CANDIDATES = [
    HOST_BASE / "dispatch" / "telemetry-events.jsonl",
    DATA_DIR / "telemetry-events.jsonl",
    APP_DIR / "data" / "telemetry-events.jsonl",
]
HOST_EXPORT_GLOBS = [
    HOST_BASE / "dispatch" / "artifacts",
    DATA_DIR / "artifacts",
]


def _first_existing(candidates: list[Path]) -> Optional[Path]:
    for p in candidates:
        if p and str(p) and p.is_file():
            return p
    return None


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _catalog_path() -> Optional[Path]:
    return _first_existing(CATALOG_CANDIDATES)


def _load_catalog() -> dict[str, Any]:
    p = _catalog_path()
    if p is None:
        raise HTTPException(status_code=503, detail="catalog unavailable")
    try:
        data = _read_json(p)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"catalog parse error: {exc}")
    data["_source_path"] = str(p)
    data["_source_sha256"] = _sha256_file(p)
    return data


def _require_auth(authorization: Optional[str] = None) -> None:
    token = os.environ.get("MODEL_PICKER_TOKEN") or os.environ.get("DASHBOARD_TOKEN")
    if not token:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    if authorization[len("Bearer ") :] != token:
        raise HTTPException(status_code=403, detail="invalid token")


def _normalise_host_export(path: Path) -> list[dict[str, Any]]:
    try:
        data = _read_json(path)
    except Exception:
        return []
    info = data.get("info") or {}
    session_id = info.get("id")
    model = (info.get("model") or {})
    selected_model = f"{model.get('providerID')}/{model.get('id')}" if model.get("providerID") and model.get("id") else None
    messages = data.get("messages") or []
    assistant_msgs = [m for m in messages if (m.get("info") or {}).get("role") == "assistant"]
    if not assistant_msgs:
        return []
    # One invocation record per assistant message, using final token totals from session info.
    records: list[dict[str, Any]] = []
    total_input = info.get("tokens", {}).get("input", 0) or 0
    total_cache_read = (info.get("tokens", {}).get("cache") or {}).get("read", 0) or 0
    total_output = info.get("tokens", {}).get("output", 0) or 0
    total_reasoning = info.get("tokens", {}).get("reasoning", 0) or 0
    created = info.get("time", {}).get("created")
    updated = info.get("time", {}).get("updated")
    wall_ms = None
    if created and updated:
        wall_ms = int(updated - created)
    cost = info.get("cost", 0)
    records.append(
        {
            "event_id": f"hostexport:{path.stem}",
            "timestamp_utc": datetime.fromtimestamp((updated or created or 0) / 1000, tz=timezone.utc).isoformat() if (updated or created) else None,
            "contract_id": path.stem.replace("-host-export", ""),
            "task_class": None,
            "model_selected": selected_model,
            "model_used": selected_model,
            "provider": model.get("providerID"),
            "plan": None,
            "route": "opencode-cli",
            "skill": None,
            "catalog_sha256": None,
            "start_utc": datetime.fromtimestamp(created / 1000, tz=timezone.utc).isoformat() if created else None,
            "end_utc": datetime.fromtimestamp(updated / 1000, tz=timezone.utc).isoformat() if updated else None,
            "wall_time_ms": wall_ms,
            "ttft_ms": None,
            "throughput_tps": None,
            "tokens": {
                "input": total_input,
                "cache_read": total_cache_read,
                "cache_write": 0,
                "output": total_output,
                "reasoning": total_reasoning,
                "total": (info.get("tokens") or {}).get("total"),
            },
            "cost_usd": cost,
            "billing_status": "provider_reported" if cost not in (None, 0) else "estimated_or_zero",
            "success": True,
            "failure_reason": None,
            "retry_count": 0,
            "fallback": False,
            "session_id": session_id,
            "provider_request_id": None,
            "source": "host-export",
            "source_path": str(path),
        }
    )
    return records


def _load_telemetry_events() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    p = _first_existing(TELEMETRY_CANDIDATES)
    if p is not None:
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass
    # Derive additional records from host exports if present.
    for base in HOST_EXPORT_GLOBS:
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*-host-export.json")):
            records.extend(_normalise_host_export(path))
    # De-duplicate by event_id preserving first.
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in records:
        eid = r.get("event_id") or r.get("source_path")
        if eid in seen:
            continue
        seen.add(eid)
        out.append(r)
    out.sort(key=lambda r: r.get("timestamp_utc") or r.get("start_utc") or "", reverse=True)
    return out


app = FastAPI(title="Relate Model Picker", version="1.0.0")


@app.get("/api/health")
def health() -> dict[str, Any]:
    cp = _catalog_path()
    return {
        "status": "ok",
        "catalog_path": str(cp) if cp else None,
        "catalog_sha256": _sha256_file(cp) if cp else None,
        "host_base": str(HOST_BASE),
        "host_base_exists": HOST_BASE.exists(),
        "time_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/catalog")
def catalog() -> JSONResponse:
    data = _load_catalog()
    return JSONResponse(data)


@app.get("/api/models")
def models(
    q: Optional[str] = Query(default=None),
    provider: Optional[str] = Query(default=None),
    billing: Optional[str] = Query(default=None),
    capability: Optional[str] = Query(default=None),
    sort: str = Query(default="id"),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict[str, Any]:
    data = _load_catalog()
    items = list((data.get("catalog") or {}).get("models") or [])
    if q:
        ql = q.lower()
        items = [m for m in items if ql in json.dumps(m, ensure_ascii=False).lower()]
    if provider:
        items = [m for m in items if m.get("provider") == provider]
    if billing:
        items = [m for m in items if m.get("billing_type") == billing]
    if capability:
        items = [m for m in items if capability in (m.get("capabilities") or [])]
    reverse = sort.startswith("-")
    key = sort.lstrip("-")
    def getter(m: dict[str, Any]) -> Any:
        if key in ("input_price", "input_usd_per_million"):
            return float((m.get("effective_rates") or {}).get("input_usd_per_million") or 0)
        if key in ("output_price", "output_usd_per_million"):
            return float((m.get("effective_rates") or {}).get("output_usd_per_million") or 0)
        if key == "context":
            return (m.get("host_model_ref") or {}).get("context_limit") or 0
        return m.get(key) or ""
    items.sort(key=getter, reverse=reverse)
    return {"count": len(items), "items": items[:limit], "catalog_sha256": data.get("catalog_sha256")}


@app.get("/api/refresh-status")
def refresh_status() -> dict[str, Any]:
    p = _first_existing(REFRESH_STATE_CANDIDATES)
    if p is None:
        return {"status": "UNKNOWN", "detail": "refresh state not found", "source_path": None}
    try:
        data = _read_json(p)
        data["_source_path"] = str(p)
        data["_source_sha256"] = _sha256_file(p)
        return data
    except Exception as exc:
        return {"status": "ERROR", "detail": str(exc)}


@app.get("/api/routing-matrix")
def routing_matrix() -> dict[str, Any]:
    p = _first_existing(ROUTING_MATRIX_CANDIDATES)
    if p is None:
        return {"status": "UNKNOWN", "detail": "routing matrix not found", "cases": []}
    try:
        data = _read_json(p)
        data["_source_path"] = str(p)
        data["_source_sha256"] = _sha256_file(p)
        return data
    except Exception as exc:
        return {"status": "ERROR", "detail": str(exc)}


@app.get("/api/telemetry/events")
def telemetry_events(authorization: Optional[str] = Header(default=None), limit: int = Query(default=500, ge=1, le=5000)) -> dict[str, Any]:
    _require_auth(authorization)
    events = _load_telemetry_events()
    return {"count": len(events), "events": events[:limit]}


@app.get("/api/telemetry/summary")
def telemetry_summary(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    _require_auth(authorization)
    events = _load_telemetry_events()
    by_model: dict[str, dict[str, Any]] = {}
    by_provider: dict[str, dict[str, Any]] = {}
    total_input = 0
    total_output = 0
    total_cache = 0
    total_cost = 0.0
    total_wall = 0
    successes = 0
    for e in events:
        tokens = e.get("tokens") or {}
        inp = int(tokens.get("input") or 0)
        out = int(tokens.get("output") or 0)
        cache = int(tokens.get("cache_read") or 0)
        wall = int(e.get("wall_time_ms") or 0)
        cost = e.get("cost_usd")
        try:
            costf = float(cost) if cost is not None else 0.0
        except (TypeError, ValueError):
            costf = 0.0
        total_input += inp
        total_output += out
        total_cache += cache
        total_cost += costf
        total_wall += wall
        if e.get("success"):
            successes += 1
        model = e.get("model_used") or e.get("model_selected") or "unknown"
        provider = e.get("provider") or "unknown"
        by_model.setdefault(model, {"model": model, "calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "wall_time_ms": 0, "cost_usd": 0.0, "successes": 0})
        by_model[model]["calls"] += 1
        by_model[model]["input_tokens"] += inp
        by_model[model]["output_tokens"] += out
        by_model[model]["cache_read_tokens"] += cache
        by_model[model]["wall_time_ms"] += wall
        by_model[model]["cost_usd"] += costf
        if e.get("success"):
            by_model[model]["successes"] += 1
        by_provider.setdefault(provider, {"provider": provider, "calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "wall_time_ms": 0, "cost_usd": 0.0, "successes": 0})
        by_provider[provider]["calls"] += 1
        by_provider[provider]["input_tokens"] += inp
        by_provider[provider]["output_tokens"] += out
        by_provider[provider]["cache_read_tokens"] += cache
        by_provider[provider]["wall_time_ms"] += wall
        by_provider[provider]["cost_usd"] += costf
        if e.get("success"):
            by_provider[provider]["successes"] += 1
    return {
        "event_count": len(events),
        "success_count": successes,
        "failure_count": len(events) - successes,
        "totals": {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cache_read_tokens": total_cache,
            "wall_time_ms": total_wall,
            "cost_usd": round(total_cost, 6),
        },
        "by_model": sorted(by_model.values(), key=lambda x: x["calls"], reverse=True),
        "by_provider": sorted(by_provider.values(), key=lambda x: x["calls"], reverse=True),
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")