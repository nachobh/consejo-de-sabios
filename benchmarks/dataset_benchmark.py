#!/usr/bin/env python3
"""Prueba pequeña sobre datasets conversacionales de ingeniería.

Soporta la evaluación de Gemma4 Complex y el export compacto de SWE-Hero.
La puntuación es una señal de triage basada en anclas de la respuesta de
referencia, forma de parche y verificaciones propuestas. No es SWE-bench: para
obtener un pass/fail real hay que aplicar el parche en el repositorio correcto
y ejecutar el oráculo del ejemplo dentro de un entorno de agente.
"""
import argparse
import json
import re
import time
from pathlib import Path

from quality_benchmark import request_json, response_text, target_config

ROOT = Path(__file__).resolve().parents[1]
COMPLEX = ROOT.parent / "gemma4-training-data/datasets/complex_v0.1_portable/evaluation.jsonl"
SWE_HERO = ROOT.parent / "datasets/SWE-Hero-openhands-trajectories-compact.jsonl"


def load_rows(path, limit=None, ids=None):
    selected = []
    wanted = set(ids or ())
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            row_id = row.get("id") or row.get("source_id") or row.get("instance_id")
            if wanted and row_id not in wanted:
                continue
            selected.append(row)
            if limit and len(selected) >= limit:
                break
    if not selected:
        raise SystemExit(f"No se encontraron casos en {path}")
    return selected


def messages_and_reference(row):
    messages = row.get("messages") or []
    user = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
    assistants = [m.get("content", "") for m in messages if m.get("role") == "assistant"]
    system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
    return system, user, "\n\n".join(assistants[-2:])


def anchors(reference):
    values = []
    values += re.findall(r"`([^`\n]{3,80})`", reference)
    values += re.findall(r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+", reference)
    values += re.findall(r"\b(?:diff|rollback|backfill|idempot\w*|synchronized|nullable|NOT NULL|pytest|mvnw|dart analyze|cargo test)\b", reference, re.I)
    unique = []
    seen = set()
    for value in values:
        value = value.strip()
        key = value.casefold()
        if key not in seen and len(value) >= 3:
            seen.add(key)
            unique.append(value)
    return unique[:8]


def score_response(response, reference, metadata):
    low = response.casefold()
    terms = anchors(reference)
    found = [term for term in terms if term.casefold() in low]
    anchor_score = min(1.0, len(found) / max(3, min(5, len(terms)))) if terms else 0.0
    needs_patch = "diff --git" in reference or "--- a/" in reference or "```diff" in reference
    patch_score = 1.0 if not needs_patch or ("diff --git" in response or "--- a/" in response or "```diff" in response) else 0.0
    commands = (metadata.get("verification") or {}).get("commands") or []
    command_tokens = []
    for command in commands:
        command_tokens += re.findall(r"(?:\./)?(?:[A-Za-z][\w.-]*)(?:\.sh)?", command)
    command_tokens = [x for x in command_tokens if x not in {"cd", "git", "rg", "python"}]
    verification_score = 1.0 if not command_tokens else min(1.0, sum(x.casefold() in low for x in command_tokens) / min(2, len(command_tokens)))
    score = 0.5 * anchor_score + 0.25 * patch_score + 0.25 * verification_score
    return round(score * 100, 2), {"anchors_found": found, "anchors_total": len(terms), "patch_shape": patch_score, "verification_mentions": verification_score}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=COMPLEX, help="evaluation.jsonl o SWE-Hero compact.jsonl")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--ids", help="IDs separados por coma; si se usan, tienen prioridad sobre --limit")
    parser.add_argument("--targets", default="solo,sabios,full")
    parser.add_argument("--configuration", default="default")
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--orq-rounds", type=int, default=1)
    parser.add_argument("--num-sabios", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = load_rows(args.dataset, args.limit, args.ids.split(",") if args.ids else None)
    results = []
    for target in [x.strip() for x in args.targets.split(",") if x.strip()]:
        url, prompt, mode = target_config(target, args.configuration, args.dataset)
        for index, row in enumerate(rows, 1):
            dataset_system, user, reference = messages_and_reference(row)
            system = dataset_system or prompt
            messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
            payload = {"model": mode if mode != "direct" else "local", "messages": messages,
                       "temperature": args.temperature, "max_tokens": args.max_tokens, "stream": False}
            if mode != "direct":
                payload.update({"rounds": args.rounds, "orq_rounds": args.orq_rounds,
                                "num_sabios": args.num_sabios, "configuration": args.configuration})
            data, elapsed, error = request_json(url, payload, args.timeout)
            text = response_text(data) if data else ""
            metadata = row.get("metadata", {})
            score, details = score_response(text, reference, metadata)
            usage = (data or {}).get("usage", {})
            output_tokens = int(usage.get("completion_tokens", usage.get("tokens_gen", 0)) or 0)
            item_id = row.get("id") or row.get("source_id") or row.get("instance_id")
            result = {"target": target, "id": item_id, "repo": row.get("repo"), "elapsed_s": round(elapsed, 3),
                      "completion_tokens": output_tokens, "tok_s": round(output_tokens / elapsed, 2) if elapsed and output_tokens else 0,
                      "score": score, "details": details, "error": error, "response": text}
            results.append(result)
            print(f"[{target:8} {index:02}/{len(rows):02}] {item_id} score={score:5.1f} "
                  f"time={elapsed:7.2f}s out={output_tokens:5} tok/s={result['tok_s']:6.1f}" +
                  (f" ERROR={error}" if error else ""), flush=True)
    output = args.output or ROOT / "results" / f"dataset_benchmark_{time.strftime('%Y%m%d_%H%M%S')}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"dataset": str(args.dataset), "note": "triage; no repository oracle executed", "results": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nResultados: {output}")


if __name__ == "__main__":
    main()
