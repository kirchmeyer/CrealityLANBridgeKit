# Project Summary

This repository is a printer-side compatibility layer for the stock Creality Print macOS app. The core idea is simple:

- Keep the app stock.
- Make the printer expose the routes and payloads that the app already expects.
- Use nginx and a small Python compatibility backend on the printer to bridge the gap.

## Mental model

The app is not being patched. Instead, the printer is made to behave like a compatible Creality endpoint.

The working path is:

1. Inspect the stock app’s expected contract from the bundled web assets.
2. Mirror the important routes and payload shapes on the printer.
3. Verify the frontend-facing endpoints with contract checks.
4. If the app still shows stale state, clear its local cache and rehydrate from the live printer.

## The important moving pieces

- Printer-side compatibility backend: printer/creality_probe_backend.py
- Printer route template: printer/nginx.compat.example.conf
- Deployment script: printer/deploy_probe_backend.sh
- Contract checks: scripts/endpoint_contract_check.py and scripts/run_contract_check.sh
- Local app bundle source tree: /Applications/Creality Print.app/Contents/Resources/web
- Local app state cache: ~/Library/Application Support/Creality/Creality Print/7.0/
- Snapshot reference: snapshots/20260729_181350/

## Why this approach worked quickly

The fastest breakthroughs came from combining four things:

- Live printer logs from nginx and the compatibility backend.
- The app bundle source tree for the stock app, which revealed the exact contract the app was expecting.
- Contract-check scripts that let changes be verified without guessing.
- A cache-reset step for the stock app whenever behavior changed, because the app otherwise reuses stale device state.

## Current printer-specific context

- Target printer: root@192.168.1.100
- Compatibility backend bind: 127.0.0.1:9001
- Moonraker upstream: http://127.0.0.1:7126
- Public host used by the app flow: 3d.nrvous.io
- Debug log: /tmp/creality_probe_backend_debug.log

## Pitfalls to avoid

- Do not patch the Creality Print app bundle unless the user explicitly approves that fallback path.
- Do not assume one endpoint is enough; the app often needs coordinated support across info, detail, poll-state, media, record, and camera routes.
- Always clear the app cache after a significant payload-shape change, otherwise the app may continue using stale cached state.
- Keep the compatibility changes focused on contract matching; over-broad nginx rules are harder to reason about.

## Good first moves when picking this up again

1. Run the deploy script.
2. Run the endpoint contract checks.
3. Inspect the live debug log and the printer’s nginx/access logs.
4. Compare the live payloads to the stock app bundle expectations.
5. Clear the Creality Print app cache if the UI still looks stale.

## Known issues / what is still not quite right

- The printer-side compatibility layer is now much closer to the app’s expectation, but some UI behavior can still feel inconsistent until the app cache is cleared and the printer is re-queried.
- The detail/media/record routes are covered, but the remaining risk is that one route still behaves slightly differently from the stock app’s expectation even when the main payloads look correct.
- The project has already tried the fastest recovery path: redeploy the backend, re-check the live response bodies, and reset the app cache. Those steps are usually the quickest way to separate a route mismatch from a stale-client problem.
