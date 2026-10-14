#!/usr/bin/env bash
# Consejo de Sabios2: levanta TODO (N sabios + 1 cerebro).
# Uso:
#   ~/sabios2/launch_all.sh [N]              # levanta con N sabios (default 4)
#   ~/sabios2/launch_all.sh --num-sabios N   # idem
#   NUM_SABIOS=N ~/sabios2/launch_all.sh
#   ~/sabios2/launch_all.sh stop
#   ~/sabios2/launch_all.sh status [--num-sabios N]
set -euo pipefail
cd "$(dirname "$0")"

# ── parsear N y comando ───────────────────────────────────────────────
ENV_NUM_SABIOS="${NUM_SABIOS:-}"
NUM_SABIOS=""
CMD="start"
RUNTIME_OVERRIDES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --num-sabios)
      [[ $# -ge 2 ]] || { echo "Falta valor para --num-sabios" >&2; exit 1; }
      NUM_SABIOS="$2"; shift 2; continue ;;
    --num-sabios=*) NUM_SABIOS="${1#--num-sabios=}" ;;
    stop|status|start|configure) CMD="$1" ;;
    --model|--port|--context)
      [[ $# -ge 2 ]] || { echo "Falta valor para $1" >&2; exit 1; }
      case "$1" in --model) RUNTIME_KEY="model";; --port) RUNTIME_KEY="port";; --context) RUNTIME_KEY="context";; esac
      RUNTIME_OVERRIDES+=("$RUNTIME_KEY=$2"); shift 2; continue ;;
    [0-9]|[0-9][0-9]) NUM_SABIOS="$1" ;;
    *) echo "Argumento no reconocido: $1" >&2; exit 1 ;;
  esac
  shift
done
if [[ -z "$NUM_SABIOS" && -n "$ENV_NUM_SABIOS" ]]; then NUM_SABIOS="$ENV_NUM_SABIOS"
elif [[ -z "$NUM_SABIOS" ]]; then
  if [[ -f "./config.json" ]]; then
    NUM_SABIOS=$(python3 -c "import json; print(json.load(open('config.json')).get('num_sabios',4))" 2>/dev/null || echo 4)
  else
    NUM_SABIOS=4
  fi
fi
# si primera arg es número sin flag, ya está; si CMD es número, corregir
if [[ "$CMD" =~ ^[0-9]+$ ]]; then NUM_SABIOS="$CMD"; CMD="start"; fi
if ! [[ "$NUM_SABIOS" =~ ^[0-9]+$ ]] || (( NUM_SABIOS < 1 || NUM_SABIOS > 12 )); then
  echo "NUM_SABIOS inválido: $NUM_SABIOS (1-12)" >&2; exit 1
fi

for override in "${RUNTIME_OVERRIDES[@]}"; do
  key="${override%%=*}"; value="${override#*=}"; role="${value%%:*}"; value="${value#*:}"
  [[ "$role" != "$value" ]] || { echo "Usa --$key rol:valor" >&2; exit 1; }
  PYTHONPATH="$PWD" python3 -c "from proxy.configurations import update_runtime; update_runtime('.', {'services': {'$role': {'$key': '$value'}}})"
done

port_for_idx() {
  local idx=$1
  if (( idx < 4 )); then echo $((8201+idx)); else echo $((8207+idx-4)); fi
}

all_ports() {
  python3 - "$NUM_SABIOS" <<'PY'
import json, os, sys
base=os.path.dirname(os.path.abspath(__file__))
cfg=json.load(open(os.path.join(base,"config.json"), encoding="utf-8"))
s=cfg.get("runtime",{}).get("services",{})
n=int(sys.argv[1]); ports=[]
for i in range(n):
    default=8201+i if i<4 else 8207+i-4
    ports.append(int(s.get(f"sabi{i}",{}).get("port",default)))
for role in ("arbitro","cerebro","orquestador"):
    ports.append(int(s.get(role,{}).get("port",8205)))
print("\n".join(dict.fromkeys(map(str,ports))))
PY
}

stop_all() {
  echo "Matando servers sabios2 (sabios:$NUM_SABIOS + cerebro 8205 único Ornith)..."
  for PORT in $(all_ports); do
    PIDS=$(pgrep -f "llama-server.*--port ${PORT} " 2>/dev/null || pgrep -f "llama-server.*--port ${PORT}$" 2>/dev/null || true)
    if [[ -n "$PIDS" ]]; then
      kill $PIDS 2>/dev/null && echo "  ${PORT}: matado (PID $PIDS)"
      sleep 1
      kill -9 $PIDS 2>/dev/null || true
    else
      echo "  ${PORT}: no hay proceso"
    fi
  done
  echo "Hecho."
}

status_all() {
  for PORT in $(all_ports); do
    if curl -s -m 1 "http://127.0.0.1:${PORT}/health" 2>/dev/null | grep -q ok; then
      echo "  ${PORT}: OK"
    else
      echo "  ${PORT}: caído"
    fi
  done
}

case "$CMD" in
  configure) exec ./configure_runtime.py ;;
  stop) stop_all ;;
  status) status_all ;;
  *)
    echo "=== sabios2: $NUM_SABIOS sabios (131072) + cerebro 8205 único Ornith 262144 (8181 apagado) ==="
    PYTHONPATH="$PWD" python3 -c "from proxy.configurations import update_runtime; update_runtime('.', {'num_sabios': int('$NUM_SABIOS')})" 2>/dev/null || true
    # secuencial para evitar pico de VRAM (5 cargas paralelas causaba segfault con 25G libres)
    ./launch_cerebro.sh
    ./launch_arbitro.sh
    ./launch_orquestador.sh
    ./launch_sabios.sh "$NUM_SABIOS"
    echo ""
    status_all
    ;;
esac
