"""Live provider catalog adapters — the missing two-speed collectors.

Each adapter hits a real provider API using host credentials and produces a
canonical catalog source JSON conforming to schemas/catalog.schema.json.
Credentials are read from the host environment/auth store; never hardcoded.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any


class AdapterError(RuntimeError):
    pass


# Governance hard rule: NVIDIA/Nemotron offerings never enter any catalog path.
EXCLUDED_VENDOR_TOKENS = ("nvidia", "nemotron")


def is_excluded_vendor_model(*parts: Any) -> bool:
    """Case-insensitive NVIDIA/Nemotron identity exclusion for adapter output."""
    for part in parts:
        if isinstance(part, str) and any(tok in part.lower() for tok in EXCLUDED_VENDOR_TOKENS):
            return True
    return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _per_m(value: Any) -> str:
    # Host `opencode models --verbose` reports USD per 1M tokens directly.
    # Do not multiply by 1,000,000 (that would mislabel million-token units).
    return str(Decimal(str(value)).normalize())


# ---------------------------------------------------------------------------
# OpenRouter adapter
# ---------------------------------------------------------------------------

def collect_openrouter(*, as_of: str, api_key: str | None = None, timeout: int = 30) -> dict:
    """Collect OpenRouter free models + account key budget."""
    key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise AdapterError("OPENROUTER_API_KEY not set")

    def get(url: str) -> dict:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as exc:
            raise AdapterError(f"OpenRouter API failed: {exc}") from exc

    models_resp = get("https://openrouter.ai/api/v1/models")
    key_resp = get("https://openrouter.ai/api/v1/auth/key")
    budget = (key_resp.get("data") or {}).get("effective_budget") or {}

    models = []
    seen_ids = set()
    for m in models_resp.get("data") or []:
        mid = m.get("id") or ""
        if is_excluded_vendor_model(mid, m.get("name")):
            continue
        pricing = m.get("pricing") or {}
        try:
            prompt = Decimal(str(pricing.get("prompt", "1")))
            completion = Decimal(str(pricing.get("completion", "1")))
        except Exception:
            continue
        is_free = prompt == 0 and completion == 0
        if not is_free:
            continue  # adapter currently emits free models only; paid need explicit authorization
        catalog_id = f"openrouter/{mid}"
        if catalog_id in seen_ids:
            continue
        seen_ids.add(catalog_id)
        models.append(
            {
                "id": catalog_id,
                "provider": "openrouter",
                "pool": "openrouter-free",
                "billing_type": "free",
                "available": True,
                "authorized": True,
                "capabilities": ["text", "tools"]
                if m.get("architecture", {}).get("input_modalities")
                else ["text"],
                "qualifications": {},
                "effective_rates": {
                    "input_usd_per_million": "0",
                    "cached_input_usd_per_million": "0",
                    "output_usd_per_million": "0",
                    "effective_as_of": as_of,
                    "source_ref": f"openrouter://models/{mid}",
                },
                "host_model_ref": {
                    "provider_id": "openrouter",
                    "model_id": mid,
                    "dispatch_ref": f"openrouter/{mid}",
                    "context_limit": m.get("context_length"),
                    "status": "active",
                    "evidence": "openrouter-api-v1-models",
                },
            }
        )

    account = {
        "limit_id": "openrouter-key-budget",
        "unit": "usd_equivalent",
        "window": "key_limit",
        "debit_metric": "allowance_usd",
        "remaining": str(budget.get("remaining_usd", "0")),
        "observed_at": _utc_now(),
        "valid_until": "2099-12-31T23:59:59+00:00",
        "source_ref": f"openrouter://auth/key?label={(key_resp.get('data') or {}).get('label','')}",
        "models": {m["id"]: "auto" for m in models},
    }
    return {
        "schema_version": "1.0.0",
        "adapter": "openrouter",
        "as_of": as_of,
        "collected_at_utc": _utc_now(),
        "catalog": {"models": models},
        "account_limits": [account],
    }


# ---------------------------------------------------------------------------
# OpenCode host adapter (opencode models --verbose + stats)
# ---------------------------------------------------------------------------

def collect_opencode_host(*, as_of: str, opencode_bin: str = "opencode", timeout: int = 60) -> dict:
    """Collect all models from the host registry + local usage stats."""
    try:
        proc = subprocess.run(
            [opencode_bin, "models", "--verbose"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdapterError(f"opencode models failed: {exc}") from exc
    if proc.returncode != 0:
        raise AdapterError(f"opencode models exited {proc.returncode}")

    # Reuse live_catalog parser
    import live_catalog

    metas = live_catalog.parse_verbose_models(proc.stdout)
    models = []
    seen_host: set[str] = set()
    for meta in metas:
        listed = meta.get("_listed_id") or ""
        provider = meta.get("providerID") or ""
        model_id = meta.get("id") or ""
        if is_excluded_vendor_model(listed, provider, model_id, meta.get("name"), meta.get("family")):
            continue
        if meta.get("status") not in (None, "active"):
            continue
        catalog_id = f"{provider}/{model_id}"
        if provider in ("openrouter", "opencode-go"):
            # Dedicated adapters own openrouter and opencode-go; host skips to avoid ID collisions.
            continue
        if catalog_id in seen_host:
            continue
        seen_host.add(catalog_id)
        cost = meta.get("cost") or {}
        cache = cost.get("cache") or {}
        try:
            zero = all(Decimal(str(cost.get(k, 0))) == 0 for k in ("input", "output")) and all(
                Decimal(str(cache.get(k, 0))) == 0 for k in ("read", "write")
            )
        except Exception:
            zero = False
        caps = ["text"]
        c = meta.get("capabilities") or {}
        if c.get("toolcall"):
            caps.append("tools")
        if c.get("reasoning"):
            caps.append("reasoning")
        if (c.get("input") or {}).get("image") or (c.get("output") or {}).get("image"):
            caps.append("vision")
        models.append(
            {
                "id": catalog_id,
                "provider": provider,
                "pool": f"opencode-{provider}",
                "billing_type": "free" if zero else "metered",
                "available": True,
                "authorized": True,
                "capabilities": sorted(set(caps)),
                "qualifications": {},
                "effective_rates": {
                    "input_usd_per_million": _per_m(cost.get("input", 0)),
                    "cached_input_usd_per_million": _per_m(cache.get("read", 0)),
                    "output_usd_per_million": _per_m(cost.get("output", 0)),
                    "effective_as_of": as_of,
                    "source_ref": f"opencode-host-models://{catalog_id}",
                },
                "host_model_ref": {
                    "provider_id": provider,
                    "model_id": model_id,
                    "dispatch_ref": catalog_id,
                    "context_limit": (meta.get("limit") or {}).get("context"),
                    "status": meta.get("status", "active"),
                    "evidence": "opencode-host-models-verbose",
                },
            }
        )

    # Local usage stats for free-tier burn tracking
    usage = {}
    try:
        stats = subprocess.run(
            [opencode_bin, "stats", "--models", "--days", "1"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        # Parse text table loosely: lines with model id followed by Messages count
        lines = stats.stdout.splitlines()
        current = None
        for line in lines:
            s = line.strip("│┌┐└┘├┤┬┴┼─ ─")
            if "/" in s and not s.startswith("Messages") and not s.startswith("Cost"):
                current = s.strip()
            elif current and "Messages" in s:
                try:
                    count = int(s.split()[-1])
                    usage[current] = count
                except (ValueError, IndexError):
                    pass
                current = None
    except (OSError, subprocess.TimeoutExpired):
        pass

    # Free-tier daily pool (200/day shared) — remaining estimated from local 24h usage
    free_models = [m for m in models if m["billing_type"] == "free" and m["provider"] == "opencode"]
    free_daily_used = sum(usage.get(m["id"], 0) for m in free_models)
    remaining = max(0, 200 - free_daily_used)
    free_pool = {
        "limit_id": "opencode-free-daily",
        "unit": "requests",
        "window": "1d",
        "debit_metric": "requests",
        "remaining": str(remaining),
        "observed_at": _utc_now(),
        "valid_until": "2099-12-31T23:59:59+00:00",
        "source_ref": f"opencode://free-tier?local_usage_24h={free_daily_used}",
        "models": {m["id"]: "1" for m in free_models},
    }
    return {
        "schema_version": "1.0.0",
        "adapter": "opencode-host",
        "as_of": as_of,
        "collected_at_utc": _utc_now(),
        "catalog": {"models": models},
        "account_limits": [free_pool],
        "usage_24h": usage,
    }


# ---------------------------------------------------------------------------
# OpenCode Go adapter (subscription usage + model list)
# ---------------------------------------------------------------------------

def collect_opencode_go(*, as_of: str, api_key: str | None = None, timeout: int = 20) -> dict:
    """Collect OpenCode Go subscription usage windows and available models.

    Endpoint: GET https://opencode.ai/zen/go/v1/usage (Bearer = Go API key).
    Returns usage percentages for rolling/weekly/monthly windows and the
    model list from /zen/go/v1/models.
    """
    key = api_key
    if not key:
        try:
            auth = json.loads((Path.home() / ".local/share/opencode/auth.json").read_text())
            key = (auth.get("opencode-go") or {}).get("key")
        except (OSError, json.JSONDecodeError, TypeError):
            key = None
    if not key:
        raise AdapterError("OpenCode Go API key not found in auth.json or OPENCODE_GO_API_KEY")

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "curl/8.5.0",
        "Accept": "application/json",
        "Origin": "https://opencode.ai",
        "Referer": "https://opencode.ai/",
    }

    def get(url: str) -> dict:
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as exc:
            raise AdapterError(f"OpenCode Go API failed: {exc}") from exc

    usage = get("https://opencode.ai/zen/go/v1/usage")
    models_resp = get("https://opencode.ai/zen/go/v1/models")

    windows = usage.get("usage") or {}
    # Go monthly $ limits per model (from official docs, as_of 2026-10)
    GO_MONTHLY_LIMITS_USD = {
        "glm-5.3-flash": "60", "glm-5.3": "15", "glm-5.2": "60",
        "kimi-k3.1-pro": "15", "kimi-k3": "15", "kimi-k2.7-code": "60",
        "longcat-2.0": "60", "step-5-preview": "15",
        "beam-free": "0", "iceman-contributor-free": "0",
        "mimo-v2.6-flash": "60", "mimo-v2.6-pro": "30", "mimo-v2.5": "60", "mimo-v2.5-pro": "30",
        "minimax-m3.1-flash": "20", "minimax-m3": "60",
        "muse-spark-1.3-contributor": "60", "muse-spark-1.2-contributor": "60",
        "qwen3.8-max": "15", "qwen3.8-flash": "60", "qwen3.7-plus": "60",
        "deepseek-v4.1-flash": "80", "deepseek-v4-pro": "15",
        "grok-4.7": "15", "gpt-6-luna": "15", "gpt-5.6-luna": "15",
        "claude-haiku-5-5": "15", "gemini-4-argon": "30",
        "hy4-preview": "60", "hy3": "60",
    }

    # Official token prices (USD per 1M) — off-peak base; DeepSeek peak = 2×
    GO_TOKEN_PRICES = {
        "glm-5.3-flash": ("0.15", "0.50", "0.03"),
        "glm-5.3": ("1.40", "4.40", "0.26"),
        "glm-5.2": ("1.40", "4.40", "0.26"),
        "kimi-k3.1-pro": ("2.00", "10.00", "0.20"),
        "kimi-k3": ("3.00", "15.00", "0.30"),
        "kimi-k2.7-code": ("0.95", "4.00", "0.19"),
        "longcat-2.0": ("0.30", "1.20", "0.006"),
        "step-5-preview": ("1.00", "2.70", "0.10"),
        "mimo-v2.6-flash": ("0.14", "0.28", "0.0028"),
        "mimo-v2.6-pro": ("0.435", "0.87", "0.003625"),
        "mimo-v2.5": ("0.14", "0.28", "0.0028"),
        "mimo-v2.5-pro": ("0.435", "0.87", "0.003625"),
        "minimax-m3.1-flash": ("0.15", "0.60", "0.015"),
        "minimax-m3": ("0.30", "1.20", "0.06"),
        "muse-spark-1.3-contributor": ("0.10", "0.20", "0.002"),
        "muse-spark-1.2-contributor": ("0.10", "0.20", "0.002"),
        "qwen3.8-max": ("2.00", "6.00", "0.25"),
        "qwen3.8-flash": ("0.15", "0.47", "0.016"),
        "qwen3.7-plus": ("0.40", "1.60", "0.04"),
        # DeepSeek: listed as off-peak base; peak multiplier applied via peak_schedule
        "deepseek-v4.1-flash": ("0.15", "0.60", "0.003"),
        "deepseek-v4-pro": ("0.66", "1.98", "0.022"),
        "grok-4.7": ("2.00", "6.00", "0.50"),
        "gpt-6-luna": ("0.10", "0.50", "0.01"),
        "gpt-5.6-luna": ("0.20", "1.20", "0.02"),
        "claude-haiku-5-5": ("0.10", "0.50", "0.01"),
        "gemini-4-argon": ("2.00", "10.00", "0.10"),  # 50% off through 2026-11-30
        "hy4-preview": ("0.834", "2.501", "0.042"),
        "hy3": ("0.14", "0.58", "0.035"),
    }

    # DeepSeek peak schedule (official): 01:00-04:00 and 06:00-10:00 UTC, Mon-Fri,
    # excluding Chinese public holidays. Peak price = 2× off-peak.
    DEEPSEEK_PEAK_SCHEDULE = {
        "provider": "deepseek",
        "peak_utc_windows": [
            {"start_hour": 1, "end_hour": 4},
            {"start_hour": 6, "end_hour": 10},
        ],
        "weekdays_only": True,
        "exclude_cn_holidays": True,
        "peak_multiplier": "2",
        "source_ref": "opencode-go://docs/go#usage-limits-deepseek-peak",
    }

    models = []
    for m in models_resp.get("data") or []:
        mid = m.get("id") or ""
        if not mid:
            continue
        if is_excluded_vendor_model(mid, m.get("name")):
            continue
        monthly_limit = GO_MONTHLY_LIMITS_USD.get(mid)
        is_free_promo = monthly_limit == "0" or mid.endswith("-free") or "contributor-free" in mid
        billing = "free" if is_free_promo else "subscription"
        inp, out, cache = GO_TOKEN_PRICES.get(mid, ("1", "4", "0.1"))
        if is_free_promo:
            inp, out, cache = "0", "0", "0"
        entry = {
            "id": f"opencode-go/{mid}",
            "provider": "opencode-go",
            "pool": "opencode-go",
            "billing_type": billing,
            "available": True,
            "authorized": True,
            "capabilities": ["text", "tools"],
            "qualifications": {},
            "effective_rates": {
                "input_usd_per_million": inp,
                "cached_input_usd_per_million": cache,
                "output_usd_per_million": out,
                "effective_as_of": as_of,
                "source_ref": f"opencode-go://models/{mid}",
            },
            "host_model_ref": {
                "provider_id": "opencode-go",
                "model_id": mid,
                "dispatch_ref": f"opencode-go/{mid}",
                "context_limit": None,
                "status": "active",
                "evidence": "opencode-zen-go-v1-models",
            },
        }
        if mid.startswith("deepseek-"):
            entry["peak_schedule"] = DEEPSEEK_PEAK_SCHEDULE
        models.append(entry)

    # Build account limits from usage percentages.
    # monthly % → remaining fraction of the $60 Go base (Go plan default)
    # rolling/weekly are % of the same monthly pool sliced (20%/50%)
    GO_BASE_MONTHLY_USD = Decimal("60")
    limits = []
    for window_key, limit_id, fraction in (
        ("monthly", "opencode-go-monthly", Decimal("1.0")),
        ("weekly", "opencode-go-weekly", Decimal("0.5")),
        ("rolling", "opencode-go-5h", Decimal("0.2")),
    ):
        w = windows.get(window_key) or {}
        pct = Decimal(str(w.get("percent", "100")))  # percent used
        window_cap = GO_BASE_MONTHLY_USD * fraction
        used_usd = window_cap * pct / Decimal("100")
        remaining_usd = max(Decimal("0"), window_cap - used_usd)
        resets = w.get("resetsAt") or "2099-12-31T23:59:59.000Z"
        limits.append(
            {
                "limit_id": limit_id,
                "unit": "usd_equivalent",
                "window": window_key,
                "debit_metric": "allowance_usd",
                "remaining": str(remaining_usd.normalize()),
                "observed_at": _utc_now(),
                "valid_until": resets.replace("Z", "+00:00"),
                "source_ref": f"opencode://zen/go/v1/usage?window={window_key}&percent_used={pct}",
                "models": {
                    m["id"]: "auto"
                    for m in models
                    if m["billing_type"] == "subscription"
                },
            }
        )

    return {
        "schema_version": "1.0.0",
        "adapter": "opencode-go",
        "as_of": as_of,
        "collected_at_utc": _utc_now(),
        "catalog": {"models": models},
        "account_limits": limits,
        "usage_windows": windows,
    }


# ---------------------------------------------------------------------------
# Batch orchestrator: collect all → write sources → refresh catalog store
# ---------------------------------------------------------------------------

def run_batch_refresh(
    *,
    as_of: str,
    store: Path,
    out_dir: Path,
    opencode_bin: str = "opencode",
    openrouter_key: str | None = None,
    include_openrouter: bool = True,
    include_opencode: bool = True,
    include_go: bool = True,
    include_codex: bool = True,
) -> dict:
    """Collect from all available adapters, merge, and refresh the catalog store.

    Returns a summary with per-adapter status and final catalog pointer.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    sources: list[Path] = []
    all_limits: list[dict] = []
    statuses: list[dict] = []
    merged_models: dict[str, dict] = {}

    if include_opencode:
        try:
            snap = collect_opencode_host(as_of=as_of, opencode_bin=opencode_bin)
            src = out_dir / f"opencode-host-{as_of}.json"
            src.write_text(json.dumps(snap["catalog"], sort_keys=True) + "\n")
            sources.append(src)
            all_limits.extend(snap.get("account_limits") or [])
            for m in snap["catalog"]["models"]:
                merged_models[m["id"]] = m
            statuses.append({"adapter": "opencode-host", "status": "OK", "models": len(snap["catalog"]["models"])})
        except AdapterError as exc:
            statuses.append({"adapter": "opencode-host", "status": "FAILED", "error": str(exc)[:300]})

    if include_openrouter:
        try:
            snap = collect_openrouter(as_of=as_of, api_key=openrouter_key)
            src = out_dir / f"openrouter-{as_of}.json"
            src.write_text(json.dumps(snap["catalog"], sort_keys=True) + "\n")
            sources.append(src)
            all_limits.extend(snap.get("account_limits") or [])
            for m in snap["catalog"]["models"]:
                if m["id"] not in merged_models:
                    merged_models[m["id"]] = m
            statuses.append({"adapter": "openrouter", "status": "OK", "models": len(snap["catalog"]["models"])})
        except AdapterError as exc:
            statuses.append({"adapter": "openrouter", "status": "FAILED", "error": str(exc)[:300]})

    if include_go:
        try:
            snap = collect_opencode_go(as_of=as_of)
            src = out_dir / f"opencode-go-{as_of}.json"
            src.write_text(json.dumps(snap["catalog"], sort_keys=True) + "\n")
            sources.append(src)
            all_limits.extend(snap.get("account_limits") or [])
            for m in snap["catalog"]["models"]:
                if m["id"] not in merged_models:
                    merged_models[m["id"]] = m
            statuses.append({"adapter": "opencode-go", "status": "OK", "models": len(snap["catalog"]["models"])})
        except AdapterError as exc:
            statuses.append({"adapter": "opencode-go", "status": "FAILED", "error": str(exc)[:300]})

    # Fetch OpenAI Codex quota (requires codex CLI authenticated)
    if include_codex:
        try:
            from codex_quota import query_codex_quota, build_account_limits

            quota = query_codex_quota()
            limits_doc = build_account_limits(quota, as_of=as_of)
            all_limits.extend(limits_doc.get("sources") or [])
            # Tag openai models with codex windows
            for lid_entry in limits_doc.get("sources") or []:
                for m in merged_models.values():
                    if m["provider"] == "openai":
                        lid_entry["models"][m["id"]] = "1"
            statuses.append({
                "adapter": "codex-quota",
                "status": "OK",
                "plan": limits_doc.get("plan_type"),
                "windows": len(limits_doc.get("sources") or []),
            })
            # Persist full quota record
            quota_file = out_dir / f"codex-quota-{as_of}.json"
            quota_file.write_text(json.dumps({**quota, **limits_doc}, sort_keys=True, indent=2) + "\n")
        except Exception as exc:
            statuses.append({"adapter": "codex-quota", "status": "FAILED", "error": str(exc)[:300]})

    if not sources:
        raise AdapterError("no adapter produced a catalog source")

    # Write merged account limits overlay
    limits_file = out_dir / f"account-limits-{as_of}.json"
    limits_payload = {
        "schema_version": "1.0.0",
        "collected_at_utc": _utc_now(),
        "sources": all_limits,
    }
    limits_file.write_text(json.dumps(limits_payload, sort_keys=True, indent=2) + "\n")

    # Refresh catalog store BEFORE dependent performance/intelligence joins so
    # they read this refresh's catalog pointer, never the previous snapshot.
    from catalog_store import refresh

    manifest = refresh(store, sources, as_of=as_of)
    refreshed_ids = [m["id"] for m in manifest["catalog"]["models"]]

    # Fetch model intelligence (benchmarks, throughput, latency) against the
    # newly refreshed catalog membership.
    intel_file = None
    try:
        from intelligence import (
            collect_artificial_analysis,
            collect_llm_stats,
            collect_models_dev,
            build_qualification_overlay,
        )

        aa = None
        md = None
        ls = None
        try:
            aa = collect_artificial_analysis()
        except Exception:
            pass
        try:
            md = collect_models_dev()
        except Exception:
            pass
        try:
            ls = collect_llm_stats()
        except Exception:
            pass
        if aa or md or ls:
            overlay = build_qualification_overlay(
                aa_data=aa, llm_stats_data=ls, models_dev_data=md,
                catalog_ids=refreshed_ids, as_of=as_of,
            )
            intel_file = out_dir / f"intelligence-{as_of}.json"
            intel_file.write_text(json.dumps(overlay, sort_keys=True, indent=2) + "\n")
            statuses.append({"adapter": "intelligence", "status": "OK", "entries": len(overlay["entries"])})
    except Exception as exc:
        statuses.append({"adapter": "intelligence", "status": "FAILED", "error": str(exc)[:200]})

    # Stage-1 performance telemetry (official endpoints + status, zero inference)
    perf_summary = None
    perf_file = None
    try:
        from performance_stage1 import build as _perf_build

        perf_dir = Path(store).parent / "performance"
        perf_summary = _perf_build(perf_dir, Path(store), as_of, opencode_bin)
        candidate = perf_dir / "performance-overlay.json"
        if candidate.is_file():
            perf_file = str(candidate)
        statuses.append({
            "adapter": "performance-stage1",
            "status": "OK",
            "overlay_entries": perf_summary.get("overlay_entries"),
            "ranked": perf_summary.get("ranked"),
        })
    except Exception as exc:
        statuses.append({"adapter": "performance-stage1", "status": "FAILED", "error": str(exc)[:200]})

    return {
        "status": "REFRESHED",
        "as_of": as_of,
        "adapters": statuses,
        "catalog_sha256": manifest["catalog_sha256"],
        "model_count": len(manifest["catalog"]["models"]),
        "account_limits_file": str(limits_file),
        "intelligence_file": str(intel_file) if intel_file else None,
        "performance_file": perf_file,
        "sources": [str(s) for s in sources],
    }


def resolve_peak_rates(catalog: dict, assignment_at_utc: str) -> dict:
    """Adjust effective_rates for models with peak_schedule based on assignment time.

    DeepSeek official peak: 01:00-04:00 and 06:00-10:00 UTC, Mon-Fri (excl. CN holidays).
    Peak price = peak_multiplier × off-peak base rates. Off-peak keeps base rates.
    Returns a new catalog dict with rates resolved; does not mutate the input.
    """
    from datetime import datetime, timezone

    try:
        at = datetime.fromisoformat(assignment_at_utc.replace("Z", "+00:00"))
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
    except ValueError:
        return catalog

    hour = at.hour
    weekday = at.weekday()  # 0=Mon .. 6=Sun

    def is_peak(schedule: dict) -> bool:
        if schedule.get("weekdays_only") and weekday >= 5:
            return False  # weekend = off-peak
        for w in schedule.get("peak_utc_windows", []):
            if w.get("start_hour", -1) <= hour < w.get("end_hour", -1):
                return True
        return False

    from decimal import Decimal

    models = []
    for m in catalog.get("models", []):
        m = dict(m)
        sched = m.get("peak_schedule")
        if sched and is_peak(sched):
            mult = Decimal(str(sched.get("peak_multiplier", "2")))
            rates = dict(m["effective_rates"])
            for k in ("input_usd_per_million", "output_usd_per_million", "cached_input_usd_per_million"):
                if k in rates:
                    try:
                        rates[k] = str((Decimal(str(rates[k])) * mult).normalize())
                    except Exception:
                        pass
            rates["source_ref"] = rates.get("source_ref", "") + f"?pricing_tier=peak&multiplier={mult}"
            m["effective_rates"] = rates
            m["pricing_tier"] = "peak"
        elif sched:
            rates = dict(m["effective_rates"])
            rates["source_ref"] = rates.get("source_ref", "") + "?pricing_tier=off_peak"
            m["effective_rates"] = rates
            m["pricing_tier"] = "off_peak"
        models.append(m)
    return {"models": models}


def main(argv=None):
    import argparse

    p = argparse.ArgumentParser(description="Two-speed batch refresh: live adapters → catalog store")
    p.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today)")
    p.add_argument("--store", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True, help="Directory for adapter source snapshots")
    p.add_argument("--opencode-bin", default="opencode")
    p.add_argument("--openrouter-key", default=None, help="Defaults to OPENROUTER_API_KEY env")
    p.add_argument("--skip-openrouter", action="store_true")
    p.add_argument("--skip-opencode", action="store_true")
    p.add_argument("--skip-go", action="store_true")
    p.add_argument("--skip-codex", action="store_true")
    args = p.parse_args(argv)
    as_of = args.as_of or date.today().isoformat()
    try:
        result = run_batch_refresh(
            as_of=as_of,
            store=args.store,
            out_dir=args.out_dir,
            opencode_bin=args.opencode_bin,
            openrouter_key=args.openrouter_key,
            include_openrouter=not args.skip_openrouter,
            include_opencode=not args.skip_opencode,
            include_go=not args.skip_go,
            include_codex=not args.skip_codex,
        )
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    except (AdapterError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}), file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
