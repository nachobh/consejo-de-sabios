#!/usr/bin/env python3
"""Pruebas rápidas sin levantar modelos ni GPU."""
import json
import os
import shutil
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
sys.path.insert(0, ROOT)
from proxy.configurations import (load_runtime, load_configurations,
                                  prompt_overrides, save_profile,
                                  set_active, update_runtime)
from proxy.consejo_proxy import (_content_to_text, _conversation_to_text,
                                  _has_tool_protocol, _parse_text_tool_calls)
from proxy.pipeline import agregar_usage_total


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sabios2-test-")
        shutil.copy(os.path.join(ROOT, "config.json"), self.tmp)
        shutil.copy(os.path.join(ROOT, "configurations.json"), self.tmp)
        # El test debe ser independiente de la configuración activa del host.
        with open(os.path.join(self.tmp, "config.json"), encoding="utf-8") as fh:
            config = json.load(fh)
        config["prompt_configuration"] = "default"
        with open(os.path.join(self.tmp, "config.json"), "w", encoding="utf-8") as fh:
            json.dump(config, fh)
        self.prompts = os.path.join(self.tmp, "prompts")
        shutil.copytree(os.path.join(ROOT, "prompts"), self.prompts)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_default_prompts_and_runtime(self):
        profiles, active = load_configurations(self.tmp, self.prompts)
        self.assertEqual(active, "default")
        prompts = prompt_overrides(self.tmp, self.prompts, "default")
        with open(os.path.join(self.prompts, "sabi0.txt"), encoding="utf-8") as fh:
            self.assertEqual(prompts["sabi0"], fh.read().strip())
        runtime = load_runtime(self.tmp)
        self.assertEqual(runtime["services"]["cerebro"]["port"], 8205)

    def test_runtime_and_profile_are_atomic_and_reloadable(self):
        update_runtime(self.tmp, {"num_sabios": 6, "services": {"arbitro": {"port": 8210, "context": 65536}}})
        self.assertEqual(load_runtime(self.tmp)["services"]["arbitro"]["port"], 8210)
        save_profile(self.tmp, self.prompts, "test_profile", {"prompts": {"sabi0": "sabi1"}})
        set_active(self.tmp, self.prompts, "test_profile")
        with open(os.path.join(self.prompts, "sabi1.txt"), encoding="utf-8") as fh:
            self.assertEqual(prompt_overrides(self.tmp, self.prompts)["sabi0"], fh.read().strip())

    def test_openai_tool_messages_are_preserved(self):
        request = {
            "tools": [{"type": "function", "function": {"name": "terminal"}}],
            "messages": [
                {"role": "user", "content": "lee la especificación"},
                {"role": "assistant", "tool_calls": [{"id": "x", "type": "function"}]},
                {"role": "tool", "tool_call_id": "x", "content": "resultado del terminal"},
            ],
        }
        self.assertTrue(_has_tool_protocol(request))
        self.assertTrue(_has_tool_protocol({"messages": [{"role": "user", "content": "[tool:terminal] command: ls"}]}))
        text = _conversation_to_text(request["messages"])
        self.assertIn("resultado del terminal", text)
        self.assertEqual(_content_to_text([{"type": "text", "text": "hola"}]), "hola")

    def test_textual_hermes_calls_become_structured_arguments(self):
        text = """Voy a revisar el sistema.
skill_view(name='scraper-40all')
terminal(command='ls -la /opt/spread-quant/ 2>/dev/null; echo \"---\"')
<tool_call><parameter name=\"command\">date \"+%Y-%m-%d\"</parameter><parameter name=\"name\">terminal</parameter></invoke>"""
        calls, cleaned = _parse_text_tool_calls(text)
        self.assertEqual([name for name, _ in calls], ["skill_view", "terminal", "terminal"])
        self.assertEqual(calls[1][1]["command"], "ls -la /opt/spread-quant/ 2>/dev/null; echo \"---\"")
        self.assertEqual(calls[0][1]["name"], "scraper-40all")
        self.assertNotIn("<tool_call>", cleaned)
        self.assertNotIn("skill_view(", cleaned)

    def test_hermes_todo_function_xml_becomes_structured_call(self):
        text = """<tool_call>
<function=todo>
<parameter name="merge">true</parameter>
<parameter name="todos">[{\"status\": \"completed\", \"id\": \"4\"}, {\"content\": \"Implementar servicio\", \"id\": \"4b\", \"status\": \"in_progress\"}]</parameter>
</function>
</tool_call>"""
        calls, cleaned = _parse_text_tool_calls(text, {"terminal"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "todo")
        self.assertEqual(calls[0][1]["merge"], "true")
        self.assertIn('"4b"', calls[0][1]["todos"])
        self.assertNotIn("<tool_call>", cleaned)

    def test_pipeline_usage_accepts_internal_phase_field_names(self):
        result = {
            "sabios": {
                "sabi0": {"tokens_prompt": 10, "tokens_gen": 20, "thinking_tokens": 3},
            },
            "plan": {"texto": "plan", "tokens_prompt": 30, "tokens_gen": 40},
        }
        self.assertEqual(agregar_usage_total(result), {
            "prompt_tokens": 40,
            "completion_tokens": 60,
            "thinking_tokens": 3,
            "total_tokens": 100,
        })


if __name__ == "__main__":
    unittest.main()
