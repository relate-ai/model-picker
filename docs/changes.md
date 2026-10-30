# Change Log

## Catalog unit bug fix

File: `/data/working/opencode/relate-skills/sources/relate-integrated-b8eb5ee2913d9b3e/relate-model-optimizer/scripts/provider_adapters.py`

Problem: `_per_m()` multiplied host-reported USD-per-million values by 1,000,000, producing implausible rates such as `1.2E+6` USD/M.

Fix: `_per_m()` now returns the host value directly because `opencode models --verbose` reports USD per 1M tokens.

## Dashboard

- Added FastAPI backend with catalog, models, refresh status, routing matrix, telemetry events/summary endpoints.
- Added frontend search, filters, sorting, model detail, compare, routing inspector, telemetry charts, refresh health.
- Fixed Authorization header handling for telemetry endpoints.
- Fixed mobile horizontal overflow.

## Telemetry

- Normalized relate-run invocations into `telemetry-events.jsonl`.
- Added host-export-derived invocation records.
- Added model/provider/day/mission aggregates in dashboard.

## Routing

- Added 19-case routing matrix with REAL and DRY_RUN/NEGATIVE cases.
- Recorded relate-run executions and fallback (rate-limited route quarantined).

## Deployment

- Coolify app UUID: `buhp7a6bipxb9pb50me711wl`
- DNS: `model-picker.relate-ai.site`
- Host mount: `/data/working/opencode/relate-skills` -> `/host/relate-skills`

## Visual validation

- Playwright repository: https://github.com/relate-ai/model-picker-playwright
- All checks passed, including mobile overflow.

## Visual verification

Playwright repository: https://github.com/relate-ai/model-picker-playwright

Verified:
- desktop and mobile rendering
- 213 catalog rows
- every provider, billing, and capability filter against API counts
- search for every model id
- all sort options
- model detail for sampled models across providers
- compare and clear compare
- 19 routing cases
- telemetry locked without token and unlocked with token
- telemetry row presence
- mobile horizontal overflow

Result: ALL VISUAL CHECKS PASSED.
