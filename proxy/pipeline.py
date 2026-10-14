#!/usr/bin/env python3
"""
Pipeline del Consejo de Sabios2 — dinámico y cerebro unificado.

Cambios vs sabios v1:
  - Cerebro y Orquestador comparten el mismo modelo Ornith 35B en 8205
    (arbitro -> mismo puerto, distinto system prompt)
  - Sabios dinámicos: N configurable (1-12), mezcla Ornith-9B/Qwen3.5-9B
    Puertos: 8201-8204 + 8207+  (8205 reservado cerebro)
  - Roles genéricos sabi0..sabiN-1 + compat legacy tecnico/estratega/creativo/abogado
"""
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
try:
    from .configurations import load_runtime
except ImportError:  # ejecución directa desde proxy/
    from configurations import load_runtime

# ═══════════════════════════════════════════════════════════════════════════
# Puertos y roles
# ═══════════════════════════════════════════════════════════════════════════

# Cerebro unificado Ornith 35B en 8205 — único Ornith del sistema (8181 apagado al usar sabios2)
CEREBRO_PORT = 8205
# Override: CEREBRO_PORT=8205 python3 ... (env tiene prioridad si se quiere forzar otro puerto)
if os.environ.get("CEREBRO_PORT"):
    try: CEREBRO_PORT = int(os.environ["CEREBRO_PORT"])
    except: pass

def sabio_port(idx: int) -> int:
    try:
        configured = load_runtime(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))["services"].get(f"sabi{idx}", {})
        if configured.get("port") is not None:
            return int(configured["port"])
    except (KeyError, TypeError, ValueError, OSError):
        pass
    if idx < 4:
        return 8201 + idx
    return 8207 + idx - 4

def get_cerebro_port():
    return get_role_port("cerebro")


def get_role_port(role):
    env_name = f"SABIOS2_{role.upper()}_PORT"
    if os.environ.get(env_name):
        try:
            return int(os.environ[env_name])
        except ValueError:
            pass
    try:
        service = load_runtime(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))["services"].get(role, {})
        return int(service.get("port"))
    except (KeyError, TypeError, ValueError, OSError):
        return CEREBRO_PORT if role in ("arbitro", "cerebro", "orquestador") else None

def all_sabio_ports(n):
    return [sabio_port(i) for i in range(n)]

# Roles estáticos + dinámicos
ROLES = {
    "orquestador": {"puerto": CEREBRO_PORT, "prompt_file": "orquestador.txt"},
    "arbitro":     {"puerto": CEREBRO_PORT, "prompt_file": "arbitro.txt"},
    "cerebro":     {"puerto": CEREBRO_PORT, "prompt_file": "orquestador.txt"},
    # compat legacy
    "tecnico":     {"puerto": sabio_port(0), "prompt_file": "sabio_tecnico.txt"},
    "estratega":   {"puerto": sabio_port(1), "prompt_file": "sabio_estratega.txt"},
    "creativo":    {"puerto": sabio_port(2), "prompt_file": "sabio_creativo.txt"},
    "abogado":     {"puerto": sabio_port(3), "prompt_file": "sabio_abogado.txt"},
}
# sabi0..sabi11
for i in range(12):
    key = f"sabi{i}"
    if key not in ROLES:
        ROLES[key] = {"puerto": sabio_port(i), "prompt_file": f"sabi{i}.txt"}
    # también alias sabio_? no necesario

SABIOS_ROLES_DEFAULT = ["sabi0", "sabi1", "sabi2", "sabi3"]
# Compat: si alguien pide legacy, mapear a sabi
LEGACY_TO_SABI = {"tecnico": "sabi0", "estratega": "sabi1", "creativo": "sabi2", "abogado": "sabi3"}
SABI_TO_LEGACY = {v: k for k, v in LEGACY_TO_SABI.items()}

def get_sabios_roles(num: int = None, config=None):
    if config is not None and hasattr(config, 'num_sabios') and config.num_sabios:
        num = config.num_sabios
    if num is None:
        # intentar leer config.json junto al pipeline
        try:
            cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json")
            if os.path.exists(cfg_path):
                import json as _j
                num = _j.load(open(cfg_path)).get("num_sabios", 4)
        except: pass
    if num is None:
        num = 4
    num = max(1, min(12, int(num)))
    return [f"sabi{i}" for i in range(num)]

# Para compat externa
SABIOS_ROLES = SABIOS_ROLES_DEFAULT

# ═══════════════════════════════════════════════════════════════════════════
# Configuración
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class PipelineConfig:
    mode: str = "full"
    rounds: int = 1
    orq_rounds: int = 1
    num_sabios: int = 4
    max_tokens_sabios: int = 4096
    max_tokens_plan: int = 2048
    max_tokens_arbitro: int = 2048
    max_tokens_ejecucion: int = 4096
    timeout: int = 600
    temperature_sabios: float = 1.0
    temperature_plan: float = 0.8
    temperature_arbitro: float = 0.7
    temperature_ejecucion: float = 0.8
    prompt_overrides: dict = field(default_factory=dict)

# ═══════════════════════════════════════════════════════════════════════════
# Carga de prompts
# ═══════════════════════════════════════════════════════════════════════════

_PROMPTS_CACHE = {}

def load_prompts(prompts_dir):
    global _PROMPTS_CACHE
    if not prompts_dir or not os.path.isdir(prompts_dir):
        return _PROMPTS_CACHE
    for f in os.listdir(prompts_dir):
        if f.endswith(".txt"):
            nombre = f[:-4]
            try:
                with open(os.path.join(prompts_dir, f), "r", encoding="utf-8") as fh:
                    _PROMPTS_CACHE[nombre] = fh.read().strip()
            except Exception:
                pass
    return _PROMPTS_CACHE


def reload_prompts(prompts_dir):
    global _PROMPTS_CACHE
    _PROMPTS_CACHE = {}
    return load_prompts(prompts_dir)


def get_prompt(role, prompts_dir=None, overrides=None):
    """Prioridad: overrides > archivo > None. Soporta aliases legacy."""
    # mapear legacy a sabi si existe override genérico?
    if overrides and role in overrides and overrides[role]:
        return overrides[role]
    # también buscar alias inverso: si pidieron sabi0 y override vino como tecnico
    if overrides:
        for legacy, sabi in LEGACY_TO_SABI.items():
            if role == sabi and legacy in overrides and overrides[legacy]:
                return overrides[legacy]
            if role == legacy and sabi in overrides and overrides[sabi]:
                return overrides[sabi]
    if prompts_dir:
        if not _PROMPTS_CACHE:
            load_prompts(prompts_dir)
        # resolver ROLES o fallback a {role}.txt
        fname = ROLES.get(role, {}).get("prompt_file", f"{role}.txt")
        cache_key = fname.replace(".txt", "") if fname else role
        if cache_key and cache_key in _PROMPTS_CACHE:
            return _PROMPTS_CACHE[cache_key]
        # fallback: sabiX busca también sabio_tecnico etc si es 0..3
        if role.startswith("sabi"):
            try:
                idx = int(role[4:])
                legacy = SABI_TO_LEGACY.get(role)
                if legacy and legacy in _PROMPTS_CACHE:
                    return _PROMPTS_CACHE[legacy]
            except: pass
        # fallback directo por role name
        if role in _PROMPTS_CACHE:
            return _PROMPTS_CACHE[role]
    return None

# ═══════════════════════════════════════════════════════════════════════════
# Comunicación con modelos
# ═══════════════════════════════════════════════════════════════════════════

def extraer_texto(data):
    try:
        msg = data.get("choices", [{}])[0].get("message", {})
        if isinstance(msg, str):
            return msg.strip()
        content = msg.get("content") or ""
        reasoning = msg.get("reasoning_content") or ""
        if content.strip():
            return content.strip()
        if reasoning.strip():
            return reasoning.strip()
    except (IndexError, KeyError, AttributeError):
        pass
    return ""


def extraer_usage(data):
    usage = data.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0) or 0
    completion_tokens = usage.get("completion_tokens", 0) or 0
    thinking_tokens = (
        usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0) or 0
    )
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "thinking_tokens": thinking_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def llamar_modelo(puerto, messages, max_tokens=2048, temperature=0.8,
                  timeout=600, extra_body=None):
    url = f"http://127.0.0.1:{puerto}/v1/chat/completions"
    payload = {
        "model": "local",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    if extra_body:
        payload.update(extra_body)
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    dt = time.time() - t0
    return data, dt

# ═══════════════════════════════════════════════════════════════════════════
# Fases del pipeline
# ═══════════════════════════════════════════════════════════════════════════

def _llamar_sabio(rol, user_msg, prompts_dir, overrides, config):
    cfg = config or PipelineConfig()
    prompt = get_prompt(rol, prompts_dir, overrides)
    if not prompt:
        # si es sabi genérico sin archivo, generar prompt base según índice
        try:
            idx = int(rol[4:]) if rol.startswith("sabi") else -1
        except: idx = -1
        defaults = [
            "Eres el Técnico/Pragmático: evalúa viabilidad, costes y datos duros. Sé ingeniero exigente.",
            "Eres el Estratega/Filósofo: analiza riesgos ocultos, ética y juego a largo plazo.",
            "Eres el Creativo/Disruptivo: propone ideas laterales audaces pero ancladas.",
            "Eres el Abogado del Diablo: cuestiona premisas y busca fallos lógicos.",
        ]
        if 0 <= idx < len(defaults):
            prompt = defaults[idx] + " Formato: 1) VEREDICTO APOYO/RESERVAS/RECHAZO 2) 3 argumentos 3) LA CONDICIÓN."
        else:
            prompt = f"Eres el sabio {idx} del consejo. Aporta una perspectiva única (idx {idx}). Responde en español, máx 200 palabras."

    msgs = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": user_msg},
    ]
    try:
        # puerto según rol
        puerto = get_role_port(rol)
        if puerto is None and rol.startswith("sabi"):
            try: puerto = sabio_port(int(rol[4:]))
            except: puerto = 8201
        data, dt = llamar_modelo(
            puerto, msgs,
            max_tokens=cfg.max_tokens_sabios,
            temperature=cfg.temperature_sabios,
            timeout=cfg.timeout,
        )
        texto = extraer_texto(data)
        usage = extraer_usage(data)
        if not texto:
            texto = f"[Sin respuesta visible — {usage['thinking_tokens']} tokens de razonamiento]"
        return rol, {
            "texto": texto,
            "segundos": round(dt, 2),
            "tokens_gen": usage["completion_tokens"],
            "tokens_prompt": usage["prompt_tokens"],
            "thinking_tokens": usage["thinking_tokens"],
        }
    except Exception as e:
        return rol, {
            "texto": f"ERROR: {e}",
            "segundos": 0,
            "tokens_gen": 0,
            "tokens_prompt": 0,
            "thinking_tokens": 0,
        }


def run_sabios_round(objetivo, prompts_dir, round_num, total_rounds,
                     historial=None, overrides=None, config=None):
    cfg = config or PipelineConfig()
    roles = get_sabios_roles(config=cfg)

    if historial and round_num > 1:
        resumen_anterior = "\n\n".join([
            f"=== {rol.upper()} (ronda {round_num - 1}) ===\n{texto}"
            for rol, texto in historial.items()
        ])
        user_msg = (
            f"OBJETIVO EN DISCUSIÓN:\n{objetivo}\n\n"
            f"OPINIONES DE LOS OTROS SABIOS (RONDA {round_num - 1}):\n"
            f"{resumen_anterior}\n\n"
            f"RONDA {round_num}/{total_rounds} — "
            f"Revisa las opiniones anteriores. Aporta tu perspectiva, "
            f"señala errores que veas en los otros sabios, y confirma o "
            f"modifica tu propia postura."
        )
    else:
        user_msg = f"OBJETIVO EN DISCUSIÓN:\n{objetivo}"

    opiniones = {}
    max_workers = min(len(roles), 8)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                _llamar_sabio, rol, user_msg, prompts_dir, overrides, cfg
            ): rol
            for rol in roles
        }
        for fut in as_completed(futures):
            rol, resultado = fut.result()
            opiniones[rol] = resultado

    return opiniones


def resumen_opiniones(opiniones, config=None):
    cfg = config or PipelineConfig()
    roles = get_sabios_roles(config=cfg)
    # si opiniones tiene más claves que roles, incluir todas ordenadas
    ordered = sorted(opiniones.keys(), key=lambda x: (0 if x in roles else 1, x))
    # priorizar orden de roles
    partes = []
    for rol in roles:
        if rol in opiniones:
            partes.append(f"=== {rol.upper()} ===\n{opiniones[rol].get('texto','')}")
    # extras no contemplados
    for rol in ordered:
        if rol not in roles:
            partes.append(f"=== {rol.upper()} ===\n{opiniones[rol].get('texto','')}")
    return "\n\n".join(partes)


def ejecutar_orquestador_plan(objetivo, opiniones_texto, prompts_dir,
                               overrides=None, config=None):
    cfg = config or PipelineConfig()
    prompt = get_prompt("orquestador", prompts_dir, overrides)
    if not prompt:
        prompt = "Eres un orquestador. Diseña un plan conciso y accionable."
    msgs = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": (
            f"FASE A — PLAN.\n\n"
            f"OBJETIVO:\n{objetivo}\n\n"
            f"OPINIONES DEL CONSEJO:\n{opiniones_texto}\n\n"
            f"DISEÑA UN PLAN. Máximo 300 palabras."
        )},
    ]
    data, dt = llamar_modelo(
        get_role_port("orquestador"), msgs,
        max_tokens=cfg.max_tokens_plan,
        temperature=cfg.temperature_plan,
        timeout=cfg.timeout,
    )
    texto = extraer_texto(data)
    usage = extraer_usage(data)
    if not texto:
        texto = f"[Sin respuesta visible — {usage['thinking_tokens']} tokens de razonamiento]"
    return {
        "texto": texto,
        "segundos": round(dt, 2),
        "tokens_gen": usage["completion_tokens"],
        "tokens_prompt": usage["prompt_tokens"],
        "thinking_tokens": usage["thinking_tokens"],
    }


def ejecutar_arbitro(objetivo, opiniones_texto, plan_texto, prompts_dir,
                      overrides=None, config=None):
    cfg = config or PipelineConfig()
    prompt = get_prompt("arbitro", prompts_dir, overrides)
    if not prompt:
        prompt = "Eres un árbitro experto. Evalúa el plan y decide la mejor solución."
    msgs = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": (
            f"OBJETIVO:\n{objetivo}\n\n"
            f"PLAN DEL ORQUESTADOR:\n{plan_texto}\n\n"
            f"OPINIONES DEL CONSEJO:\n{opiniones_texto}\n\n"
            f"DECIDE LA MEJOR SOLUCIÓN."
        )},
    ]
    data, dt = llamar_modelo(
        get_role_port("arbitro"), msgs,
        max_tokens=cfg.max_tokens_arbitro,
        temperature=cfg.temperature_arbitro,
        timeout=cfg.timeout,
    )
    texto = extraer_texto(data)
    usage = extraer_usage(data)
    if not texto:
        texto = f"[Sin respuesta visible — {usage['thinking_tokens']} tokens de razonamiento]"
    return {
        "texto": texto,
        "segundos": round(dt, 2),
        "tokens_gen": usage["completion_tokens"],
        "tokens_prompt": usage["prompt_tokens"],
        "thinking_tokens": usage["thinking_tokens"],
    }


def ejecutar_orquestador_revisar(objetivo, plan_actual, arbitro_texto,
                                  opiniones_texto, prompts_dir,
                                  ronda=None, overrides=None, config=None):
    cfg = config or PipelineConfig()
    prompt = get_prompt("orquestador", prompts_dir, overrides)
    if not prompt:
        prompt = "Eres un orquestador. Revisa el plan según el árbitro."
    ronda_label = f" (ronda {ronda})" if ronda else ""
    msgs = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": (
            f"FASE A — REVISIÓN DEL PLAN{ronda_label}.\n\n"
            f"OBJETIVO:\n{objetivo}\n\n"
            f"PLAN ACTUAL:\n{plan_actual}\n\n"
            f"VEREDICTO DEL ÁRBITRO:\n{arbitro_texto}\n\n"
            f"OPINIONES DEL CONSEJO:\n{opiniones_texto}\n\n"
            f"Revisa el plan según el veredicto. "
            f"Devuelve el PLAN ACTUALIZADO, máximo 300 palabras."
        )},
    ]
    data, dt = llamar_modelo(
        get_role_port("orquestador"), msgs,
        max_tokens=cfg.max_tokens_plan,
        temperature=cfg.temperature_plan,
        timeout=cfg.timeout,
    )
    texto = extraer_texto(data)
    usage = extraer_usage(data)
    if not texto:
        texto = plan_actual
    return {
        "texto": texto,
        "segundos": round(dt, 2),
        "tokens_gen": usage["completion_tokens"],
        "tokens_prompt": usage["prompt_tokens"],
        "thinking_tokens": usage["thinking_tokens"],
    }


def ejecutar_orquestador_ejecucion(objetivo, plan_final, prompts_dir,
                                    overrides=None, config=None):
    cfg = config or PipelineConfig()
    prompt = get_prompt("orquestador", prompts_dir, overrides)
    if not prompt:
        prompt = "Eres un orquestador. Ejecuta la solución paso a paso."
    msgs = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": (
            f"FASE B — EJECUCIÓN.\n\n"
            f"OBJETIVO:\n{objetivo}\n\n"
            f"PLAN APROBADO:\n{plan_final}\n\n"
            f"EJECUTA EL PLAN. Sé autocontenido, paso a paso. "
            f"No re-explicues el plan, ejecútalo directamente."
        )},
    ]
    data, dt = llamar_modelo(
        get_role_port("orquestador"), msgs,
        max_tokens=cfg.max_tokens_ejecucion,
        temperature=cfg.temperature_ejecucion,
        timeout=cfg.timeout,
    )
    texto = extraer_texto(data)
    usage = extraer_usage(data)
    if not texto:
        texto = f"[Sin respuesta visible — {usage['thinking_tokens']} tokens de razonamiento]"
    return {
        "texto": texto,
        "segundos": round(dt, 2),
        "tokens_gen": usage["completion_tokens"],
        "tokens_prompt": usage["prompt_tokens"],
        "thinking_tokens": usage["thinking_tokens"],
    }

# ═══════════════════════════════════════════════════════════════════════════
# Pipeline principal
# ═══════════════════════════════════════════════════════════════════════════

def ejecutar_pipeline(user_message, prompts_dir=None, config=None, log_fn=None):
    cfg = config or PipelineConfig()
    log = log_fn or print
    t_inicio = time.time()

    resultados = {
        "user_message": user_message,
        "mode": cfg.mode,
        "rounds": cfg.rounds,
        "orq_rounds": cfg.orq_rounds,
        "num_sabios": cfg.num_sabios,
    }

    # ── FASE 0: Rondas de sabios ──────────────────────────────────────
    historial_sabios = None
    for ronda in range(1, cfg.rounds + 1):
        log(f"  [PIPELINE] Ronda sabios {ronda}/{cfg.rounds} ({cfg.num_sabios} sabios)...")
        opiniones = run_sabios_round(
            user_message, prompts_dir, ronda, cfg.rounds,
            historial=historial_sabios,
            overrides=cfg.prompt_overrides or {},
            config=cfg,
        )
        key = f"sabios_ronda_{ronda}" if cfg.rounds > 1 else "sabios"
        resultados[key] = opiniones
        historial_sabios = {rol: op["texto"] for rol, op in opiniones.items()}
        log(f"  [PIPELINE] Ronda sabios {ronda} completa: "
            f"{sum(1 for v in opiniones.values() if 'ERROR' not in v.get('texto',''))}/{cfg.num_sabios} OK")

    opiniones_texto = resumen_opiniones(opiniones, config=cfg)

    if cfg.mode == "solo":
        log("  [PIPELINE] Modo solo: baseline sin consejo.")
        prompt = get_prompt("orquestador", prompts_dir,
                            cfg.prompt_overrides or {})
        if not prompt:
            prompt = "Eres un orquestador. Ejecuta la solución."
        msgs = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": (
                f"FASE B — EJECUCIÓN directa (baseline, sin consejo).\n\n"
                f"OBJETIVO:\n{user_message}"
            )},
        ]
        data, dt = llamar_modelo(
            get_role_port("cerebro"), msgs,
            max_tokens=cfg.max_tokens_ejecucion,
            temperature=cfg.temperature_ejecucion,
            timeout=cfg.timeout,
        )
        resultados["respuesta_directa"] = {
            "texto": extraer_texto(data),
            "segundos": round(dt, 2),
            **extraer_usage(data),
        }
        resultados["total_segundos"] = round(time.time() - t_inicio, 2)
        return resultados

    # ── FASE 1: Orquestador planifica ─────────────────────────────────
    log(f"  [PIPELINE] Orquestador (puerto {get_role_port('orquestador')}): diseñando plan...")
    plan_result = ejecutar_orquestador_plan(
        user_message, opiniones_texto, prompts_dir,
        overrides=cfg.prompt_overrides or {}, config=cfg,
    )
    resultados["plan"] = plan_result
    log(f"  [PIPELINE] Plan: {plan_result['segundos']}s, "
        f"{len(plan_result['texto'])} chars")

    if cfg.mode == "sabios":
        resultados["total_segundos"] = round(time.time() - t_inicio, 2)
        return resultados

    # ── FASE 2: Rondas de arbitraje + revisión (mismo cerebro, distinto prompt) ─
    plan_actual = plan_result["texto"]
    for ronda in range(1, cfg.orq_rounds + 1):
        es_ultima = ronda == cfg.orq_rounds
        log(f"  [PIPELINE] Árbitro (puerto {get_role_port('arbitro')}, prompt árbitro) ronda {ronda}/{cfg.orq_rounds}...")

        arbitro_result = ejecutar_arbitro(
            user_message, opiniones_texto, plan_actual, prompts_dir,
            overrides=cfg.prompt_overrides or {}, config=cfg,
        )
        key_arb = (f"arbitro" if cfg.orq_rounds == 1
                   else f"arbitro_ronda_{ronda}")
        resultados[key_arb] = arbitro_result
        log(f"  [PIPELINE] Árbitro ronda {ronda}: "
            f"{arbitro_result['segundos']}s")

        if not es_ultima:
            log(f"  [PIPELINE] Orquestador: revisando plan ronda {ronda}...")
            rev = ejecutar_orquestador_revisar(
                user_message, plan_actual, arbitro_result["texto"],
                opiniones_texto, prompts_dir,
                ronda=ronda, overrides=cfg.prompt_overrides or {},
                config=cfg,
            )
            plan_actual = rev["texto"]
            resultados[f"revision_plan_ronda_{ronda}"] = rev
            log(f"  [PIPELINE] Plan revisado ronda {ronda}: "
                f"{rev['segundos']}s")

    resultados["plan_final"] = plan_actual

    # ── FASE 3: Orquestador ejecuta ───────────────────────────────────
    log(f"  [PIPELINE] Orquestador (puerto {get_role_port('orquestador')}): ejecutando plan final...")
    ejecucion = ejecutar_orquestador_ejecucion(
        user_message, plan_actual, prompts_dir,
        overrides=cfg.prompt_overrides or {}, config=cfg,
    )
    resultados["ejecucion"] = ejecucion
    log(f"  [PIPELINE] Ejecución: {ejecucion['segundos']}s, "
        f"{len(ejecucion['texto'])} chars")

    resultados["total_segundos"] = round(time.time() - t_inicio, 2)
    return resultados


def extraer_respuesta_final(resultados):
    if "respuesta_directa" in resultados:
        return resultados["respuesta_directa"].get("texto", "")
    if "ejecucion" in resultados:
        return resultados["ejecucion"].get("texto", "")
    if "plan_final" in resultados:
        return resultados["plan_final"].get("texto", "")
    if "plan" in resultados:
        return resultados["plan"].get("texto", "")
    return "Sin respuesta."


def agregar_usage_total(resultados):
    """Suma usage tolerando el formato interno histórico del pipeline.

    Las fases guardan ``tokens_prompt``/``tokens_gen`` para sus resultados
    detallados, mientras que la API OpenAI expone ``prompt_tokens``/
    ``completion_tokens``. Antes sólo se buscaba el segundo formato y el
    proxy devolvía usage=0 para ``sabios`` y ``full``.
    """
    total_prompt = 0
    total_completion = 0
    total_thinking = 0
    def add(value):
        nonlocal total_prompt, total_completion, total_thinking
        if not isinstance(value, dict):
            return
        total_prompt += value.get("prompt_tokens", value.get("tokens_prompt", 0)) or 0
        total_completion += value.get("completion_tokens", value.get("tokens_gen", 0)) or 0
        total_thinking += value.get("thinking_tokens", 0) or 0

    for key, val in resultados.items():
        if isinstance(val, dict) and (
            "prompt_tokens" in val or "tokens_prompt" in val
        ):
            add(val)
        elif isinstance(val, dict) and not any(
            k in val for k in ["texto", "prompt_tokens", "tokens_prompt", "segundos"]
        ):
            for sub_val in val.values():
                if isinstance(sub_val, dict) and (
                    "prompt_tokens" in sub_val or "tokens_prompt" in sub_val
                ):
                    add(sub_val)
    return {
        "prompt_tokens": total_prompt,
        "completion_tokens": total_completion,
        "thinking_tokens": total_thinking,
        "total_tokens": total_prompt + total_completion,
    }
