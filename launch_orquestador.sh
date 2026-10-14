#!/usr/bin/env bash
# Orquestador configurable; por defecto comparte cerebro en 8205.
exec "$(dirname "$0")/launch_role.sh" orquestador
