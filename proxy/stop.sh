#!/usr/bin/env bash
# Detener el proxy del Consejo de Sabios2 y frontend web2.
echo "Parando proxy Sabios2..."
PROXY_PORT="${CONSEJO_PROXY_PORT:-8081}"
PROMPTS_PORT="${CONSEJO_PROMPTS_PORT:-9082}"
WEB_PORT="${CONSEJO_WEB_PORT:-9080}"
lsof -ti :$PROXY_PORT :$PROMPTS_PORT 2>/dev/null | xargs -r kill 2>/dev/null || true
pgrep -f "sabios2.*consejo_proxy.py" 2>/dev/null | xargs -r kill 2>/dev/null || true
echo "Parando frontend web2 (puerto $WEB_PORT)..."
lsof -ti :$WEB_PORT 2>/dev/null | xargs -r kill 2>/dev/null || true
pgrep -f "http.server $WEB_PORT" 2>/dev/null | xargs -r kill 2>/dev/null || true
# fallback por si se lanzó con python -m http.server sin filtro web dir
pgrep -f "sabios2/web" 2>/dev/null | xargs -r kill 2>/dev/null || true
echo "Listo."
