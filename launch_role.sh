#!/usr/bin/env bash
# Lanza un servicio configurable: arbitro, cerebro u orquestador.
set -euo pipefail
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROLE="${1:-cerebro}"
CONFIG="$BASE_DIR/config.json"
[[ "$ROLE" =~ ^(arbitro|cerebro|orquestador)$ ]] || { echo "Rol inválido: $ROLE" >&2; exit 1; }
json_field() {
  python3 - "$CONFIG" "$ROLE" "$1" <<'PY'
import json, sys
cfg=json.load(open(sys.argv[1], encoding="utf-8"))
service=cfg.get("runtime",{}).get("services",{}).get(sys.argv[2],{})
print(service.get(sys.argv[3], ""))
PY
}
export PATH=/opt/rocm-7.2.4/bin:/usr/local/bin:$PATH
export LD_LIBRARY_PATH=/usr/local/lib:/opt/rocm-7.2.4/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
export HSA_OVERRIDE_GFX_VERSION="${HSA_OVERRIDE_GFX_VERSION:-11.5.0}"
export HIP_VISIBLE_DEVICES="${HIP_VISIBLE_DEVICES:-0}"
LLAMA_BIN="${LLAMA_BIN:-/home/ignacia/llama-rocmfpx/build-strix-rocmfp4/bin/llama-server}"
[[ -x "$LLAMA_BIN" ]] || { echo "No encuentro: $LLAMA_BIN" >&2; exit 1; }
MODEL_NAME="$(json_field model)"; MODEL_PATH="$(json_field model_path)"; MMPROJ="$(json_field mmproj)"
PORT="$(json_field port)"; CTX="$(json_field context)"; PORT="${PORT:-8205}"; CTX="${CTX:-262144}"
if [[ -z "$MODEL_PATH" ]]; then
  case "$MODEL_NAME" in
    ornith-35b) MODEL_PATH="${ORNITH_MODEL:-$HOME/models/ornith-uncensored/Ornith-1.5-35B-A3B-Abliterated-Q8_0.gguf}"; MMPROJ="${MMPROJ:-${ORNITH_MMPROJ:-$HOME/models/ornith-uncensored/mmproj-Ornith-1.5-35B-A3B-Abliterated-F16.gguf}}" ;;
    qwen3.6-35b|qwen-35b) MODEL_PATH="${QWEN35_MODEL:-$HOME/models/qwen35/Qwen3.6-35B-A3B.gguf}" ;;
    *) MODEL_PATH="$MODEL_NAME" ;;
  esac
fi
[[ -f "$MODEL_PATH" ]] || { echo "Modelo no encontrado para $ROLE: $MODEL_PATH" >&2; exit 1; }
ARGS=("$LLAMA_BIN" -m "$MODEL_PATH" -c "$CTX" --fit off -ngl 999 -b 2048 --ubatch-size 1024 --flash-attn on --cache-type-k q8_0 --cache-type-v q8_0 --host 127.0.0.1 --port "$PORT" --predict 32768 --temp 1.0 --top-k 64 --top-p 0.95 --min-p 0.0 --repeat-penalty 1.0 --parallel 1 --metrics --jinja --reasoning on --reasoning-format auto --reasoning-budget 16384 --chat-template-kwargs '{"preserve_thinking":true}' --no-warmup --no-context-shift --cache-prompt --no-mmap)
[[ -f "$MMPROJ" ]] && ARGS+=(--mmproj "$MMPROJ")
LOG="/tmp/${ROLE}_${PORT}.log"
if curl -s -m 1 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then echo "[$ROLE:$PORT] ya está vivo"; exit 0; fi
printf '[%s:%s] modelo=%s contexto=%s\n' "$ROLE" "$PORT" "$MODEL_PATH" "$CTX"
printf -v CMD_STR '%q ' "${ARGS[@]}"
sg render -c "$CMD_STR" >"$LOG" 2>&1 & PID=$!
for _ in $(seq 1 240); do
  if curl -s -m 1 "http://127.0.0.1:${PORT}/health" 2>/dev/null | grep -q ok; then echo "[$ROLE:$PORT] OK PID=$PID log=$LOG"; exit 0; fi
  kill -0 "$PID" 2>/dev/null || { echo "[$ROLE:$PORT] proceso muerto; log=$LOG" >&2; tail -n 40 "$LOG" >&2 || true; exit 1; }
  sleep 1
done
echo "[$ROLE:$PORT] SIN health tras 240s; log=$LOG" >&2
exit 1
