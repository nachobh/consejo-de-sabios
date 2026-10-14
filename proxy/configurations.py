#!/usr/bin/env python3
"""Configuraciones persistentes de asignación de prompts.

Una configuración guarda nombres de archivos, no copias del texto. Así se
pueden editar/reutilizar los prompts desde la web sin duplicar contenido.
"""
import json
import os
import tempfile
from contextlib import contextmanager

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


ROLES = [f"sabi{i}" for i in range(12)] + ["orquestador", "arbitro"]
LEGACY = {"sabi0": "sabio_tecnico", "sabi1": "sabio_estratega",
          "sabi2": "sabio_creativo", "sabi3": "sabio_abogado"}

DEFAULT_SERVICES = {
    "arbitro": {"model": "ornith-35b", "port": 8205, "context": 262144},
    "cerebro": {"model": "ornith-35b", "port": 8205, "context": 262144},
    "orquestador": {"model": "ornith-35b", "port": 8205, "context": 262144},
}


def _read(path, fallback):
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
        return value if isinstance(value, dict) else fallback
    except (OSError, ValueError, TypeError):
        return fallback


@contextmanager
def _locked(path):
    """Bloqueo de proceso para no truncar config durante dos escrituras."""
    lock_path = path + ".lock"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as lock:
        if fcntl:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _atomic_write(path, value):
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".sabios2-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_write_text(path, text):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    lock_path = path + ".lock"
    with _locked(lock_path):
        fd, tmp = tempfile.mkstemp(prefix=".prompt-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


def atomic_delete(path):
    lock_path = os.path.abspath(path) + ".lock"
    with _locked(lock_path):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def load_runtime(base_dir):
    """Carga runtime con defaults y normaliza servicios dinámicos sabi0..11."""
    config_path, _ = _paths(base_dir)
    cfg = _read(config_path, {})
    runtime = cfg.get("runtime", {})
    if not isinstance(runtime, dict):
        runtime = {}
    services = {k: dict(v) for k, v in DEFAULT_SERVICES.items()}
    configured = runtime.get("services", {})
    if isinstance(configured, dict):
        for role, values in configured.items():
            if isinstance(values, dict):
                services[role] = {**services.get(role, {}), **values}
    n = runtime.get("num_sabios", cfg.get("num_sabios", 4))
    try:
        n = max(1, min(12, int(n)))
    except (TypeError, ValueError):
        n = 4
    for i in range(12):
        role = f"sabi{i}"
        default_port = 8201 + i if i < 4 else 8207 + i - 4
        services.setdefault(role, {"model": "ornith-9b" if i % 2 == 0 else "qwen3.5-9b",
                                   "port": default_port, "context": 131072})
    return {"num_sabios": n, "services": services,
            "prompt_configuration": cfg.get("prompt_configuration", "default")}


def save_runtime(base_dir, runtime):
    config_path, _ = _paths(base_dir)
    if not isinstance(runtime, dict):
        raise ValueError("runtime inválido")
    try:
        n = max(1, min(12, int(runtime.get("num_sabios", 4))))
    except (TypeError, ValueError):
        raise ValueError("num_sabios debe estar entre 1 y 12")
    current = _read(config_path, {})
    current["num_sabios"] = n
    current["prompt_configuration"] = runtime.get("prompt_configuration", current.get("prompt_configuration", "default"))
    current["runtime"] = {"num_sabios": n, "services": runtime.get("services", {})}
    with _locked(config_path):
        _atomic_write(config_path, current)
    return load_runtime(base_dir)


def update_runtime(base_dir, changes):
    runtime = load_runtime(base_dir)
    if not isinstance(changes, dict):
        raise ValueError("cambios inválidos")
    if "num_sabios" in changes:
        runtime["num_sabios"] = changes["num_sabios"]
    for role, values in (changes.get("services", {}) or {}).items():
        if role not in runtime["services"] or not isinstance(values, dict):
            continue
        service = runtime["services"][role]
        for key in ("model", "model_path", "mmproj", "port", "context"):
            if key in values:
                service[key] = values[key]
    return save_runtime(base_dir, runtime)


def _paths(base_dir):
    base_dir = os.path.abspath(base_dir)
    return (os.path.join(base_dir, "config.json"),
            os.path.join(base_dir, "configurations.json"))


def _default_mapping(prompts_dir):
    mapping = {}
    for role in ROLES:
        candidate = role
        if not os.path.exists(os.path.join(prompts_dir, candidate + ".txt")):
            candidate = LEGACY.get(role, role)
        if os.path.exists(os.path.join(prompts_dir, candidate + ".txt")):
            mapping[role] = candidate
    return mapping


def load_configurations(base_dir, prompts_dir):
    config_path, profiles_path = _paths(base_dir)
    data = _read(profiles_path, {})
    profiles = data.get("profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
    default = profiles.get("default", {})
    if not isinstance(default, dict):
        default = {}
    default.setdefault("name", "Configuración por defecto")
    default.setdefault("description", "Prompts actuales de sabios2")
    default["prompts"] = {**_default_mapping(prompts_dir),
                           **(default.get("prompts", {}) or {})}
    profiles["default"] = default
    cfg = _read(config_path, {})
    active = cfg.get("prompt_configuration", "default")
    if active not in profiles:
        active = "default"
    return profiles, active


def active_name(base_dir, prompts_dir):
    return load_configurations(base_dir, prompts_dir)[1]


def prompt_overrides(base_dir, prompts_dir, name=None):
    profiles, active = load_configurations(base_dir, prompts_dir)
    name = name or active
    profile = profiles.get(name) or profiles["default"]
    result = {}
    for role, filename in (profile.get("prompts", {}) or {}).items():
        if role not in ROLES or not isinstance(filename, str):
            continue
        filename = os.path.basename(filename).removesuffix(".txt")
        path = os.path.join(prompts_dir, filename + ".txt")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                result[role] = fh.read().strip()
    return result


def save_profile(base_dir, prompts_dir, name, profile):
    if not isinstance(name, str) or not name or not name.replace("_", "").replace("-", "").isalnum():
        raise ValueError("Nombre de configuración inválido")
    if not isinstance(profile, dict):
        raise ValueError("Configuración inválida")
    prompts = profile.get("prompts", {})
    if not isinstance(prompts, dict):
        raise ValueError("prompts debe ser un objeto")
    clean = {}
    for role, filename in prompts.items():
        if role in ROLES and isinstance(filename, str) and filename:
            clean[role] = os.path.basename(filename).removesuffix(".txt")
    _, profiles_path = _paths(base_dir)
    profiles, _ = load_configurations(base_dir, prompts_dir)
    profiles[name] = {"name": profile.get("name", name),
                      "description": profile.get("description", ""),
                      "prompts": clean}
    with _locked(profiles_path):
        _atomic_write(profiles_path, {"profiles": profiles})


def set_active(base_dir, prompts_dir, name):
    profiles, _ = load_configurations(base_dir, prompts_dir)
    if name not in profiles:
        raise ValueError("Configuración inexistente")
    config_path, _ = _paths(base_dir)
    cfg = _read(config_path, {})
    cfg["prompt_configuration"] = name
    with _locked(config_path):
        _atomic_write(config_path, cfg)
