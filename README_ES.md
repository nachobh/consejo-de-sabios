# Consejo de Sabios2

Pipeline multi-modelo dinámico con cerebro unificado y sabios mezcla Ornith/Qwen.

Duplicado de `~/sabios` con cambios clave pedidos:
- **Cerebro unificado**: Orquestador y Árbitro comparten el mismo modelo `run_ornith_35b_proxied.sh` (Ornith 35B Q8) en **solo 8205**. El system prompt distingue el rol (orquestador vs árbitro). Ahorra VRAM al levantar 1 proceso en vez de 2.
- **Sabios mezcla**: `run_ornith_9b.sh` (pares: sabi0,2,4…) + `run_qwen35_9b.sh` (impares: sabi1,3,5…) alternando.
- **N configurable**: Elige con cuántos sabios arrancar (1-12) vía `launch_all.sh N`, `NUM_SABIOS=N`, `config.json` o desde el frontend.
- **Prompts dinámicos**: Añade sabios y genera nuevos system prompts seleccionables desde el `.html`.

## Arquitectura

```
Objetivo
  ↓
Rondas de sabios (1..5, paralelo, N configurable)
  - sabi0 (8201) Ornith-9B   Técnico
  - sabi1 (8202) Qwen3.5-9B  Estratega
  - sabi2 (8203) Ornith-9B   Creativo
  - sabi3 (8204) Qwen3.5-9B  Abogado
  - sabi4 (8207) Ornith-9B  \
  - sabi5 (8208) Qwen3.5-9B  > extras si N>4
  - ... hasta 12
  ↓
Cerebro (8205) Ornith 35B → PLAN  (prompt orquestador.txt)
  ↓
Rondas Árbitro+Cerebro (1..3)  — mismo puerto 8205, distinto prompt
  Árbitro (8205, prompt arbitro.txt) → VEREDICTO
  Cerebro (8205, prompt orquestador.txt) → PLAN REVISADO
  ↓
Cerebro (8205) → EJECUCIÓN
```

Puertos sabios: 8201-8204 y 8207+ (8205/8206 reservados para no colisionar con cerebro).

## Arranque

```bash
cd ~/sabios2
./launch_all.sh              # arranca el runtime configurado
./launch_all.sh 6            # 6 sabios
NUM_SABIOS=8 ./launch_all.sh # 8 sabios
./launch_all.sh --num-sabios 2
./launch_all.sh status       # ver salud
./launch_all.sh stop         # detener todos los modelos
./launch_all.sh configure    # asistente 0=defaults / 1=paso a paso
./launch_all.sh --model sabi0:ornith-9b --port sabi0:8301 --context sabi0:65536

# A trozos:
./launch_sabios.sh 6         # solo sabios (mezcla)
./launch_cerebro.sh          # solo cerebro
# wrappers compat:
./launch_orquestador.sh      # orquestador configurable
./launch_arbitro.sh           # árbitro configurable
```

El orden recomendado es arrancar primero los modelos y después el proxy:

```bash
cd ~/sabios2
./launch_all.sh start
./proxy/start.sh
```

Para parar ambos servicios:

```bash
./proxy/stop.sh
./launch_all.sh stop
```

Config persistida en `~/sabios2/config.json` (`{"num_sabios":4}`). Las asignaciones
de prompts se guardan en `configurations.json`; la configuración inicial es
**Configuración por defecto** y reproduce los `.txt` actuales.

## Proxy (API OpenAI-compatible) — proxy unificado en 8081

v1 y v2 comparten el proxy en **8081** (usas uno u otro, nunca ambos). Prompts/web de v2 siguen en 9082/9080 para no colisionar:

```bash
~/sabios2/proxy/start.sh         # proxy en :8081 + prompts en :9082 + web en :9080
~/sabios2/proxy/start.sh --port 8081 --prompts-port 9082 --web-port 9080
~/sabios2/proxy/stop.sh
# Para v1: ~/sabios/proxy/start.sh  (también 8081)
```

Puertos:
- **8081** `/v1/chat/completions` — API principal (mismo que v1)
- **8081** `/status` — estado de modelos (cerebro + N sabios)
- **9082** `/api/prompts` — gestión de system prompts v2 (v1 usa 8082)
- **9082** `/config` — GET/POST `{"num_sabios":N}` (genera prompts base si faltan)
- **9080** Frontend web v2 (`~/sabios2/web/index.html`, v1 usa 8080)

Override vía env: `CONSEJO_PROXY_PORT`, `CONSEJO_PROMPTS_PORT`, `CONSEJO_WEB_PORT`.

## CLI (consejo.py)

```bash
python3 ~/sabios2/consejo.py "TU OBJETIVO" --mode solo          # baseline cerebro
python3 ~/sabios2/consejo.py "TU OBJETIVO" --mode sabios        # sabios + plan
python3 ~/sabios2/consejo.py "TU OBJETIVO" --mode full          # completo
python3 ~/sabios2/consejo.py "TU OBJETIVO" --num-sabios 6 --rounds 2 --orq-rounds 2
python3 ~/sabios2/consejo.py "TU OBJETIVO" --configuration default
```

Flags:
- `--mode full|sabios|solo`
- `--rounds N` (1-5)
- `--orq-rounds N` (1-3)
- `--num-sabios N` (1-12, default config.json o 4)
- `--configuration NOMBRE` (configuración de prompts; por defecto, la activa)
- `--max-tokens-*`, `--timeout`, `--task`

Salida: `results/<task>.json`

## API del proxy (request body)

```json
{
  "model": "full",
  "messages": [{"role": "user", "content": "TU OBJETIVO"}],
  "rounds": 2,
  "orq_rounds": 1,
  "num_sabios": 6,
  "system_prompts": {
    "sabi0": "override prompt sabio 0",
    "sabi5": "nuevo sabio 5",
    "orquestador": "override cerebro plan",
    "arbitro": "override cerebro veredicto"
  }
}
```

Compat: también acepta claves legacy `tecnico/estratega/creativo/abogado` mapeadas a `sabi0..3`.

## Prompts y añadir sabios

En `prompts/`: `sabi0.txt`..`sabi3.txt`, `orquestador.txt`, `arbitro.txt` + legados `sabio_*.txt`.

- **Añadir sabio desde frontend**: deslizador N (1-12) → `Aplicar` → POST `/config` genera `sabiX.txt` base si no existe (usa templates rotativos: Historiador, Científico, Economista…).
- **Crear prompt nuevo**: sección `Gestión de Prompts` → nombre + contenido → `Crear y Asignar` (asigna a sabio/cerebro) o `Crear (sin asignar)`.
- **Seleccionable en .html**: cada sabio tiene desplegable con todos los `.txt` disponibles; cambia el `system prompt` sin reiniciar modelos (se inyecta en cada request).

Los prompts nuevos son seleccionables inmediatamente en los desplegables tras `POST /api/prompts`.

### Configuraciones de prompts

`GET /configurations` lista las configuraciones y la activa. Para cambiarla:

```bash
curl -X POST http://127.0.0.1:9082/configurations \
  -H 'Content-Type: application/json' \
  -d '{"action":"select","name":"default"}'
```

Una configuración contiene asignaciones de `sabi0..sabi11`, `orquestador` y
`arbitro` a nombres de prompts `.txt`; no duplica el texto. El campo
`configuration` de una petición puede seleccionar una configuración solo para
esa ejecución, sin cambiar la activa.

### Runtime: modelos, puertos y contexto

El runtime se guarda en `config.json`. Por defecto árbitro, cerebro y
orquestador comparten Ornith 35B en `8205`; pueden separarse asignando otro
`model`/`model_path`, `port` y `context` a cada servicio. Los sabios tienen la
misma estructura bajo `sabi0` ... `sabi11`.

### Compatibilidad con herramientas de harness

`/v1/chat/completions` detecta peticiones OpenAI con `tools`, `functions`,
`tool_calls` o mensajes `role=tool`. En ese caso entrega el turno directamente
al orquestador configurado, conserva la estructura de la respuesta y devuelve
los `tool_calls` al harness. También adapta llamadas que algunos templates
devuelven como texto (`terminal(command='...')`, `skill_view(name='...')` o
`<tool_call>...</tool_call>`/`</invoke>`) a `tool_calls` OpenAI. Hermes ejecuta
entonces `terminal`, `skill_view`, etc., y el siguiente request con los
resultados continúa la conversación. No se ejecutan comandos arbitrarios
dentro del proxy.

También se reconocen adaptadores textuales que usan etiquetas como
`[tool:terminal]` o `[tool:skill_view]`, además del formato Hermes
`<tool_call><function=todo>...</function></tool_call>` para actualizar su lista
de tareas sin dejar la llamada como texto visible.

La web ofrece “Paso 0 — Valores por defecto” y “Configuración paso a paso”:
árbitro, cerebro, orquestador y después cada sabio. Desde el mismo panel se
pueden guardar cambios, desplegar o matar los modelos. La API equivalente es
`POST /control` con `{"action":"start"}` o `{"action":"stop"}`.

Para preparar una instalación nueva:

```bash
./setup.sh                 # descarga los tres modelos GGUF
./setup.sh --skip-download # solo muestra/prepara la operación sin descargar
./TEST_GUIDE.sh
```

El script usa Hugging Face (`hf download`) y permite cambiar repositorios o
ruta mediante `ORNITH35_REPO`, `ORNITH9_REPO`, `QWEN9_REPO` y
`SABIOS2_MODEL_DIR`. Revisa siempre las licencias de los modelos antes de
redistribuirlos.

## VRAM estimada

N=4: 2×Ornith-9B +2×Qwen-9B (~5G c/u, ctx 100K) + 1×35B (~20G, ctx 120K) ≈ 40G (vs 58G de v1 con 2 modelos grandes).
N=8: ~60G. Cabe en 96G junto a producción (~37G en :8181). Si compite, baja ctx en `launch_sabios.sh`.

## Logs

```
/tmp/sabio_8201.log ... /tmp/sabio_8204.log /tmp/sabio_8207.log ...
/tmp/cerebro_8205.log (también /tmp/orquestador_8205.log symlink)
/tmp/consejo2_proxy.log  /tmp/consejo2_web.log
```

## Benchmark de calidad y rendimiento

`benchmarks/quality_benchmark.py` ejecuta el mismo conjunto de tareas contra
cada sabio directamente y contra `solo`, `sabios` y `full` del proxy. Mide el
tiempo completo de la petición, tokens de entrada/salida, tokens/s y una
puntuación reproducible por criterios editables. El JSON guardado contiene
además cada respuesta y el detalle de los criterios acertados, por lo que se
puede comparar una configuración de prompts con otra.

Con los modelos y el proxy arrancados:

```bash
cd ~/sabios2
python3 benchmarks/quality_benchmark.py
```

Pruebas rápidas o comparaciones concretas:

```bash
python3 benchmarks/quality_benchmark.py --targets sabi0,sabi1,sabi2,sabi3
python3 benchmarks/quality_benchmark.py --targets solo,sabios,full --rounds 1 --orq-rounds 1
python3 benchmarks/quality_benchmark.py --targets full --configuration informatico --num-sabios 4
```

Las tareas están en `benchmarks/quality_cases.json`. Cada criterio tiene
`contains_any`, `contains_all`, `regex`, `not_contains` y `weight`; añadir o
modificar casos no requiere tocar el script. La puntuación no sustituye a un
benchmark SWE-bench oficial: es un banco de regresión local, barato y
auditable para medir si un prompt nuevo mejora el resultado sin ocultar el
coste en latencia. Para comparaciones justas, usa la misma temperatura,
`max_tokens`, dataset y número de rondas, y ejecuta cada configuración varias
veces si quieres promediar la variabilidad del muestreo.

### Muestra de datasets de ingeniería

También se puede lanzar una muestra pequeña de `gemma4-complex-v0.1`:

```bash
python3 benchmarks/dataset_benchmark.py \
  --dataset ~/gemma4-training-data/datasets/complex_v0.1_portable/evaluation.jsonl \
  --limit 3 --targets solo,sabios,full --max-tokens 4096
```

Para una muestra del export compacto de SWE-Hero:

```bash
python3 benchmarks/dataset_benchmark.py \
  --dataset ~/datasets/SWE-Hero-openhands-trajectories-compact.jsonl \
  --limit 2 --targets solo,full --max-tokens 4096
```

Esta prueba compara anclas de la respuesta de referencia, forma de parche y
verificaciones mencionadas. Es una señal de triage, no un resultado SWE-bench:
el pass/fail real requiere aplicar el parche al repositorio de la tarea y
ejecutar su oráculo en un agente con herramientas.

## Diferencias vs sabios v1

|  | sabios | sabios2 |
|--|--|--|
| Cerebro | 2 procesos 8205 (35A3B) +8206 (27B) | 1 proceso 8205 Ornith 35B (mismo GGUF que `run_ornith_35b_proxied.sh`, dos prompts) |
| Sabios | 4× Qwen3.5-9B | N× mezcla Ornith-9B/Qwen3.5-9B alternando |
| N configurable | no | sí (1-12) vía launch, CLI, API y web |
| Proxy/web | 8081/8082/8080 | 8081/9082/9080 (proxy 8081 compartido) |
| Pipeline | fijo 4 roles | dinámico `sabi0..` + compat legacy |

## Notas de diseño

- Mismo `pipeline.py` como fuente de verdad (CLI y proxy comparten lógica).
- Sabios en paralelo; rondas 2+ ven opiniones previas.
- Cerebro reutiliza KV cache prompt en 8205 para plan/revisión/ejecución vs árbitro sin reinicio.
