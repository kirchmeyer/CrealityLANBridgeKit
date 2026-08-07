# Security Policy

## Supported versions

This is an unofficial compatibility project maintained in spare time. Security fixes are applied to the latest `main` branch as quickly as possible.

| Version | Supported          |
| ------- | ------------------ |
| main    | :white_check_mark: |
| older   | :x:                |

## Reporting a vulnerability

If you discover a security issue that could affect users of this project, please report it privately by email rather than opening a public issue.

Email: **ostrich.toehold58@icloud.com**

Pull requests and regular issues are otherwise welcome on GitHub. Please include:

- A clear description of the issue.
- Steps to reproduce, or a minimal proof of concept if possible.
- The component involved (installer, nginx config, compatibility backend, camera stack, etc.).
- Any suggested mitigation.

I will acknowledge receipt within a few days and share a timeline for a fix or mitigation.

## Scope and expectations

This project modifies a Creality printer's OpenWrt installation and runs as `root`. By design it:

- Disables the stock Creality `app` service to free ports 80/443.
- Installs custom init scripts, Python services, and an nginx front-door configuration.
- May generate or install TLS certificates on the printer.

You should only install this on a printer you own and control, and you should understand that it is an unofficial compatibility layer, not a Creality-supported product.

## Common-sense precautions

- Do not expose the printer or the optional local HTTPS proxy directly to the public internet without additional hardening.
- Use strong SSH keys for printer access; password-based root SSH is strongly discouraged.
- Keep your printer on a trusted LAN segment, or put it behind a firewall with only the ports you need exposed.
- Review `install.sh` and the generated nginx config before running them on your printer.
- If you find a real certificate/private key committed to this repository by accident, please report it immediately.
