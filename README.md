# Consejo de Sabios2 — Multi-Model Debate & Orchestration Pipeline

A local multi-agent pipeline that runs several small/mid LLMs ("sages") in
parallel behind a single unified "brain" model that plans, arbitrates and
executes. Everything runs on local `llama-server` instances — no cloud APIs.

**Scope and honesty note:** this is a prompting/orchestration pipeline on top
of existing inference servers (llama.cpp), not a kernel- or runtime-level
optimization project. Its value for this kind of work is in *task design and
rigorous evaluation of multi-agent behavior* (debate protocols, role prompts,
reproducible scoring, latency/throughput accounting), not in low-level
inference engineering.

## What it is

- N configurable "sages" (1–12), alternating between two local models
  (Ornith-9B / Qwen3.5-9B), each served by its own `llama-server` on ports
  8201-8204 and 8207+.
- A single unified "cerebro" (Ornith-35B, one process on port 8205) that plays
  two roles via different system prompts: **orchestrator** (writes/revises the
  plan) and **referee** (issues a verdict on the sages' answers). Running one
  model instead of two separate large models saves VRAM (see below).
- Rounds: sages debate in parallel (1-5 rounds) → cerebro drafts a plan →
  referee/cerebro rounds (1-3) → cerebro executes the final answer.
- An OpenAI-compatible proxy (port 8081) exposes `/v1/chat/completions` with
  custom fields (`num_sabios`, `rounds`, `orq_rounds`, `system_prompts`,
  `configuration`) and can also act as a harness-compatible endpoint: it
  detects `tools`/`tool_calls` requests and routes the turn to the
  orchestrator, converting several text-based tool-call conventions (XML,
  `[tool:...]`, Hermes `<tool_call>`) back into OpenAI `tool_calls`.
- A CLI (`consejo.py`) and a small web frontend drive the same pipeline code
  (`proxy/pipeline.py` is the single source of truth for both).

## Architecture

```
                    Goal
                      |
      +---------------+----------------+
      |   Sage round(s), 1-5, parallel  |
      |   N configurable (1-12)         |
      |                                  |
      |  sabi0 :8201  Ornith-9B  (role A)|
      |  sabi1 :8202  Qwen3.5-9B (role B)|
      |  sabi2 :8203  Ornith-9B  (role C)|
      |  sabi3 :8204  Qwen3.5-9B (role D)|
      |  sabi4.. :8207+  (extra, if N>4) |
      +---------------+----------------+
                      |
                      v
       Cerebro :8205  Ornith-35B  --(orquestador prompt)-->  PLAN
                      |
                      v
       Referee/Cerebro rounds (1-3), same port 8205,
       prompt switches between "arbitro" (verdict) and
       "orquestador" (revised plan)
                      |
                      v
       Cerebro :8205  -->  FINAL EXECUTION / ANSWER
```

Ports: sages 8201-8204 and 8207+; cerebro (orchestrator+referee) 8205;
proxy 8081; prompts API 9082; web UI 9080.

## How to run

```bash
cd ~/sabios2
./setup.sh                      # downloads the 3 GGUF models (HF)
./launch_all.sh 6                # start 6 sages + cerebro
./proxy/start.sh                 # proxy :8081, prompts :9082, web :9080

# CLI
python3 consejo.py "YOUR GOAL" --mode full --num-sabios 6 --rounds 2 --orq-rounds 2

# Benchmark (quality + speed)
python3 benchmarks/quality_benchmark.py
python3 benchmarks/quality_benchmark.py --targets solo,sabios,full --rounds 1 --orq-rounds 1

./launch_all.sh stop && ./proxy/stop.sh
```

Model source/paths are configurable via env vars (`ORNITH35_REPO`,
`ORNITH9_REPO`, `QWEN9_REPO`, `SABIOS2_MODEL_DIR`); always check model
licenses before redistributing weights.

## What was actually measured

`benchmarks/quality_benchmark.py` runs a fixed local regression suite
(`benchmarks/quality_cases.json`: reasoning, coding, security, structured
extraction, trade-off analysis, probabilistic reasoning, critique, incident
response) against each sage directly and against the `solo` / `sabios` /
`full` proxy pipelines, scoring each response against weighted, auditable
checks (substring/regex/contains-any), and recording wall-clock latency and
tokens/s per call. This is **not** a published benchmark like SWE-bench — it's
a cheap, reproducible local regression bench meant to catch prompt
regressions without hiding their latency cost.

Real run recorded in `results/benchmark_20260825_145557.json` (4 sages, 1
round, 12 test cases each, full run wall-clock ≈ 9,391 s):

| Target | Avg. quality score | Avg. output tok/s | Avg. latency (s) |
|---|---|---|---|
| sabi0 (Ornith-9B) | 85.0 | 33.2 | 26.5 |
| sabi1 (Qwen3.5-9B) | 79.6 | 28.4 | 49.9 |
| sabi2 (Ornith-9B) | 91.2 | 33.2 | 29.2 |
| sabi3 (Qwen3.5-9B) | 76.7 | 28.2 | 51.0 |
| `solo` (cerebro baseline, no sages) | 95.8 | 6.5 | 196.7 |
| `sabios` (sages + plan, no referee) | 86.7 | — | 170.9 |
| `full` (sages + plan + referee) | 91.7 | — | 258.3 |

Observations from this single run (small sample, 12 cases per target — not
statistically rigorous, treat as directional):

- The 9B sages are 4-5x faster per token than the 35B cerebro baseline
  (`solo`), as expected from model size, at the cost of lower per-sage scores.
- Multi-agent `full` scored higher than any individual 9B sage and close to
  the `solo` 35B baseline, but took ~1.3x longer wall-clock than `solo` in
  this run — the debate/arbitration overhead did not clearly pay for itself
  on this small sample. This is the kind of quality-vs-latency trade-off the
  benchmark is designed to expose, not hide.
- `sabi1`/`sabi3` (Qwen3.5-9B) show noticeably higher per-call latency and
  lower output tok/s than the Ornith-9B sages at the same size class on this
  hardware/config — worth investigating (context length, sampler settings,
  or genuinely slower decode) before drawing model-quality conclusions.

A second harness (`benchmarks/dataset_benchmark.py`) can replay small samples
from external datasets (e.g. SWE-bench-style trajectories) for the same
targets; its comparison against reference answers is explicitly a triage
signal, not a pass/fail SWE-bench result (no patch application or oracle
execution).

## Relevance

This project demonstrates: designing reproducible, weighted evaluation
harnesses for LLM behavior; running and comparing heterogeneous local models
side-by-side with controlled latency/throughput accounting; and building an
OpenAI-compatible proxy layer that adapts several tool-calling conventions for
agent harness compatibility. It does **not** demonstrate GPU kernel work,
quantization engineering, or serving-stack internals — the inference serving
itself is delegated entirely to stock `llama-server`.

## Notes / limitations

- Single informal benchmark run, 12 cases per target — not a statistically
  powered comparison. Treat the numbers above as illustrative, not final.
  Re-run multiple times at fixed temperature/`max_tokens` for a fair
  comparison (the README documents this caveat explicitly).
- VRAM estimate (not independently re-measured here): N=4 ≈ 40 GB, N=8 ≈ 60 GB
  (9 GB-class sages + 20 GB-class 35B cerebro, long context).
