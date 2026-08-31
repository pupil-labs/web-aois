# Web AOIs Neon Player Plugin

This folder contains the Neon Player analysis plugin.

## Why this location
- Keep `src/` for installable package code (`pupil_labs.web_aois`).
- Keep Neon Player integration code separate from the core recording app.

## Install/Use in Neon Player
1. Copy this folder (`web_aois_plugin`) into your Neon Player plugins directory.
2. Restart Neon Player.
3. Open a recording and set plugin properties:
   - `aoi_definitions_file`
   - optionally `event_log_file` if using sidecar browser events CSV.

## Typical sidecar file
- `web-events.csv` written next to your selected `web-aois.json` by `pl-web-aois-app` recording mode.
