# Windows Setup and First Push

## Prerequisites
- Git for Windows.
- GitHub CLI (gh).
- PowerShell 7 or Windows PowerShell 5.1.
- Docker Desktop with the Compose plugin for local control-plane development.

## Clone
    git clone https://github.com/aseyam11-png/AI-VIDEO-STUDIO.git
    cd AI-VIDEO-STUDIO

## Local control-plane setup
1. Copy .env.example to .env.
2. Replace all placeholder values with unique random secrets.
3. Run docker compose config to validate the Compose file.
4. Run docker compose up --build.
5. Open http://127.0.0.1:8000/health.

The web UI is a separate Next.js scaffold under apps/studio-web. It is not yet connected to the API.

## Safety
- This repository is public.
- Never commit .env, tokens, media, weights, model caches or exports.
- Do not expose database, broker, Ollama, ComfyUI or worker ports publicly.
- Do not rent GPU time until the provider price, security model and monthly cap are verified.
