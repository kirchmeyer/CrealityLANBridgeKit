# Printer-Only Migration Checklist

## Goal
Move toward a printer-centric compatibility stack so the stock desktop app remains unchanged.

## Baseline
- Printer has nginx fronting local services.
- Compatibility backend is running (python script or service wrapper).
- Moonraker is reachable from compatibility backend.

## Required Endpoint Contracts
- GET /info
- GET /machine/system_info
- GET /machine/multi_machine
- GET /printer/objects/query
- POST /printer/print/start
- POST /server/files/upload
- POST /upload/*
- GET /api/v1/device/status
- GET /cxy/v1/status
- Device-status responses should expose identity fields at the top level of result and keep the nested device object for compatibility.

## Validation Matrix
1. Discovery
- App can add printer by LAN.
- Printer shows online.
- Model/name fields show correctly.

2. Upload
- Send Only dispatches.
- Start Print dispatches.
- Printer receives upload POST.
- Upload response matches app expectations.

3. Transition
- App enters print view without crash.
- Printer starts print.

4. Regression
- Retest after app update.
- Do not patch the stock app bundle; keep the fix printer-side only.

## nginx Guidance
- Keep nginx thin: TLS + route forwarding + websocket upgrade.
- Put schema/behavior adaptation in compatibility backend.
- Avoid huge location lists; use focused explicit routes plus controlled fallback prefixes.

## Release Workflow
1. Update app.
2. Retest stock app against printer-only stack.
3. If regression appears, restore known-good client snapshot.
4. Record new behavior delta and update contract docs.
