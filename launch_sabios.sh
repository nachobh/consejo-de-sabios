#!/usr/bin/env bash
# Consejo de Sabios2: Sabios dinámicos mezcla Ornith-9B / Qwen3.5-9B.
# Uso: launch_sabios.sh [N] | NUM_SABIOS=N launch_sabios.sh
#   N = número de sabios a levantar (1-12, default 4)
#   Pares (0,2,4..) -> Ornith-9B (mradermacher i1-GGUF Q4_K_M)
#   Impares (1,3,5..) -> Qwen3.5-9B Heretic (DavidAU Q4_K_M)
# Cada sabio tiene su system prompt inyectado por pipeline.py (no flag --system).
# LLAMA_BIN="${LLAMA_BIN:-/home/ignacia/llama-rocmfpx/build-strix-rocmfp4/bin/llama-server}"
set -euo pipefail
export PATH=/opt/rocm-7.2.4/bin:/usr/local/bin:$PATH
export LD_LIBRARY_PATH=/usr/local/lib:/opt/rocm-7.2.4/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
export HSA_OVERRIDE_GFX_VERSION=11.5.0
export HIP_VISIBLE_DEVICES=0
LLAMA_BIN="${LLAMA_BIN:-/home/ignacia/llama-rocmfpx/build-strix-rocmfp4/bin/llama-server}"
[[ -x "$LLAMA_BIN" ]] || { echo "No encuentro: $LLAMA_BIN" >&2; exit 1; }

# ── resolver N ──────────────────────────────────────────────────────────
# Prioridad: arg[1] > NUM_SABIOS env > config file > 4
CONFIG_FILE="$(dirname "$0")/config.json"
NUM_SABIOS_ARG="${1:-}"
if [[ -n "$NUM_SABIOS_ARG" ]]; then
  NUM_SABIOS="$NUM_SABIOS_ARG"
elif [[ -n "${NUM_SABIOS:-}" ]]; then
  NUM_SABIOS="$NUM_SABIOS"
elif [[ -f "$CONFIG_FILE" ]]; then
  NUM_SABIOS=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('num_sabios',4))" 2>/dev/null || echo 4)
else
  NUM_SABIOS=4
fi
# sanitizar
if ! [[ "$NUM_SABIOS" =~ ^[0-9]+$ ]] || (( NUM_SABIOS < 1 || NUM_SABIOS > 12 )); then
  echo "NUM_SABIOS debe ser 1-12 (recibido: $NUM_SABIOS)" >&2; exit 1
fi
echo "=== Sabios2: levantando $NUM_SABIOS sabio(s) (mezcla Ornith/Qwen) ==="

service_field() {
  python3 -c 'import json,sys; c=json.load(open(sys.argv[1])); print(c.get("runtime",{}).get("services",{}).get(sys.argv[2],{}).get(sys.argv[3],""))' "$CONFIG_FILE" "sabi$1" "$2"
}

# ── modelos ─────────────────────────────────────────────────────────────
# Qwen3.5-9B Heretic (DavidAU)
QWEN_MODEL="$HOME/models/models--DavidAU--Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-NEO-IMATRIX-MAX-MTP-GGUF/snapshots/def4849b209b3ad226e4761585b74b04d8da743c/Qwen3.5-9B-The-Defiant-Fable-Uncnr-Heretic-NEO-MAX-MTP-Q4_K_M.gguf"
QWEN_MMPROJ="$HOME/models/models--DavidAU--Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-NEO-IMATRIX-MAX-MTP-GGUF/snapshots/def4849b209b3ad226e4761585b74b04d8da743c/mmproj-BF16.gguf"
# Ornith-9B (mradermacher i1-GGUF)
ORNITH_HF_CACHE="$(find "$HOME/.cache/huggingface/hub/models--mradermacher--Ornith-1.5-9B-uncensored-i1-GGUF" -name "Ornith-1.5-9B-uncensored.i1-Q4_K_M.gguf" 2>/dev/null | head -n 1 || true)"
ORNITH_MODEL=""
if [[ -n "$ORNITH_HF_CACHE" && -f "$ORNITH_HF_CACHE" ]]; then
  ORNITH_MODEL="$ORNITH_HF_CACHE"
elif [[ -n "${ORNITH_MODEL_FILE:-}" && -f "$ORNITH_MODEL_FILE" ]]; then
  ORNITH_MODEL="$ORNITH_MODEL_FILE"
else
  # fallback: buscar cualquier Ornith gguf local
  ORNITH_MODEL="$(find "$HOME/models" -name "Ornith-1.5-9B*.gguf" -path "*Q4_K_M*" 2>/dev/null | head -n 1 || true)"
fi
if [[ -z "$ORNITH_MODEL" || ! -f "$ORNITH_MODEL" ]]; then
  echo "ADVERTENCIA: Ornith-9B no encontrado, los sabios pares usarán Qwen3.5-9B como fallback" >&2
  ORNITH_MODEL="$QWEN_MODEL"
  ORNITH_MMPROJ="$QWEN_MMPROJ"
else
  # mmproj Ornith
  ORNITH_MMPROJ_LOCAL="$HOME/models/Ornith-1.5-9B-uncensored-GGUF/Ornith-1.5-9B-uncensored.mmproj-f16.gguf"
  ORNITH_MMPROJ="$(find "$HOME/.cache/huggingface/hub/models--mradermacher--Ornith-1.5-9B-uncensored-GGUF" -name "Ornith-1.5-9B-uncensored.mmproj-f16.gguf" 2>/dev/null | head -n 1 || true)"
  if [[ -z "$ORNITH_MMPROJ" && -f "$ORNITH_MMPROJ_LOCAL" ]]; then ORNITH_MMPROJ="$ORNITH_MMPROJ_LOCAL"; fi
  if [[ -z "$ORNITH_MMPROJ" || ! -f "$ORNITH_MMPROJ" ]]; then
    ALT="$(find "$HOME/models" -name "Ornith-1.5-9B-uncensored.mmproj-*.gguf" 2>/dev/null | head -n 1 || true)"
    [[ -n "$ALT" ]] && ORNITH_MMPROJ="$ALT" || ORNITH_MMPROJ=""
  fi
fi

echo "  Qwen   : $QWEN_MODEL"
echo "  Ornith : $ORNITH_MODEL"
[[ -n "$ORNITH_MMPROJ" ]] && echo "  Qwen mmproj   : $QWEN_MMPROJ" || echo "  Qwen mmproj   : (no)"
[[ -n "$ORNITH_MMPROJ" ]] && echo "  Ornith mmproj : $ORNITH_MMPROJ" || echo "  Ornith mmproj : (no)"

# ── función puerto ──────────────────────────────────────────────────────
port_for_idx() {
  local idx=$1
  if (( idx < 4 )); then
    echo $((8201 + idx))
  else
    echo $((8207 + idx - 4))
  fi
}

# Único setup: 8181 apagado al usar sabios2, ctx sabios 131072 como pides
SABIOS_CTX="${SABIOS_CTX:-131072}"
echo "  ctx sabios: $SABIOS_CTX (Ornith 262144 en 8205)"

# ── lanzar SEGUENCIAL (evita pico de VRAM de 5 cargas paralelas que causaba segfault) ─
for (( idx=0; idx<NUM_SABIOS; idx++ )); do
  PORT="$(service_field "$idx" port)"
  [[ -n "$PORT" ]] || PORT=$(port_for_idx $idx)
  LOG="/tmp/sabio_${PORT}.log"
  if curl -s -m 1 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    echo "[${PORT}] sabio$idx ya está vivo, lo salto"
    continue
  fi
  MODEL_CONFIG="$(service_field "$idx" model)"
  MODEL_PATH_CONFIG="$(service_field "$idx" model_path)"
  SABIO_CTX_CONFIG="$(service_field "$idx" context)"
  [[ -n "$SABIO_CTX_CONFIG" ]] || SABIO_CTX_CONFIG="$SABIOS_CTX"
  if [[ -n "$MODEL_PATH_CONFIG" ]]; then
    MODELO="$MODEL_PATH_CONFIG"
    MMPROJ="$(service_field "$idx" mmproj)"
    LABEL="${MODEL_CONFIG:-modelo configurado}"
  elif [[ "$MODEL_CONFIG" == qwen* ]]; then
    MODELO="$QWEN_MODEL"
    MMPROJ="$QWEN_MMPROJ"
    LABEL="Qwen3.5-9B"
  elif (( idx % 2 == 0 )); then
    MODELO="$ORNITH_MODEL"
    MMPROJ="$ORNITH_MMPROJ"
    LABEL="Ornith-9B"
  else
    MODELO="$QWEN_MODEL"
    MMPROJ="$QWEN_MMPROJ"
    LABEL="Qwen3.5-9B"
  fi
  if [[ ! -f "$MODELO" ]]; then
    echo "Falta modelo para sabio$idx ($LABEL): $MODELO" >&2; exit 1
  fi
  MMPROJ_ARGS=()
  if [[ -n "$MMPROJ" && -f "$MMPROJ" ]]; then
    MMPROJ_ARGS=(--mmproj "$MMPROJ")
  fi
  echo "[${PORT}] arrancando sabio$idx ($LABEL) ctx $SABIO_CTX_CONFIG ..."
  MMPROJ_STR=""
  [[ -n "${MMPROJ_ARGS[*]:-}" ]] && MMPROJ_STR="--mmproj \"$MMPROJ\""
  CMD_STR="$LLAMA_BIN -m \"$MODELO\" $MMPROJ_STR -c $SABIO_CTX_CONFIG --fit off -ngl 999 -b 2048 --ubatch-size 1024 --jinja --parallel 1 --metrics --flash-attn on --temp 1.0 --top-p 0.95 --min-p 0.0 --top-k 64 --repeat-penalty 1.0 --presence-penalty 0.0 --no-warmup --cache-type-k q8_0 --cache-type-v q8_0 --cache-prompt --no-context-shift --no-mmap --host 127.0.0.1 --port $PORT"
  sg render -c "$CMD_STR" >"$LOG" 2>&1 &
  PID=$!
  echo "[${PORT}] sabio$idx PID=$PID log=$LOG — esperando health antes del siguiente..."
  for i in $(seq 1 120); do
    curl -s -m 1 "http://127.0.0.1:${PORT}/health" 2>/dev/null | grep -q ok && break
    # si el proceso murió, abortar espera
    if ! kill -0 $PID 2>/dev/null; then
      echo "[${PORT}] sabio$idx proceso murió (segfault/OOM) — mira $LOG"
      cat "$LOG" 2>/dev/null | tail -n 50
      break
    fi
    sleep 1
  done
  if curl -s -m 1 "http://127.0.0.1:${PORT}/health" 2>/dev/null | grep -q ok; then
    echo "[${PORT}] sabio$idx OK"
  else
    echo "[${PORT}] sabio$idx SIN health tras 120s — mira $LOG"
    exit 1
  fi
  sleep 1
done
echo "Todos los sabios lanzados."
# Guardar N efectivo para pipeline/proxy
if command -v python3 >/dev/null 2>&1; then
  PYTHONPATH="$(dirname "$CONFIG_FILE")" python3 -c "from proxy.configurations import update_runtime; update_runtime('$(dirname "$CONFIG_FILE")', {'num_sabios': int('$NUM_SABIOS')})" 2>/dev/null || true
fi
