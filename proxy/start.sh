#!/usr/bin/env bash
# Iniciar el proxy del Consejo de Sabios2 y el frontend web.
# Proxy en 8081 igual que v1 (usas uno u otro, nunca ambos). Prompts/web en 9082/9080.
# Uso: ~/sabios2/proxy/start.sh [--port 8081] [--prompts-port 9082] [--web-port 9080]

set -euo pipefail
PROXY_PORT="${CONSEJO_PROXY_PORT:-8081}"
PROMPTS_PORT="${CONSEJO_PROMPTS_PORT:-9082}"
WEB_PORT="${CONSEJO_WEB_PORT:-9080}"

# permitir override por args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PROXY_PORT="$2"; shift 2;;
    --prompts-port) PROMPTS_PORT="$2"; shift 2;;
    --web-port) WEB_PORT="$2"; shift 2;;
    *) shift;;
  esac
done
export CONSEJO_PROXY_PORT="$PROXY_PORT"
export CONSEJO_PROMPTS_PORT="$PROMPTS_PORT"
export CONSEJO_WEB_PORT="$WEB_PORT"

# Matar proxy previo en esos puertos
for P in "$PROXY_PORT" "$PROMPTS_PORT"; do
  PID=$(lsof -ti :$P 2>/dev/null || true)
  if [[ -n "$PID" ]]; then
    echo "Matando proceso en :$P (PID $PID)..."
    kill $PID 2>/dev/null || true
    sleep 1
  fi
done
EXISTING_PROXY=$(pgrep -f "sabios2.*consejo_proxy.py" 2>/dev/null || true)
if [[ -n "$EXISTING_PROXY" ]]; then
  echo "Matando proxy2 anterior (PID $EXISTING_PROXY)..."; kill $EXISTING_PROXY 2>/dev/null || true
  sleep 1
fi

echo "Iniciando proxy Sabios2 (puertos $PROXY_PORT + $PROMPTS_PORT)..."
nohup python3 /home/ignacia/sabios2/proxy/consejo_proxy.py > /tmp/consejo2_proxy.log 2>&1 &
PROXY_PID=$!
echo "Proxy2 iniciado (PID $PROXY_PID) log=/tmp/consejo2_proxy.log"

# Frontend web (puerto WEB_PORT)
# Buscar web dir: /home/ignacia/sabios2/web o /var/www/html/consejo2
WEB_DIR="/home/ignacia/sabios2/web"
if [[ ! -d "$WEB_DIR" ]]; then WEB_DIR="/var/www/html/consejo2"; fi
if [[ ! -d "$WEB_DIR" ]]; then
  echo "ADVERTENCIA: no existe $WEB_DIR, creando desde template..."
  mkdir -p "$WEB_DIR"
fi
# matar web previo
PIDW=$(lsof -ti :$WEB_PORT 2>/dev/null || true)
if [[ -n "$PIDW" ]]; then echo "Matando web en :$WEB_PORT (PID $PIDW)"; kill $PIDW 2>/dev/null || true; sleep 1; fi

echo "Iniciando frontend web2 (puerto $WEB_PORT) desde $WEB_DIR..."
cd "$WEB_DIR" && nohup python3 -m http.server "$WEB_PORT" > /tmp/consejo2_web.log 2>&1 &
WEB_PID=$!
echo "Frontend web2 iniciado (PID $WEB_PID) log=/tmp/consejo2_web.log"

echo ""
echo "════════════════════════════════════════════════════════════════════════════"
echo "  Sabios2 Proxy:   http://127.0.0.1:$PROXY_PORT (API OpenAI compatible) — mismo 8081 que v1"
echo "  Sabios2 Web:     http://127.0.0.1:$WEB_PORT (frontend)"
echo "  Sabios2 Prompts: http://127.0.0.1:$PROMPTS_PORT/api/prompts"
echo "  Logs:    /tmp/consejo2_proxy.log, /tmp/consejo2_web.log"
echo "  Parar:   ~/sabios2/proxy/stop.sh"
echo "  Nota: v1 y v2 comparten 8081 — para solo uno a la vez"
echo "════════════════════════════════════════════════════════════════════════════"
