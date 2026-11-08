#!/usr/bin/env bash
set -uo pipefail

API_BASE_URL="${API_BASE_URL:-http://127.0.0.1:8000}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
COMFYUI_BASE_URL="${COMFYUI_BASE_URL:-http://127.0.0.1:8188}"
RUN_ARABIC_INFERENCE_SMOKE_TEST="${RUN_ARABIC_INFERENCE_SMOKE_TEST:-false}"
failed=0

section() { printf "\n== %s ==\n" "$1"; }
fail() { printf "FAIL: %s\n" "$1" >&2; failed=1; }

section "GPU driver"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi || fail "nvidia-smi failed"
else
  fail "nvidia-smi not found; NVIDIA GPU not verified"
fi

section "Docker"
docker version >/dev/null 2>&1 || fail "Docker Engine unavailable"
docker compose version || fail "Docker Compose unavailable"
docker compose config --quiet || fail "Compose configuration invalid"

section "Studio API"
curl --fail --silent --show-error "$API_BASE_URL/ready" || fail "Studio API not ready"
curl --fail --silent --show-error "$API_BASE_URL/v1/models/status" | python -m json.tool || fail "Model status not available"

section "Ollama"
if tags="$(curl --fail --silent --show-error "$OLLAMA_BASE_URL/api/tags")"; then
  printf '%s\n' "$tags" | python -m json.tool
  printf '%s' "$tags" | python -c 'import json,sys; names={m.get("name") for m in json.load(sys.stdin).get("models",[])}; sys.exit(0 if "qwen3.5:9b-q4_K_M" in names else 1)' || fail "Required Qwen tag is not installed"
else
  fail "Ollama is not reachable at $OLLAMA_BASE_URL"
fi

section "ComfyUI"
if stats="$(curl --fail --silent --show-error "$COMFYUI_BASE_URL/system_stats")"; then
  printf '%s\n' "$stats" | python -m json.tool
  printf '%s' "$stats" | python -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("devices") else 1)' || fail "ComfyUI reports no compute device"
else
  fail "ComfyUI is not reachable at $COMFYUI_BASE_URL"
fi

if [ "$RUN_ARABIC_INFERENCE_SMOKE_TEST" = "true" ]; then
  section "Qwen Arabic inference"
  payload='{"model":"qwen3.5:9b-q4_K_M","stream":false,"messages":[{"role":"user","content":"اكتب كلمة تم فقط باللغة العربية."}],"options":{"num_ctx":1024,"temperature":0}}'
  curl --fail --silent --show-error -H 'Content-Type: application/json' -d "$payload" "$OLLAMA_BASE_URL/api/chat" | python -c 'import json,sys; text=json.load(sys.stdin).get("message",{}).get("content",""); print(text); sys.exit(0 if text.strip() else 1)' || fail "Qwen Arabic inference failed"
fi

section "Result"
if [ "$failed" -ne 0 ]; then
  echo "Runtime acceptance is NOT complete."
  exit 1
fi
echo "Runtime preflight passed. Approved ComfyUI workflow inference must still be tested."
