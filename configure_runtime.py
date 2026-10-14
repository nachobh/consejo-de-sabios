#!/usr/bin/env python3
"""Asistente interactivo de runtime para sabios2."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from proxy.configurations import load_runtime, save_runtime

BASE = os.path.dirname(os.path.abspath(__file__))
r = load_runtime(BASE)
print("0) Valores por defecto (conservar runtime actual)")
print("1) Configuración paso a paso")
mode = input("Elige [0/1]: ").strip() or "0"
if mode == "0":
    print("No se modifica el runtime.")
    raise SystemExit(0)
if mode != "1":
    raise SystemExit("Opción inválida")

roles = ["arbitro", "cerebro", "orquestador"]
for index, role in enumerate(roles, 1):
    print(f"\nPaso {index}: {role}")
    service = r["services"].setdefault(role, {})
    model = input(f"  modelo [{service.get('model','')}]: ").strip()
    port = input(f"  puerto [{service.get('port','')}]: ").strip()
    context = input(f"  contexto [{service.get('context','')}]: ").strip()
    if model: service["model"] = model
    if port: service["port"] = int(port)
    if context: service["context"] = int(context)

n = input(f"\nPaso 4 — número de sabios [{r['num_sabios']}]: ").strip()
if n:
    r["num_sabios"] = max(1, min(12, int(n)))
for index in range(r["num_sabios"]):
    role = f"sabi{index}"
    print(f"\nPaso {index + 5}: {role}")
    service = r["services"].setdefault(role, {})
    model = input(f"  modelo [{service.get('model','')}]: ").strip()
    port = input(f"  puerto [{service.get('port','')}]: ").strip()
    context = input(f"  contexto [{service.get('context','')}]: ").strip()
    if model: service["model"] = model
    if port: service["port"] = int(port)
    if context: service["context"] = int(context)
save_runtime(BASE, r)
print("\nRuntime guardado en config.json")
