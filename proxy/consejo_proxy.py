#!/usr/bin/env python3
"""
Proxy OpenAI-compatible del Consejo de Sabios2.

Cambios vs v1:
  - Cerebro unificado: orquestador y árbitro comparten puerto 8205
  - Sabios dinámicos: N configurable (1-12) mezcla Ornith-9B/Qwen3.5-9B
  - Proxy en 8081 igual que v1 (solo uno a la vez); prompts/web en 9082/9080
    Sobreescribible vía env: CONSEJO_PROXY_PORT, CONSEJO_PROMPTS_PORT
  - Soporta campo "num_sabios" en request + añadir prompts para sabi nuevos

Puertos por defecto:
  8081  /v1/chat/completions, /status, /v1/models
  9082  /api/prompts
  Front web: 9080 (si usas proxy/start.sh)

Uso:
  python3 ~/sabios2/proxy/consejo_proxy.py
  ~/sabios2/proxy/start.sh   (con auto-reinicio)
"""
import json
import os
import sys
import time
import signal
import subprocess
import uuid
import ast
import html
import re
import urllib.request
import urllib.error
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline import (
    PipelineConfig, load_prompts, reload_prompts, ejecutar_pipeline,
    extraer_respuesta_final, agregar_usage_total, ROLES, get_sabios_roles, sabio_port, CEREBRO_PORT, get_cerebro_port,
    llamar_modelo, get_prompt, get_role_port,
)
from configurations import (load_configurations, prompt_overrides as profile_prompt_overrides,
                             save_profile, set_active, active_name, load_runtime,
                             update_runtime, atomic_write_text, atomic_delete)

# ═══════════════════════════════════════════════════════════════════════════
# Configuración
# ═══════════════════════════════════════════════════════════════════════════

# Proxy en 8081 igual que v1 (solo se usa uno a la vez); prompts/web en 9082/9080
PUERTO_PROXY = int(os.environ.get("CONSEJO_PROXY_PORT", 8081))
PUERTO_PROMPTS = int(os.environ.get("CONSEJO_PROMPTS_PORT", 9082))
PROMPTS_DIR = os.environ.get(
    "CONSEJO_PROMPTS_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "prompts"),
)
PROMPTS_DIR = os.path.normpath(PROMPTS_DIR)
RESULTS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "results"
)
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json")
os.makedirs(RESULTS_DIR, exist_ok=True)

def get_default_num_sabios():
    return load_runtime(os.path.dirname(PROMPTS_DIR)).get("num_sabios", 4)

# Cargar prompts iniciales
load_prompts(PROMPTS_DIR)

# ═══════════════════════════════════════════════════════════════════════════
# CORS helpers
# ═══════════════════════════════════════════════════════════════════════════

def send_cors(handler):
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers",
                        "Content-Type, Authorization")


def send_json(handler, status, data, indent=False):
    body = json.dumps(data, ensure_ascii=False,
                      indent=2 if indent else None).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    send_cors(handler)
    handler.end_headers()
    handler.wfile.write(body)


# ═══════════════════════════════════════════════════════════════════════════
# Handler: Proxy (puerto proxy)
# ═══════════════════════════════════════════════════════════════════════════

class ProxyHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        if args:
            print(f"[PROXY] {args[0]}")

    def do_OPTIONS(self):
        self.send_response(200)
        send_cors(self)
        self.end_headers()

    def do_GET(self):
        if self.path == "/v1/models":
            send_json(self, 200, {
                "object": "list",
                "data": [
                    {"id": "consejo-sabios2","object": "model","created": int(time.time()),"owned_by": "consejo2","permission": []},
                    {"id": "consejo-sabios2-full","object": "model","created": int(time.time()),"owned_by": "consejo2","permission": []},
                ],
                "total": 2,
            })
        elif self.path == "/status":
            n = get_default_num_sabios()
            estados_detail = {}
            for role in ("arbitro", "cerebro", "orquestador"):
                p = get_role_port(role)
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{p}/health", timeout=2):
                        estados_detail[f"{role}_{p}"] = "OK"
                except Exception:
                    estados_detail[f"{role}_{p}"] = "DOWN"
            # sabios
            for i in range(n):
                p = sabio_port(i)
                try:
                    req = urllib.request.Request(f"http://127.0.0.1:{p}/health", method="GET")
                    with urllib.request.urlopen(req, timeout=2):
                        estados_detail[f"sabi{i}_{p}"] = "OK"
                        estados_detail[str(p)] = "OK"
                except:
                    estados_detail[f"sabi{i}_{p}"] = "DOWN"
                    estados_detail[str(p)] = "DOWN"
            send_json(self, 200, {"models": estados_detail, "num_sabios": n, "cerebro_port": get_cerebro_port()}, indent=True)
        elif self.path == "/config":
            runtime = load_runtime(os.path.dirname(PROMPTS_DIR))
            send_json(self, 200, {"num_sabios": runtime["num_sabios"], "cerebro_port": get_cerebro_port(), "sabio_ports": [sabio_port(i) for i in range(runtime["num_sabios"])], "runtime": runtime}, indent=True)
        else:
            send_json(self, 404, {"error": "Not found"})

    def do_POST(self):
        if self.path == "/v1/chat/completions":
            self._handle_chat()
        elif self.path == "/api/prompts":
            self._handle_prompts()
        else:
            send_json(self, 404, {"error": "Not found"})

    def _handle_chat(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            request_data = json.loads(body)
        except json.JSONDecodeError:
            send_json(self, 400, {"error": "JSON inválido"})
            return

        # No reducir una petición con tools a un único user_message: Hermes
        # necesita recibir tool_calls estructurados y enviar después role=tool.
        if _has_tool_protocol(request_data):
            _handle_tool_turn(self, request_data)
            return

        user_message = ""
        system_messages = []
        messages = request_data.get("messages", [])
        for m in messages:
            role = m.get("role", "")
            content = _content_to_text(m.get("content", ""))
            if role == "system" and content:
                system_messages.append(content)
            elif role == "user" and content:
                user_message = content
        if not user_message:
            for m in reversed(messages):
                if m.get("role") == "user":
                    user_message = _content_to_text(m.get("content", ""))
                    break
        if not user_message:
            send_json(self, 400, {"error": "No se encontró mensaje de usuario"})
            return

        want_stream = request_data.get("stream", False)
        raw_model = request_data.get("model", "full")
        _MODEL_MAP = {"consejo-sabios": "full","consejo-sabios2": "full","full": "full","sabios": "sabios","solo": "solo"}
        mode = _MODEL_MAP.get(raw_model, "full")

        rounds = request_data.get("rounds", 1)
        try: rounds = max(1, min(5, int(rounds)))
        except: rounds = 1
        orq_rounds = request_data.get("orq_rounds", 1)
        try: orq_rounds = max(1, min(3, int(orq_rounds)))
        except: orq_rounds = 1
        num_sabios = request_data.get("num_sabios", request_data.get("numSabios", None))
        if num_sabios is None:
            # también puede venir en system_prompts count
            num_sabios = get_default_num_sabios()
        try:
            num_sabios = max(1, min(12, int(num_sabios)))
        except: num_sabios = get_default_num_sabios()

        configuration = request_data.get("configuration") or active_name(
            os.path.dirname(PROMPTS_DIR), PROMPTS_DIR)
        prompt_overrides = profile_prompt_overrides(
            os.path.dirname(PROMPTS_DIR), PROMPTS_DIR, configuration)
        raw_overrides = request_data.get("system_prompts")
        prompt_overrides.update(_mapear_overrides(raw_overrides))

        config = PipelineConfig(
            mode=mode,
            rounds=rounds,
            orq_rounds=orq_rounds,
            num_sabios=num_sabios,
            prompt_overrides=prompt_overrides,
        )

        task_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        print(f"\n[PROXY2] ====== NUEVA SOLICITUD ======")
        print(f"[PROXY2] task={task_id} mode={mode} rounds={rounds} orq_rounds={orq_rounds} num_sabios={num_sabios}")

        try:
            pipeline_msg = _conversation_to_text(messages) or user_message
            if system_messages:
                ctx = "\n\n".join(system_messages)
                pipeline_msg = f"CONTEXTO DEL SISTEMA:\n{ctx}\n\nOBJETIVO:\n{user_message}"

            resultados = ejecutar_pipeline(
                pipeline_msg,
                prompts_dir=PROMPTS_DIR,
                config=config,
            )
            texto = extraer_respuesta_final(resultados)
            # Algunos harnesses no declaran ``tools`` en la primera petición,
            # pero el modelo puede emitir igualmente la intención de usar una
            # herramienta en formato textual. Convertirla aquí permite que el
            # cliente reciba un tool_call estándar y continúe el ciclo.
            parsed_calls, cleaned_text = _parse_text_tool_calls(
                texto, _tool_names_from_request(request_data)
            )
            response_message = {
                "role": "assistant",
                "content": cleaned_text if cleaned_text else "",
            }
            finish_reason = "stop"
            if parsed_calls:
                response_message["tool_calls"] = _make_structured_tool_calls(parsed_calls)
                finish_reason = "tool_calls"
            usage = agregar_usage_total(resultados)

            resultados["configuration"] = configuration
            _guardar_resultado(task_id, user_message, mode, rounds, orq_rounds, num_sabios, resultados)

            completion_id = f"consejo2-{task_id}"
            created = int(time.time())
            model_name = f"consejo-sabios2-{mode}"

            if want_stream:
                self.send_response(200)
                self.send_header("Content-Type","text/event-stream; charset=utf-8")
                self.send_header("Cache-Control","no-cache")
                self.send_header("Connection","keep-alive")
                send_cors(self)
                self.end_headers()
                delta = {key: response_message[key] for key in ("role", "content", "tool_calls") if key in response_message}
                chunk = {"id": completion_id,"object": "chat.completion.chunk","created": created,"model": model_name,"choices": [{"index":0,"delta": delta,"finish_reason":None}]}
                self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()); self.wfile.flush()
                chunk_stop = {"id": completion_id,"object": "chat.completion.chunk","created": created,"model": model_name,"choices": [{"index":0,"delta":{},"finish_reason":finish_reason}],"usage": usage}
                self.wfile.write(f"data: {json.dumps(chunk_stop, ensure_ascii=False)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
            else:
                send_json(self, 200, {
                    "id": completion_id,"object": "chat.completion","created": created,"model": model_name,
                    "choices": [{"index":0,"message": response_message,"finish_reason":finish_reason}],
                    "usage": usage,
                })
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"[PROXY2] ERROR: {e}")
            send_json(self, 500, {"error": {"message": str(e), "type": "pipeline_error"}})

    def _handle_prompts(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try: data = json.loads(body)
        except: send_json(self, 400, {"error": "JSON inválido"}); return
        nombre = data.get("nombre", "")
        contenido = data.get("contenido", "")
        accion = data.get("accion", "crear")
        if not nombre: send_json(self, 400, {"error": "Nombre requerido"}); return
        nombre_limpio = "".join(c for c in nombre if c.isalnum() or c in "-_")
        if not nombre_limpio or nombre_limpio != nombre:
            send_json(self, 400, {"error": "Nombre inválido"}); return
        if accion == "borrar":
            ruta = os.path.join(PROMPTS_DIR, nombre_limpio + ".txt")
            atomic_delete(ruta)
            send_json(self, 200, {"ok": True, "mensaje": f"Prompt '{nombre_limpio}' borrado"})
        else:
            if not contenido: send_json(self, 400, {"error": "Contenido requerido"}); return
            os.makedirs(PROMPTS_DIR, exist_ok=True)
            ruta = os.path.join(PROMPTS_DIR, nombre_limpio + ".txt")
            with open(ruta, "w", encoding="utf-8") as f: f.write(contenido)
            reload_prompts(PROMPTS_DIR)
            send_json(self, 200, {"ok": True, "mensaje": f"Prompt '{nombre_limpio}' guardado"})


# ═══════════════════════════════════════════════════════════════════════════
# Handler: Prompts API (puerto prompts)
# ═══════════════════════════════════════════════════════════════════════════

class PromptsHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if args: print(f"[PROMPTS2] {args[0]}")
    def do_OPTIONS(self):
        self.send_response(200); send_cors(self); self.end_headers()
    def do_GET(self):
        if self.path == "/api/prompts":
            prompts = load_prompts(PROMPTS_DIR)
            send_json(self, 200, {"prompts": dict(prompts), "count": len(prompts), "num_sabios": get_default_num_sabios(), "cerebro_port": get_cerebro_port(), "configuration": active_name(os.path.dirname(PROMPTS_DIR), PROMPTS_DIR)}, indent=True)
        elif self.path == "/configurations":
            profiles, active = load_configurations(os.path.dirname(PROMPTS_DIR), PROMPTS_DIR)
            send_json(self, 200, {"configurations": profiles, "active": active}, indent=True)
        elif self.path == "/config":
            send_json(self, 200, {"num_sabios": get_default_num_sabios(), "cerebro_port": get_cerebro_port(), "configuration": active_name(os.path.dirname(PROMPTS_DIR), PROMPTS_DIR)}, indent=True)
        else:
            send_json(self, 404, {"error": "Not found"})
    def do_POST(self):
        if self.path not in ("/api/prompts","/config","/configurations","/control"):
            send_json(self, 404, {"error": "Not found"}); return
        if self.path == "/control":
            content_length = int(self.headers.get("Content-Length", 0))
            try: data = json.loads(self.rfile.read(content_length))
            except json.JSONDecodeError: send_json(self, 400, {"error": "JSON inválido"}); return
            action = data.get("action", "status")
            launcher = os.path.abspath(os.path.join(os.path.dirname(PROMPTS_DIR), "launch_all.sh"))
            try:
                if action == "start":
                    proc = subprocess.Popen([launcher, "start"], cwd=os.path.dirname(launcher),
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            start_new_session=True)
                    send_json(self, 202, {"ok": True, "action": action, "pid": proc.pid})
                elif action == "stop":
                    proc = subprocess.Popen([launcher, "stop"], cwd=os.path.dirname(launcher),
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            start_new_session=True)
                    send_json(self, 202, {"ok": True, "action": action, "pid": proc.pid})
                elif action == "status":
                    send_json(self, 200, {"ok": True, "action": action})
                else:
                    send_json(self, 400, {"error": "action debe ser start, stop o status"})
            except OSError as e:
                send_json(self, 500, {"error": str(e)})
            return
        if self.path == "/configurations":
            content_length = int(self.headers.get("Content-Length", 0))
            try: data = json.loads(self.rfile.read(content_length))
            except json.JSONDecodeError: send_json(self, 400, {"error": "JSON inválido"}); return
            base_dir = os.path.dirname(PROMPTS_DIR)
            try:
                action = data.get("action", "select")
                if action == "select":
                    set_active(base_dir, PROMPTS_DIR, data.get("name", ""))
                elif action in ("save", "create"):
                    save_profile(base_dir, PROMPTS_DIR, data.get("name", ""), data)
                    if data.get("activate", True):
                        set_active(base_dir, PROMPTS_DIR, data.get("name", ""))
                else:
                    raise ValueError("action debe ser select, save o create")
                profiles, active = load_configurations(base_dir, PROMPTS_DIR)
                send_json(self, 200, {"ok": True, "configurations": profiles, "active": active}, indent=True)
            except (ValueError, OSError) as e:
                send_json(self, 400, {"error": str(e)})
            return
        if self.path == "/config":
            # actualizar num_sabios
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try: data = json.loads(body)
            except: send_json(self, 400, {"error": "JSON inválido"}); return
            n = data.get("num_sabios")
            try: n = max(1, min(12, int(n)))
            except: send_json(self, 400, {"error": "num_sabios 1-12"}); return
            try:
                runtime = update_runtime(os.path.dirname(PROMPTS_DIR), data)
            except Exception as e: send_json(self, 500, {"error":str(e)}); return
            # si no hay prompts para nuevo sabio, crear archivo base
            for i in range(n):
                p = os.path.join(PROMPTS_DIR, f"sabi{i}.txt")
                if not os.path.exists(p):
                    # crear desde template si existe sabio_tecnico etc, sino genérico
                    template = ""
                    if i < 4:
                        legacy_map = ["sabio_tecnico.txt","sabio_estratega.txt","sabio_creativo.txt","sabio_abogado.txt"]
                        lp = os.path.join(PROMPTS_DIR, legacy_map[i])
                        if os.path.exists(lp):
                            template = open(lp, encoding="utf-8").read()
                    if not template:
                        defaults = [
                            "Eres el Técnico/Pragmático del consejo (Tesla+CTO). Evalúa viabilidad, costes y datos duros. Máx 200 palabras.",
                            "Eres el Estratega/Filósofo (Maquiavelo+Marco Aurelio). Riesgos ocultos, éticos y a largo plazo.",
                            "Eres el Creativo/Disruptivo (Da Vinci+Jobs). Ideas laterales ancladas.",
                            "Eres el Abogado del Diablo (Sócrates). Cuestiona premisas y fallos lógicos.",
                        ]
                        # para idx >=4, générico rotativo
                        if i < 4:
                            template = defaults[i] + "\nFormato: VEREDICTO + 3 argumentos + LA CONDICIÓN."
                        else:
                            roles_extra = ["Historiador","Científico","Economista","Psicólogo","Ingeniero de Seguridad","Ética IA","Product Manager","Futurista"]
                            rol = roles_extra[(i-4) % len(roles_extra)]
                            template = f"Eres el {rol} del consejo (sabio {i}). Aporta perspectiva de {rol} con rigor. Máx 200 palabras.\nFormato: VEREDICTO + 3 argumentos + LA CONDICIÓN."
                    os.makedirs(PROMPTS_DIR, exist_ok=True)
                    atomic_write_text(p, template)
            reload_prompts(PROMPTS_DIR)
            send_json(self, 200, {"ok": True, "num_sabios": runtime["num_sabios"], "runtime": runtime})
            return
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try: data = json.loads(body)
        except: send_json(self, 400, {"error": "JSON inválido"}); return
        nombre = data.get("nombre", "")
        contenido = data.get("contenido", "")
        accion = data.get("accion", "crear")
        if not nombre: send_json(self, 400, {"error": "Nombre requerido"}); return
        nombre_limpio = "".join(c for c in nombre if c.isalnum() or c in "-_")
        if not nombre_limpio or nombre_limpio != nombre: send_json(self, 400, {"error": "Nombre inválido"}); return
        if accion == "borrar":
            ruta = os.path.join(PROMPTS_DIR, nombre_limpio + ".txt")
            atomic_delete(ruta)
            send_json(self, 200, {"ok": True, "mensaje": f"Prompt '{nombre_limpio}' borrado"})
        else:
            if not contenido: send_json(self, 400, {"error": "Contenido requerido"}); return
            os.makedirs(PROMPTS_DIR, exist_ok=True)
            ruta = os.path.join(PROMPTS_DIR, nombre_limpio + ".txt")
            atomic_write_text(ruta, contenido)
            reload_prompts(PROMPTS_DIR)
            send_json(self, 200, {"ok": True, "mensaje": f"Prompt '{nombre_limpio}' guardado"})


# ═══════════════════════════════════════════════════════════════════════════
# Guardado de resultados
# ═══════════════════════════════════════════════════════════════════════════

def _guardar_resultado(task_id, objetivo, mode, rounds, orq_rounds, num_sabios, resultados):
    out = {"task": task_id,"objetivo": objetivo,"mode": mode,"rounds": rounds,"orq_rounds": orq_rounds,"num_sabios": num_sabios,"inicio": time.strftime("%Y-%m-%d %H:%M:%S")}
    out.update(resultados)
    path = os.path.join(RESULTS_DIR, f"{task_id}.json")
    try:
        atomic_write_text(path, json.dumps(out, ensure_ascii=False, indent=2) + "\n")
        print(f"[PROXY2] Resultado guardado en {path}")
    except Exception as e: print(f"[PROXY2] ERROR guardando: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# Mapeo frontend → pipeline (ahora directo sabi0..sabi11)
# ═══════════════════════════════════════════════════════════════════════════

def _mapear_overrides(raw):
    if not raw or not isinstance(raw, dict): return {}
    mapped = {}
    for key, val in raw.items():
        # directo sabi, orquestador, arbitro
        if key in ROLES:
            mapped[key] = val
        # también permitir tecnico etc legacy
        elif key in ["tecnico","estratega","creativo","abogado"]:
            mapped[key] = val
        # fallback: cualquier clave que empiece por sabi
        elif key.startswith("sabi"):
            mapped[key] = val
    return mapped


def _content_to_text(content):
    """Convierte contenido OpenAI (texto o lista de partes) en texto legible."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                if part.get("type") in ("text", "input_text"):
                    parts.append(str(part.get("text", part.get("content", ""))))
                elif part.get("type") in ("tool_result", "output_text"):
                    parts.append(str(part.get("content", part.get("text", ""))))
        return "\n".join(p for p in parts if p)
    if content is None:
        return ""
    return str(content)


def _conversation_to_text(messages):
    """Fallback para clientes que mandan transcripciones sin tools formales."""
    rendered = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role", "unknown")
        text = _content_to_text(message.get("content"))
        tool_calls = message.get("tool_calls")
        if tool_calls:
            text += "\nTOOL_CALLS:\n" + json.dumps(tool_calls, ensure_ascii=False)
        name = message.get("name") or message.get("tool_call_id")
        label = f"{role}({name})" if name else role
        if text.strip():
            rendered.append(f"[{label}]\n{text.strip()}")
    return "\n\n".join(rendered)


def _has_tool_protocol(request_data):
    """Detecta tools OpenAI o una continuación con resultados de herramientas."""
    if request_data.get("tools") or request_data.get("functions"):
        return True
    for message in request_data.get("messages", []) or []:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "tool" or message.get("tool_calls"):
            return True
        # Algunos harnesses (incluido Hermes en ciertos adaptadores) mandan
        # la herramienta como texto en vez de usar tools[] de OpenAI.
        raw = _content_to_text(message.get("content"))
        if "[tool:" in raw or "[skill_view" in raw or "tool_call" in raw:
            return True
    return False


def _tool_turn_messages(request_data, prompt):
    """Devuelve mensajes válidos para templates que solo admiten un system.

    Hermes suele enviar su propio ``system``. Insertar otro delante parecía
    inocuo, pero deja dos mensajes de sistema y algunos templates de Qwen
    abortan con ``System message must be at the beginning``. Fusionamos todos
    los system al principio y dejamos intacto el orden de los demás mensajes.
    """
    source = list(request_data.get("messages") or [])
    system_parts = [prompt] if prompt else []
    conversation = []
    for message in source:
        if isinstance(message, dict) and message.get("role") == "system":
            content = _content_to_text(message.get("content"))
            if content:
                system_parts.append(content)
        else:
            conversation.append(message)

    if system_parts:
        return [{"role": "system", "content": "\n\n".join(system_parts)}] + conversation
    return conversation


_TEXT_TOOL_NAMES = {
    "terminal", "skill_view", "tool_search", "tool_describe", "tool_call", "todo"
}


def _parse_call_arguments(raw):
    """Parsea argumentos Python simples usados por algunos adaptadores Hermes."""
    try:
        tree = ast.parse("f(" + raw + ")", mode="eval").body
        args = {}
        for keyword in tree.keywords:
            args[keyword.arg] = ast.literal_eval(keyword.value)
        return args
    except (SyntaxError, ValueError, TypeError):
        # Fallback para comillas/escapes no estándar: suficiente para command=
        args = {}
        for match in re.finditer(r"([A-Za-z_]\w*)\s*=\s*(['\"])(.*?)\2", raw, re.S):
            args[match.group(1)] = bytes(match.group(3), "utf-8").decode("unicode_escape")
        return args


def _find_balanced_call_end(text, start):
    depth = 0
    quote = None
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _parse_text_tool_calls(text, allowed_names=None):
    """Extrae llamadas Python/XML emitidas literalmente por el modelo."""
    if not isinstance(text, str) or not text:
        return [], text
    # Los adaptadores internos pueden no aparecer en tools[] de algunos
    # clientes, pero siguen siendo despachables por Hermes.
    allowed = set(allowed_names or ()) | _TEXT_TOOL_NAMES
    found = []
    spans = []

    # Formato XML de varios templates: <tool_call><parameter ...>...</parameter>.
    xml_pattern = re.compile(
        r"<tool_call\b[^>]*>(.*?)(?:</tool_call\s*>|</invoke\s*>)",
        re.I | re.S,
    )
    for match in xml_pattern.finditer(text):
        block = match.group(1)
        params = {}
        for pm in re.finditer(r"<parameter\s+name=[\"']([^\"']+)[\"']\s*>(.*?)</parameter\s*>", block, re.I | re.S):
            params[pm.group(1)] = html.unescape(pm.group(2)).strip()
        name = params.pop("name", None)
        if not name:
            invoke = re.search(r"<invoke\s+name=[\"']([^\"']+)[\"']", block, re.I)
            name = invoke.group(1) if invoke else None
        if not name:
            # Hermes/OpenHands-style format: <function=todo> ... </function>
            function_tag = re.search(r"<function\s*=\s*([\w.-]+)\s*>", block, re.I)
            name = function_tag.group(1) if function_tag else None
        if name in allowed:
            found.append((match.start(), name, params)); spans.append(match.span())

    # Formato skill_view(name='...') / terminal(command='...').
    names_pattern = "|".join(re.escape(name) for name in sorted(allowed, key=len, reverse=True))
    call_pattern = re.compile(r"(?<![\w.-])(" + names_pattern + r")\s*\(", re.I)
    for match in call_pattern.finditer(text):
        end = _find_balanced_call_end(text, match.end() - 1)
        if end is None:
            continue
        name = match.group(1)
        args = _parse_call_arguments(text[match.end():end])
        if args or text[match.end():end].strip() == "":
            found.append((match.start(), name, args)); spans.append((match.start(), end + 1))

    # Eliminar bloques/calls del contenido visible, pero conservar el resto.
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + text[end:]
    text = re.sub(r"\n\s*<\/?(?:invoke|parameter)[^>]*>\s*", "\n", text, flags=re.I)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    unique = []
    seen = set()
    for _, name, args in sorted(found, key=lambda item: item[0]):
        key = (name.lower(), json.dumps(args, sort_keys=True, ensure_ascii=False))
        if key not in seen:
            seen.add(key); unique.append((name, args))
    return unique, text


def _tool_names_from_request(request_data):
    # Include the built-in textual adapters even if a client omitted their
    # formal schema. Hermes can still dispatch the returned structured call.
    names = set(_TEXT_TOOL_NAMES)
    for item in request_data.get("tools") or []:
        fn = item.get("function", {}) if isinstance(item, dict) else {}
        if fn.get("name"):
            names.add(fn["name"])
    for fn in request_data.get("functions") or []:
        if isinstance(fn, dict) and fn.get("name"):
            names.add(fn["name"])
    return names or _TEXT_TOOL_NAMES


def _make_structured_tool_calls(parsed_calls, existing=None):
    normalized = list(existing or [])
    existing_keys = {
        (c.get("function", {}).get("name"), c.get("function", {}).get("arguments"))
        for c in normalized if isinstance(c, dict)
    }
    for name, arguments in parsed_calls:
        arguments_json = json.dumps(arguments, ensure_ascii=False)
        key = (name, arguments_json)
        if key not in existing_keys:
            normalized.append({
                "id": "call_" + uuid.uuid4().hex[:12],
                "type": "function",
                "function": {"name": name, "arguments": arguments_json},
            })
            existing_keys.add(key)
    return normalized


def _handle_tool_turn(self, request_data):
    """Ejecuta un turno compatible con OpenAI tools y devuelve tool_calls.

    Hermes (u otro harness) ejecuta las herramientas. El proxy no inventa ni
    ejecuta comandos: devuelve la llamada estructurada y espera el siguiente
    request con mensajes role=tool.
    """
    base_dir = os.path.dirname(PROMPTS_DIR)
    configuration = request_data.get("configuration") or active_name(base_dir, PROMPTS_DIR)
    overrides = profile_prompt_overrides(base_dir, PROMPTS_DIR, configuration)
    overrides.update(_mapear_overrides(request_data.get("system_prompts")))
    prompt = get_prompt("orquestador", PROMPTS_DIR, overrides) or (
        "Eres el cerebro operativo del Consejo. Puedes usar las herramientas "
        "declaradas. Si necesitas una, llama a la herramienta con argumentos "
        "válidos y espera su resultado; después continúa hasta responder al usuario."
    )
    if not request_data.get("tools") and not request_data.get("functions"):
        prompt += (" Si el harness representa herramientas con etiquetas de texto "
                   "como [tool:terminal] o [tool:skill_view], interpreta esa "
                   "transcripción y continúa usando el mismo formato cuando proceda.")
    tools = request_data.get("tools")
    if not tools and request_data.get("functions"):
        tools = [{"type": "function", "function": fn} for fn in request_data["functions"]]
    # No enviar tools=[]: algunos servidores OpenAI-compatibles lo interpretan
    # como una especificación inválida. Si hay herramientas declaradas se
    # prueba primero el modo nativo y abajo existe un fallback textual.
    extra = {}
    if tools:
        extra["tools"] = tools
    for key in ("tool_choice", "parallel_tool_calls", "response_format"):
        if key in request_data:
            extra[key] = request_data[key]
    try:
        model_kwargs = {
            "max_tokens": int(request_data.get("max_tokens", 4096)),
            "temperature": float(request_data.get("temperature", 0.8)),
            "timeout": 600,
        }
        try:
            data, elapsed = llamar_modelo(
                get_role_port("orquestador"),
                _tool_turn_messages(request_data, prompt),
                extra_body=extra or None,
                **model_kwargs,
            )
        except urllib.error.HTTPError as exc:
            # Hay backends (incluidos algunos builds de llama.cpp) que
            # anuncian una API OpenAI pero no aceptan tools/function calling.
            # El proxy puede seguir funcionando porque convierte la salida
            # textual del modelo a tool_calls en el paso siguiente.
            if not tools or exc.code not in (400, 404, 422, 500, 501):
                raise
            print(f"[PROXY2] backend rechazó tools ({exc.code}); reintentando modo textual")
            fallback_extra = {
                key: value for key, value in extra.items()
                if key != "tools"
            }
            data, elapsed = llamar_modelo(
                get_role_port("orquestador"),
                _tool_turn_messages(request_data, prompt),
                extra_body=fallback_extra or None,
                **model_kwargs,
            )
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        # Algunos modelos no rellenan message.tool_calls aunque el template
        # haya generado llamadas literales. Adaptarlas aquí es lo que permite
        # que Hermes las ejecute en vez de mostrarlas como texto.
        existing_calls = message.get("tool_calls") or []
        raw_model_text = message.get("content") or message.get("reasoning_content") or ""
        parsed_calls, cleaned_content = _parse_text_tool_calls(
            raw_model_text, _tool_names_from_request(request_data)
        )
        if parsed_calls:
            normalized_calls = _make_structured_tool_calls(parsed_calls, existing_calls)
            message = dict(message)
            message["content"] = cleaned_content or None
            message["tool_calls"] = normalized_calls
            choice = dict(choice)
            choice["finish_reason"] = "tool_calls"
        response = {
            "id": data.get("id", "consejo2-tool-turn"),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": data.get("model", "consejo-sabios2-tools"),
            "choices": [{"index": 0, "message": message,
                         "finish_reason": choice.get("finish_reason", "stop")}],
            "usage": data.get("usage", {}),
            "consejo": {"tool_mode": True, "configuration": configuration,
                        "elapsed": round(elapsed, 2)},
        }
        if request_data.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            send_cors(self); self.end_headers()
            delta = {k: message[k] for k in ("role", "content", "tool_calls") if k in message}
            chunk = {"id": response["id"], "object": "chat.completion.chunk",
                     "created": response["created"], "model": response["model"],
                     "choices": [{"index": 0, "delta": delta,
                                  "finish_reason": choice.get("finish_reason")}]} 
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
        else:
            send_json(self, 200, response)
    except Exception as exc:
        send_json(self, 502, {"error": {"message": str(exc), "type": "tool_turn_error"}})

# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    server = ThreadingHTTPServer(("0.0.0.0", PUERTO_PROXY), ProxyHandler)
    prompts_server = ThreadingHTTPServer(("0.0.0.0", PUERTO_PROMPTS), PromptsHandler)
    n_prompts = len(load_prompts(PROMPTS_DIR))
    cerebro = get_cerebro_port()
    print(f"[PROXY2] Iniciando proxy sabios2 en puerto {PUERTO_PROXY}...")
    print(f"[PROMPTS2] API prompts en puerto {PUERTO_PROMPTS}...")
    print(f"[PROXY2] System prompts cargados: {n_prompts} | num_sabios={get_default_num_sabios()} | cerebro={cerebro} ({'8181 producción' if cerebro==8181 else '8205 dedicado'})")
    print(f"[PROXY2] Puertos sabios: {[sabio_port(i) for i in range(get_default_num_sabios())]}, cerebro={cerebro}")
    print("[PROXY2] Ctrl+C para detener")
    def shutdown(sig, frame):
        print("\n[PROXY2] Cerrando..."); server.shutdown(); prompts_server.shutdown(); sys.exit(0)
    signal.signal(signal.SIGINT, shutdown); signal.signal(signal.SIGTERM, shutdown)
    import threading
    t1 = threading.Thread(target=server.serve_forever, daemon=True)
    t2 = threading.Thread(target=prompts_server.serve_forever, daemon=True)
    t1.start(); t2.start()
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt: shutdown(None, None)

if __name__ == "__main__":
    main()
