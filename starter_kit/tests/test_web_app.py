import os
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from starter_kit import web_app
except ImportError:
    import web_app


QASM = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0], q[1];
measure q -> c;
"""


class WebApiTests(unittest.TestCase):
    @patch.object(web_app.adapter, "agent_chat")
    def test_chat_returns_machine_readable_qasm_alongside_friendly_text(self, chat):
        chat.return_value = "给你一枚会同步揭晓的双硬币。\n```qasm\n" + QASM + "\n```"
        result = web_app._chat({"prompt": "做一个贝尔态"})
        self.assertEqual(result["qasm"], QASM.strip())
        self.assertIn("双硬币", result["response"])

    @patch.object(web_app.adapter, "run")
    def test_run_forwards_a_bounded_request_to_l1(self, run):
        run.return_value = {
            "backend": "braket_local_simulator",
            "shots": 100,
            "counts": {"00": 50, "11": 50},
        }
        result = web_app._run({"qasm": QASM, "target": "braket", "shots": 100})
        self.assertEqual(result["counts"], {"00": 50, "11": 50})
        self.assertEqual(result["kind"], "noiseless_simulator")
        self.assertAlmostEqual(result["ideal"]["00"], 0.5)
        self.assertAlmostEqual(result["ideal"]["11"], 0.5)
        run.assert_called_once_with(QASM, "braket", 100)

    def test_run_rejects_unbounded_shots(self):
        with self.assertRaisesRegex(ValueError, "1 到 100000"):
            web_app._run({"qasm": QASM, "target": "spinq", "shots": 100001})

    def test_chat_requires_a_human_intent(self):
        with self.assertRaisesRegex(ValueError, "一句话"):
            web_app._chat({"prompt": ""})

    def test_local_env_file_fills_missing_variables_without_overriding(self):
        env_file = Path(self.id().replace(".", "_") + ".env")
        env_file.write_text("LOOMQ_LLM_MODEL=from-file\nLOOMQ_LLM_API_KEY=from-file\n", encoding="utf-8")
        self.addCleanup(env_file.unlink)
        with patch.dict(os.environ, {"LOOMQ_LLM_MODEL": "already-set"}, clear=False):
            os.environ.pop("LOOMQ_LLM_API_KEY", None)
            web_app.load_optional_env_file(env_file)
            self.assertEqual(os.environ["LOOMQ_LLM_MODEL"], "already-set")
            self.assertEqual(os.environ["LOOMQ_LLM_API_KEY"], "from-file")
            os.environ.pop("LOOMQ_LLM_API_KEY", None)

    def test_guided_h_lesson_runs_directly_on_the_local_simulator(self):
        web_dir = Path(web_app.__file__).resolve().parent / "web"
        page = (web_dir / "index.html").read_text(encoding="utf-8")
        script = (web_dir / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="guided-lab"', page)
        self.assertIn('id="lesson-quiz"', page)
        self.assertIn("gateCount: 0", script)
        self.assertIn("gateCount: 1", script)
        self.assertIn("gateCount: 2", script)
        self.assertIn("qasm: lessonQasm(step.gateCount)", script)
        self.assertIn("target: 'originq'", script)
        self.assertIn("shots: LESSON_SHOTS", script)
        self.assertIn("本次采样", page)
        self.assertIn("理想分布", page)
        self.assertIn("renderCompareChart", script)
        self.assertIn("formatRunMeta", script)


if __name__ == "__main__":
    unittest.main()
