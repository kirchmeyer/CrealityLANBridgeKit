# Copilot Instructions

Project intent:
- This project is independent from the Creality Print source repository.
- Focus on custom LAN compatibility and printer-side integration tooling.
- The target model is: keep the stock Creality Print app unchanged and make the printer expose the contract that app already expects.

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
- Target printer host: root@192.168.1.100
- Compatibility backend bind: 127.0.0.1:9001
- Moonraker upstream: http://127.0.0.1:7126
- Public host used by the app-facing flow: 3d.nrvous.io
- Useful logs: /var/log/nginx/access.log, /var/log/nginx/upload-access.log, /tmp/creality_probe_backend_debug.log
- Useful local references: /Applications/Creality Print.app/Contents/Resources/web and ~/Library/Application Support/Creality/Creality Print/7.0/

Debugging shortcuts that save time:
- Reapply the printer stack with ./printer/deploy_probe_backend.sh.
- Run ./scripts/run_contract_check.sh 192.168.1.100 80 for the main front door checks.
- Use python3 scripts/endpoint_contract_check.py --host 192.168.1.100 --port 80 --skip-upload for fast iteration.
- If the app still shows stale state, clear the cache with ./scripts/reset_creality_print_cache.sh --yes --no-launch.
- If a route seems wrong, inspect both nginx and the compatibility backend output before changing more code.

Pitfalls to remember:
- The app is very sensitive to payload shape and route behavior; a contract mismatch can look like a UI bug.
- Cache staleness can make the app appear broken even when the backend is correct.
- The most valuable evidence is often the live response body from /info, /protocal.csp, and the relevant media/record routes.
- Keep the compatibility work focused on contract matching rather than broad, speculative nginx changes.

Documentation expectations:
- Update docs when behavior or contracts change.
- Keep commands and paths copy/paste-ready for later reuse.
- Prefer short, practical handoff notes that explain the current printer target, the relevant logs, and the quickest recovery steps.
