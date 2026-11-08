# Public Repository Checklist

This repository is intentionally public at the owner's request.

Before every push:
- Confirm .env and local secret files are ignored.
- Run a secret scanner locally.
- Never commit user videos, images, project files, exports, weights, checkpoints, caches or access tokens.
- Verify that example configuration contains placeholders only.
- Do not publish VPN addresses, server IPs, credentials or private storage URLs.
- Review third-party license files before copying code or weights.
- Keep project license status explicit; public repository visibility is not an open-source license.

If any secret is accidentally committed, rotate/revoke it immediately. Removing it from a later commit does not invalidate the exposed credential.
