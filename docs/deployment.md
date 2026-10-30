# Deployment

Coolify app UUID: buhp7a6bipxb9pb50me711wl
Project UUID: sejk5tyw2y2i6xheotx35rw5
Environment: production

Required env vars:
- MODEL_PICKER_TOKEN
- RELATE_HOST_BASE=/host/relate-skills
- MODEL_PICKER_DATA=/app/data

Required storage mount:
- host: /data/working/opencode/relate-skills
- container: /host/relate-skills
