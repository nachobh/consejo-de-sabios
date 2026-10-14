#!/usr/bin/env bash
# Guía ejecutable de pruebas rápidas para sabios2.
set -euo pipefail
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"
echo '== Sintaxis Bash =='
bash -n ./*.sh proxy/*.sh
echo '== Python =='
python3 -m py_compile consejo.py proxy/*.py tests/test_sabios2.py
echo '== Tests de configuración =='
python3 -m unittest -v tests/test_sabios2.py
echo '== API local (si está levantada) =='
if curl -fsS http://127.0.0.1:9082/configurations >/tmp/sabios2-configurations.json; then
  python3 -m json.tool /tmp/sabios2-configurations.json >/dev/null
  curl -fsS http://127.0.0.1:9082/config >/tmp/sabios2-runtime.json
  python3 -m json.tool /tmp/sabios2-runtime.json >/dev/null
  echo 'API OK'
else
  echo 'API no levantada; se omite prueba HTTP'
fi
echo 'Pruebas completadas.'
