#!/usr/bin/env python3
"""
Consejo de Sabios2 — CLI dinámica multi-modelo.

Puertos (ver launch_*.sh):
  8201-8204      Sabios base (mezcla Ornith-9B / Qwen3.5-9B alternando)
  8207+          Sabios extra (si N>4)
  8205           Cerebro unificado Ornith 35B (orquestador+árbitro, mismo modelo, distinto system prompt)

Uso:
  python3 consejo.py "OBJETIVO..." [--task id] [--mode full|solo|sabios]
                                   [--rounds N] [--orq-rounds N] [--num-sabios N]
    full  : sabios → plan → arbitraje → ejecución
    sabios: sabios + plan (sin árbitro)
    solo  : baseline — cerebro directo
  --num-sabios 1..12 (default: config.json o 4)
Salida: ./results/<task>.json
"""
import argparse
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from proxy.pipeline import (
    PipelineConfig, load_prompts, ejecutar_pipeline,
    extraer_respuesta_final, agregar_usage_total, get_sabios_roles,
)
from proxy.configurations import active_name, prompt_overrides, atomic_write_text

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
CONFIG_PATH = os.path.join(HERE, "config.json")

def get_default_num():
    try:
        if os.path.exists(CONFIG_PATH):
            return int(json.load(open(CONFIG_PATH)).get("num_sabios", 4))
    except: pass
    return 4

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("objetivo")
    ap.add_argument("--task", default=None, help="id del task (default: timestamp)")
    ap.add_argument("--mode", choices=["full", "sabios", "solo"], default="full")
    ap.add_argument("--rounds", type=int, default=1, help="Rondas de discusión entre sabios (1-5, default: 1)")
    ap.add_argument("--orq-rounds", type=int, default=1, help="Rondas de revisión cerebro (1-3, default: 1)")
    ap.add_argument("--num-sabios", type=int, default=None, help="Número de sabios (1-12, default: config.json o 4)")
    ap.add_argument("--configuration", default=None, help="Configuración de prompts (default: activa)")
    ap.add_argument("--max-tokens-sabios", type=int, default=4096)
    ap.add_argument("--max-tokens-plan", type=int, default=2048)
    ap.add_argument("--max-tokens-arbitro", type=int, default=2048)
    ap.add_argument("--max-tokens-ejecucion", type=int, default=4096)
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    task = args.task or (time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8])
    os.makedirs(RESULTS, exist_ok=True)

    rounds = max(1, min(5, args.rounds))
    orq_rounds = max(1, min(3, args.orq_rounds))
    num_sabios = args.num_sabios if args.num_sabios is not None else get_default_num()
    num_sabios = max(1, min(12, int(num_sabios)))

    config = PipelineConfig(
        mode=args.mode,
        rounds=rounds,
        orq_rounds=orq_rounds,
        num_sabios=num_sabios,
        max_tokens_sabios=args.max_tokens_sabios,
        max_tokens_plan=args.max_tokens_plan,
        max_tokens_arbitro=args.max_tokens_arbitro,
        max_tokens_ejecucion=args.max_tokens_ejecucion,
        timeout=args.timeout,
    )

    prompts_dir = os.path.join(HERE, "prompts")
    load_prompts(prompts_dir)
    configuration = args.configuration or active_name(HERE, prompts_dir)
    config.prompt_overrides = prompt_overrides(HERE, prompts_dir, configuration)

    print(f">> Modo: {args.mode} | Rondas sabios: {rounds} | Rondas cerebro: {orq_rounds} | Sabios: {num_sabios} ({', '.join(get_sabios_roles(num_sabios))})")
    print(f">> Cerebro: Ornith 35B en 8205 (orquestador+árbitro unificados)")
    print(f">> Configuración de prompts: {configuration}")
    print(f">> Objetivo: {args.objetivo[:100]}...")
    print()

    resultados = ejecutar_pipeline(args.objetivo, prompts_dir=prompts_dir, config=config)

    out = {"task": task,"objetivo": args.objetivo,"mode": args.mode,"rounds": rounds,"orq_rounds": orq_rounds,"num_sabios": num_sabios,"configuration": configuration,"inicio": time.strftime("%Y-%m-%d %H:%M:%S")}
    out.update(resultados)
    path = os.path.join(RESULTS, f"{task}.json")
    atomic_write_text(path, json.dumps(out, ensure_ascii=False, indent=2) + "\n")

    texto_final = extraer_respuesta_final(resultados)
    usage = agregar_usage_total(resultados)
    print(f"\n=== HECHO: {resultados.get('total_segundos', '?')}s en total ===")
    print(f"=== Tokens: prompt={usage['prompt_tokens']} completion={usage['completion_tokens']} thinking={usage['thinking_tokens']} ===")
    print(f"=== Guardado en {path} ===")
    print(f"\n--- RESPUESTA FINAL ---\n{texto_final}")

if __name__ == "__main__":
    main()
