# Relate Model Picker

Bespoke model catalog, comparator, routing inspector, and telemetry dashboard for Relate.AI.

Live URL: https://model-picker.relate-ai.site

## What this repository contains

- `app/main.py`: FastAPI backend serving the live catalog, telemetry, routing matrix, and refresh status.
- `static/`: Frontend dashboard (search, filter, sort, compare, detail, telemetry, refresh health).
- `Dockerfile`: Coolify deployment image.
- `data/`: Bundled fallback catalog snapshot and routing matrix.

## Live data sources

The deployed app reads from the host-mounted Relate skills directory:

- Catalog: `/host/relate-skills/dispatch/catalog/current.json`
- Telemetry: `/host/relate-skills/dispatch/telemetry-events.jsonl` plus `*-host-export.json`
- Routing matrix: `/host/relate-skills/dispatch/routing-matrix.json`
- Refresh state: `/host/relate-skills/refresh/state.json`

## Catalog refresh

Twice-daily refresh is configured through:

- `/data/working/opencode/relate-skills/bin/relate-refresh-catalog.sh`
- `/data/working/opencode/relate-skills/bin/relate-refresh-supervisor.py`
- loop config: `/data/working/opencode/relate-skills/refresh/loop.json` (interval 43200 seconds)

Manual refresh:

```bash
/data/working/opencode/relate-skills/bin/relate-refresh-catalog.sh
```

## Provider routes

Qualified routes:

- OpenCode Console
- OpenCode Go
- OpenRouter
- OpenAI Codex

## Deployment

- Coolify project: `Web Apps` (`sejk5tyw2y2i6xheotx35rw5`)
- Coolify app UUID: `buhp7a6bipxb9pb50me711wl`
- DNS: `model-picker.relate-ai.site` -> `37.60.235.136` (Hostinger)
- Storage mount: `/data/working/opencode/relate-skills` -> `/host/relate-skills`

## Verification

Playwright validation repository: https://github.com/relate-ai/model-picker-playwright

All visual checks passed, including mobile overflow.
