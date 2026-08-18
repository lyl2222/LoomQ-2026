import unittest

try:
    from starter_kit.loomq.interpret import interpret_run
except ImportError:
    from loomq.interpret import interpret_run


def _qasm(body: str, qubits: int = 1) -> str:
    return f"""OPENQASM 2.0;
include "qelib1.inc";
qreg q[{qubits}];
creg c[{qubits}];
{body}
measure q -> c;
"""


class InterpreterTests(unittest.TestCase):
    def test_explains_the_empty_control_experiment(self):
        data = interpret_run(_qasm(""), "originq", 1024, {"counts": {"0": 1024}, "ideal": {"0": 1.0}})
        self.assertIn("对照组", data["title"])
        labels = [item["label"] for item in data["parameters"]]
        self.assertEqual(labels, ["运行地点", "重复开奖", "量子硬币", "记下的位数", "中间操作"])
        self.assertIn("1,024 次", data["parameters"][1]["value"])
        self.assertIn("本源", data["parameters"][0]["value"])
        self.assertTrue(data["steps"])

    def test_recognizes_one_and_two_hadamards(self):
        once = interpret_run(_qasm("h q[0];"), "spinq", 512)
        twice = interpret_run(_qasm("h q[0];\nh q[0];"), "braket", 1024)
        self.assertIn("一次 H", once["title"])
        self.assertIn("两次 H", twice["title"])
        self.assertIn("Taurus", once["parameters"][0]["value"])

    def test_recognizes_bell_and_ghz(self):
        bell = interpret_run(_qasm("h q[0];\ncx q[0], q[1];", qubits=2), "originq", 1000)
        ghz = interpret_run(
            _qasm("h q[0];\ncx q[0], q[1];\ncx q[1], q[2];", qubits=3),
            "originq",
            8192,
        )
        self.assertIn("贝尔", bell["title"])
        self.assertIn("3 枚", ghz["title"])
        self.assertIn("CNOT", bell["parameters"][-1]["value"])

    def test_invalid_qasm_returns_empty_overlay(self):
        self.assertEqual(interpret_run("not qasm", "originq", 10), {})


if __name__ == "__main__":
    unittest.main()
