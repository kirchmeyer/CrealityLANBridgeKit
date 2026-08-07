# CrealityLANBridgeKit backlog

This file tracks simplifications, improvements, and future ideas that have been
identified but not yet implemented. Items are intentionally not prioritized;
pick the one that solves the current pain point.

## Infrastructure simplification

1. **Remove duplicate init files** ✅
   - Removed legacy duplicate init files.
   - Canonical `.init.sh` versions remain in `printer/` and are deployed by the install/sync scripts.
   - Rationale resolved: drift between duplicate init files is no longer possible.

2. **Unify deployment into a single installer** ✅
   - Created `install.sh` with `install`, `restore`, `uninstall`, `sync`, and `status` subcommands.
   - Backs up stock config to `/etc/${PROJECT_NAME}_backup_manifest.json` on first install.
   - Uses `scripts/check_local_remote_sync.py` for idempotent file deployment.
   - Handles service enable/disable, restarts, and endpoint contract verification.
   - Legacy scripts (`deploy_lan_bridge.sh`, `deploy_nginx_frontdoor.sh`, `deploy_mdns_announcer.sh`, `deploy_status_page.sh`) remain for now but `install.sh` is the primary path.

3. **Deduplicate `creality_mdns_announcer.py`** ✅
   - Removed the duplicate `scripts/creality_mdns_announcer.py`.
   - Canonical copy remains in `printer/creality_mdns_announcer.py`.
   - `printer/deploy_mdns_announcer.sh` already copies from the canonical path.

4. **Remove app-patching utilities** ✅
   - Removed scripts that patched the macOS Creality Print app bundle.
   - Project policy remains printer-side changes only; the desktop app is treated as a reference source.

5. **Add bounded respawn to moonraker/klipper inits**
   - Currently `moonraker` and `klipper` init scripts do not respawn.
   - If they crash, the printer becomes unreachable. Consider adding bounded `procd_set_param respawn`.
   - Caution: blindly respawning Klipper on a motion fault could be dangerous; investigate first.

## Status page ideas

6. **Grouped health cards by subsystem**
   - Group services into "Network / Front door", "Camera stack", "LAN bridge", "Printer core".
   - Makes it faster to see which subsystem is unhealthy.

7. **Traffic-light summary header**
   - One large overall badge at the top: green = all checks pass, yellow = non-critical issue, red = user-facing outage.
   - Useful when glancing at the page from across the room.

8. **Auto-refresh and last-updated timestamp**
   - Add a `<meta refresh>` or small JS fetch that updates the page every 30 s.
   - Show "last updated X seconds ago" so stale data is obvious.

9. **Sparkline / recent-history graphs**
   - Keep a rolling in-memory buffer of load, memory, and endpoint latency.
   - Render tiny SVG sparklines so trends (e.g., load climbing before a crash) are visible.

10. **Prometheus-compatible metrics endpoint**
    - Expose `/${STATUS_PATH}/metrics` with simple `key value` pairs or OpenMetrics format.
    - Allows external monitoring (Grafana, Uptime Kuma, etc.) without parsing HTML.

11. **Collapsible log sections**
    - The log tail cards take a lot of vertical space. Make them collapsible so the service grid is visible first.

12. **Per-service dependency graph (Mermaid or SVG)**
    - A small diagram showing how cam_app → delivery_bridge → go2rtc → mjpeg_server → nginx → app flows.
    - Color nodes by health.

## Hardening

13. **Add health-check endpoint to lan_bridge.py**
    - A cheap `/healthz` endpoint that returns 200 when Moonraker is reachable would let the watchdog use HTTP instead of port probes.

14. **Log rotation for `/var/log/lan_bridge.log`**
    - The file can grow unbounded. Add `logrotate` config or size-based rotation inside the service.

15. **Structured logging (JSON)** ✅
   - Converted lan_bridge.py, status_page.py, mjpeg_server.py, cam_delivery_bridge.py, creality_mdns_announcer.py, and webrtc_local_bridge.py to emit ECS 8.11.0 JSON lines.
   - Converted watchdog.sh and restart_cam_stack.sh shell logs to ECS JSON.

## Documentation

16. **Single "first-time setup" walkthrough**
    - Merge the install steps from README.md, INSTALL_AND_RESTORE.md, and SESSION_HANDOFF.md into one coherent doc.

17. **Decision record for why each stock process was kept or removed**
    - Document the reasoning behind `app_cloud_only`, killing `Monitor`, retiring `cloud_webrtc_bridge`, etc.

18. **Troubleshooting flowchart**
    - A visual decision tree: "App can't find printer → check mDNS", "Send fails → check /upload", "Camera blank → check go2rtc streams", etc.

## Active work items

19. **Ensure local and remote files match** ✅
    - Built `scripts/check_local_remote_sync.py` that maps canonical local files to deployed printer paths.
    - Compares SHA-256 digests and reports drift; supports `--sync` to push only changed files.
    - Verified all 20 tracked files match on `192.168.1.100` after sync.

20. **Create automated install with backup/restore/uninstall**
    - Build a single `install.sh` that backs up stock config, installs the full stack, and can restore/uninstall.
    - Replaces the separate `deploy_lan_bridge.sh`, `deploy_nginx_frontdoor.sh`, `deploy_mdns_announcer.sh`, and `deploy_status_page.sh` scripts.
    - Should integrate with `check_local_remote_sync.py` for idempotent deployments.

## Deferred / future ideas

21. **Explore alternative status page display concepts**
    - Evaluate different ways to present stack health: grouped cards, traffic-light summary, auto-refresh, sparklines, Prometheus metrics endpoint, collapsible logs, dependency graph.
    - Decide which concepts are worth implementing and update the status page accordingly.
    - Deferred until the installer and docs are in place.
