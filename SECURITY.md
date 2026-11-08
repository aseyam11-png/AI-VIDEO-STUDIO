# Security Policy

## Repository visibility

This repository is public. Never commit API keys, passwords, private media, model weights, access tokens, private URLs, or generated customer assets.

## Deployment requirements

- Keep API, PostgreSQL, Valkey, Ollama, ComfyUI, and worker ports private.
- Use a private VPN and strong authentication for remote access.
- Do not provide agents with unrestricted shell access.
- Route every tool call through schema validation, path allowlists, timeouts, and audit logging.
- Require human approval for paid generation, external uploads, destructive operations, and final export.
- Treat media metadata, subtitles, OCR text, and embedded prompts as untrusted data.
- Scan uploads and enforce file size, content type, and project-scoped storage.
- Store secrets outside Git and rotate any credential that may have been exposed.

## Reporting

Do not publish exploit details or sensitive operational data in public issues. Contact the repository owner privately.
