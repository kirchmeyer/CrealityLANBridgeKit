# Contributing

Thanks for your interest in CrealityLANBridgeKit.

This project is a printer-side compatibility layer for the stock Creality Print
macOS app. The core constraint is: **keep the desktop app unchanged**. We fix
and extend behavior on the printer side, in nginx, and in the compatibility
backend, but we do not patch the Creality Print app bundle.

## How to contribute

1. Open an issue first for larger changes so we can agree on direction.
2. Fork the repo and create a branch.
3. Make focused changes with clear commit messages.
4. Test against a live printer when possible (`./scripts/run_contract_check.sh`).
5. Keep documentation in sync with code changes.
6. Open a pull request and reference the issue.

## What we especially welcome

- Fixes for LAN protocol contract mismatches on other Creality printer models.
- Cleanups and documentation improvements.
- Additional Home Assistant / Homebridge integration notes.
- Hardening for the OpenWrt/nginx deployment path.

## What we will not accept

- Patches to the Creality Print desktop app bundle.
- Removal of printer-side fallbacks without an equivalent client-side-safe replacement.
- Secrets, personal printer data, or copyrighted stock app assets.

## Security

See [SECURITY.md](SECURITY.md) for how to report vulnerabilities privately.
