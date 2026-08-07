# Copilot Instructions

Project intent:
- This project is independent from the Creality Print source repository.
- Focus on custom LAN compatibility and printer-side integration tooling.
- The target model is: keep the stock Creality Print app unchanged and make the printer expose the contract that app already expects.
- Compatibility: developed and tested on a Creality K2 Plus. Other Creality models may need adaptation; use at your own risk.

Preferred decision order:
1. Prefer printer-side/service-side fixes first (nginx + compatibility backend + endpoint contracts).
2. Treat the desktop app bundle as a reference source, not a thing to patch, unless the user explicitly asks for a fallback path.
3. Keep any fallback work minimal, reproducible, and clearly isolated.

Workflow preferences:
- Preserve reproducibility with snapshot/export/restore scripts and clear handoff notes.
- Validate changes with endpoint contract checks before assuming the issue is solved.
- Favor small, explicit route maps and schema normalization in backend logic over large nginx location sprawl.
- When a behavior change is made, re-check the live printer response and the app cache state.

Operational guidance:
- When diagnosing send failures, verify printer-side logs and endpoint behavior first.
- Treat app updates as expected regression points: test stock behavior first, then decide whether a printer-side contract fix is sufficient.
- Capture known-good states whenever a full print flow succeeds.
- The fastest path in this project usually comes from combining four sources: printer logs, the stock app bundle source tree, the live compatibility backend responses, and the app cache reset workflow.
- Under no circumstances, and without an explicit YES from the user, may the macOS Creality Print app be shimmed, patched, or modified in any way other than purely clearing its cache. If the intended action is uncertain, stop and ask.

Printer-specific context:
- Target printer host: read from `$PRINTER_HOST` / `$PRINTER_USER` (live testing default: root@192.168.1.100).
- LAN bridge backend bind: 127.0.0.1:9002 (WebSocket fronted by nginx on 9999)
- Moonraker upstream: http://127.0.0.1:7126
- Public host used by the app-facing flow: read from `$PUBLIC_HOST` (live testing default: printer.lan).
- Operational status page: `http://$PUBLIC_HOST/$STATUS_PATH/`
- Backup manifest on printer: /etc/${PROJECT_NAME}_backup_manifest.json
- Useful logs: /var/log/nginx/access.log, /var/log/nginx/upload-access.log, /var/log/lan_bridge.log, /var/log/${PROJECT_NAME}_watchdog.log, logread, /tmp/cam_app_solo.log, /tmp/cam_delivery_bridge.log, /tmp/mjpeg_server_solo.log, /tmp/go2rtc_solo.log
- All service logs are ECS-compliant JSON lines by default; set `ECS_LOGGING=0` for plain text.
- Useful local references: /Applications/Creality Print.app/Contents/Resources/web and ~/Library/Application Support/Creality/Creality Print/7.0/

Environment defaults:
- `PRINTER_HOST` (default: `printer.lan`)
- `PRINTER_USER` (default: `root`)
- `PUBLIC_HOST` (default: `printer.lan`)
- `CERT_BASENAME` (default: `self-signed`)
- `ECS_LOGGING` (default: `1`)
- `PROJECT_NAME` (default: `bridge`)
- `STATUS_PATH` (default: `$PROJECT_NAME-status`)

Debugging shortcuts that save time:
- Set environment defaults once: `export PRINTER_HOST=192.168.1.100 PRINTER_USER=root PUBLIC_HOST=printer.lan CERT_BASENAME=self-signed`
- Reapply the printer stack with `./install.sh install`.
- Sync only changed files with `./install.sh sync`.
- Check sync and service state with `./install.sh status`.
- Restart just the camera stack with `/etc/init.d/go2rtc restart` on the printer.
- Run `./scripts/run_contract_check.sh` for the main front door checks.
- Use `python3 scripts/endpoint_contract_check.py --skip-upload` for fast iteration.
- Use `python3 scripts/check_local_remote_sync.py` to verify local/remote file drift.
- If the app still shows stale state, clear the cache with `./scripts/reset_creality_print_cache.sh --yes --no-launch`.
- If a route seems wrong, inspect both nginx and the LAN bridge output before changing more code.

Pitfalls to remember:
- The app is very sensitive to payload shape and route behavior; a contract mismatch can look like a UI bug.
- Cache staleness can make the app appear broken even when the backend is correct.
- The most valuable evidence is often the live response body from /info, /protocal.csp, and the relevant media/record routes.
- Keep the compatibility work focused on contract matching rather than broad, speculative nginx changes.

Documentation expectations:
- Update docs when behavior or contracts change.
- Keep commands and paths copy/paste-ready for later reuse.
- Prefer short, practical handoff notes that explain the current printer target, the relevant logs, and the quickest recovery steps.
