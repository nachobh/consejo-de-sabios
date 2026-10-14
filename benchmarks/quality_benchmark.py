#!/usr/bin/env python3
"""Benchmark local de calidad y rendimiento para sabios2.

Mide modelos aislados (sabi0, sabi1, ...) y modos del proxy (solo, sabios,
full). La puntuación es deliberadamente auditable: cada caso tiene criterios
de texto/regex editables en quality_cases.json. No pretende ser SWE-bench
oficial; es un banco de regresión local para comparar prompts y configuraciones.
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = Path(__file__).with_name("quality_cases.json")
sys.path.insert(0, str(ROOT / "proxy"))
from configurations import prompt_overrides  # noqa: E402
from pipeline import get_role_port  # noqa: E402


def request_json(url, payload, timeout):
    body = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode())
        return data, time.perf_counter() - started, None
    except Exception as exc:
        return None, time.perf_counter() - started, f"{type(exc).__name__}: {exc}"


def response_text(data):
    try:
        message = data["choices"][0].get("message", {})
        content = message.get("content") or message.get("reasoning_content") or ""
        if isinstance(content, list):
            return "\n".join(str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in content)
        return str(content)
    except (KeyError, IndexError, TypeError):
        return ""


def score_case(text, criteria):
    lowered = text.casefold()
    earned = 0.0
    maximum = sum(float(item.get("weight", 1)) for item in criteria) or 1.0
    details = []
    for item in criteria:
        ok = True
        if item.get("contains_any"):
            ok = any(str(value).casefold() in lowered for value in item["contains_any"])
        if item.get("contains_all"):
            ok = ok and all(str(value).casefold() in lowered for value in item["contains_all"])
        if item.get("not_contains"):
            ok = ok and not any(str(value).casefold() in lowered for value in item["not_contains"])
        if item.get("regex"):
            try:
                ok = ok and bool(re.search(item["regex"], text, re.I | re.S))
            except re.error:
                ok = False
        weight = float(item.get("weight", 1))
        earned += weight if ok else 0
        details.append({"name": item.get("name", "criterio"), "passed": ok, "weight": weight})
    return round(earned / maximum, 6), details


def target_config(target, configuration, cases_dir):
    """Devuelve endpoint, prompt y si la petición es al proxy."""
    base = ROOT
    prompts_dir = base / "prompts"
    overrides = prompt_overrides(str(base), str(prompts_dir), configuration)
    if target in {"solo", "sabios", "full"}:
        return "http://127.0.0.1:8081/v1/chat/completions", None, target
    role = target
    if target.startswith("sabi"):
        role = target
    elif target in {"cerebro", "orquestador", "arbitro"}:
        role = target
    else:
        raise ValueError(f"Target desconocido: {target}")
    port = get_role_port(role)
    prompt = overrides.get(role)
    if not prompt:
        prompt_path = prompts_dir / f"{role}.txt"
        prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""
    return f"http://127.0.0.1:{port}/v1/chat/completions", prompt, "direct"


def run_target(target, cases, args):
    url, system_prompt, mode = target_config(target, args.configuration, DEFAULT_CASES)
    rows = []
    for index, case in enumerate(cases, 1):
        messages = [{"role": "user", "content": case["prompt"]}]
        if system_prompt:
            messages.insert(0, {"role": "system", "content": system_prompt})
        payload = {"model": mode if mode != "direct" else "local", "messages": messages,
                   "temperature": args.temperature, "max_tokens": args.max_tokens, "stream": False}
        if mode != "direct":
            payload.update({"rounds": args.rounds, "orq_rounds": args.orq_rounds,
                            "num_sabios": args.num_sabios, "configuration": args.configuration})
        data, elapsed, error = request_json(url, payload, args.timeout)
        text = response_text(data) if data else ""
        score, checks = score_case(text, case.get("criteria", []))
        usage = (data or {}).get("usage", {})
        prompt_tokens = int(usage.get("prompt_tokens", usage.get("tokens_prompt", 0)) or 0)
        output_tokens = int(usage.get("completion_tokens", usage.get("tokens_gen", 0)) or 0)
        total_tokens = int(usage.get("total_tokens", prompt_tokens + output_tokens) or 0)
        rows.append({"target": target, "case": case["id"], "category": case.get("category"),
                     "elapsed_s": round(elapsed, 3), "prompt_tokens": prompt_tokens,
                     "completion_tokens": output_tokens, "total_tokens": total_tokens,
                     "output_tok_s": round(output_tokens / elapsed, 2) if elapsed and output_tokens else 0,
                     "total_tok_s": round(total_tokens / elapsed, 2) if elapsed and total_tokens else 0,
                     "score": round(score * 100, 2), "checks": checks, "error": error,
                     "response": text})
        print(f"[{target:12} {index:02}/{len(cases):02}] {case['id']:16} "
              f"score={score*100:5.1f}  time={elapsed:7.2f}s  out={output_tokens:5} tok "
              f"speed={output_tokens/elapsed if elapsed and output_tokens else 0:6.1f} tok/s" +
              (f"  ERROR={error}" if error else ""), flush=True)
    return rows


def print_summary(rows):
    print("\nRESUMEN")
    print(f"{'target':12} {'casos':>5} {'score':>8} {'tiempo':>10} {'tokens':>10} {'out tok/s':>10}")
    targets = sorted({row["target"] for row in rows})
    for target in targets:
        group = [row for row in rows if row["target"] == target]
        valid = [row for row in group if not row["error"]]
        if not valid:
            print(f"{target:12} {len(group):5} {'ERROR':>8}")
            continue
        print(f"{target:12} {len(valid):5} {sum(x['score'] for x in valid)/len(valid):7.2f}% "
              f"{sum(x['elapsed_s'] for x in valid):9.2f}s "
              f"{sum(x['total_tokens'] for x in valid):9} "
              f"{sum(x['completion_tokens'] for x in valid)/sum(x['elapsed_s'] for x in valid):9.2f}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", default="sabi0,sabi1,sabi2,sabi3,solo,sabios,full",
                        help="targets separados por coma: sabiN, cerebro, solo, sabios, full")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--configuration", default="default")
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--orq-rounds", type=int, default=1)
    parser.add_argument("--num-sabios", type=int, default=4)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    targets = [item.strip() for item in args.targets.split(",") if item.strip()]
    all_rows = []
    started = time.perf_counter()
    for target in targets:
        all_rows.extend(run_target(target, cases, args))
    print_summary(all_rows)
    output = args.output or ROOT / "results" / f"benchmark_{time.strftime('%Y%m%d_%H%M%S')}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"metadata": {"targets": targets, "cases": str(args.cases),
        "configuration": args.configuration, "rounds": args.rounds, "orq_rounds": args.orq_rounds,
        "num_sabios": args.num_sabios, "wall_clock_s": round(time.perf_counter()-started, 3)},
        "results": all_rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nResultados detallados: {output}")


if __name__ == "__main__":
    main()
