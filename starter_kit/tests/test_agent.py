import unittest
from unittest.mock import patch

try:
    from starter_kit.loomq import agent as agent_module
except ImportError:
    from loomq import agent as agent_module


GHZ = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[3];
creg c[3];
h q[0];
cx q[0], q[1];
cx q[1], q[2];
measure q -> c;
"""


def completion(text):
    return {"choices": [{"message": {"content": text}}]}


def execution_result():
    return {
        "backend": "originq_cpu_simulator",
        "job_id": "originq-local-test",
        "shots": 512,
        "counts": {"000": 259, "111": 253},
        "bit_order": "little",
        "timestamp": "2026-08-05T00:00:00Z",
        "meta": {"transpiled_gates": 3, "depth": 3},
    }


class AgentTests(unittest.TestCase):
    @patch.object(agent_module, "execute")
    @patch.object(agent_module, "chat_completion")
    def test_generates_and_executes_a_circuit_before_returning_it(self, chat, execute):
        chat.return_value = completion("这会得到全部为 0 或全部为 1。\n```qasm\n" + GHZ + "\n```")
        execute.return_value = execution_result()

        answer = agent_module.agent_chat("生成一个 3 比特 GHZ 态并全部测量")

        self.assertIn("```qasm\nOPENQASM 2.0;", answer)
        self.assertIn("已用 originq_cpu_simulator 做过 512 次试跑", answer)
        self.assertEqual(execute.call_count, 1)
        circuit, target, shots = execute.call_args.args
        self.assertEqual(circuit.qubit_count, 3)
        self.assertEqual((target, shots), ("originq", 512))

    @patch.object(agent_module, "execute")
    @patch.object(agent_module, "chat_completion")
    def test_retries_with_the_deterministic_parser_error(self, chat, execute):
        invalid = GHZ.replace("h q[0];", "u3(1, 2, 3) q[0];")
        chat.side_effect = [
            completion("```qasm\n" + invalid + "\n```"),
            completion("```qasm\n" + GHZ + "\n```"),
        ]
        execute.return_value = execution_result()

        answer = agent_module.agent_chat("修好这个 GHZ 电路")

        self.assertIn("OPENQASM 2.0;", answer)
        self.assertEqual(chat.call_count, 2)
        retry_messages = chat.call_args.args[0]
        self.assertIn("gate is outside the 12-gate whitelist", retry_messages[-1]["content"])

    @patch.object(agent_module, "execute")
    @patch.object(agent_module, "chat_completion")
    def test_returns_only_canonical_backend_recommendations(self, chat, execute):
        chat.return_value = completion(
            "推荐 braket_local_simulator：它免费、本地运行，而且不用排队。"
        )

        answer = agent_module.agent_chat("15 比特、免费、不要排队，选哪个？")

        self.assertIn("braket_local_simulator", answer)
        execute.assert_not_called()
        system_message = chat.call_args.args[0][0]["content"]
        self.assertIn('"max_qubits":25', system_message)

    @patch.object(agent_module, "chat_completion")
    def test_retries_when_the_model_lists_multiple_backends(self, chat):
        chat.side_effect = [
            completion("可以用 braket_local_simulator 或 originq_local_simulator。"),
            completion("推荐 braket_local_simulator，因为它免费且不用排队。"),
        ]

        answer = agent_module.agent_chat("15 比特、免费、不要排队，选哪个？")

        self.assertIn("braket_local_simulator", answer)
        self.assertNotIn("originq_local_simulator", answer)
        self.assertEqual(chat.call_count, 2)
        self.assertIn("恰好包含一个官方后端 id", chat.call_args.args[0][-1]["content"])

    @patch.object(agent_module, "chat_completion")
    def test_allows_an_explicit_no_matching_backend_answer(self, chat):
        chat.return_value = completion(
            "LOOMQ_BACKEND_NO_MATCH：没有后端能同时满足这些约束。"
        )
        self.assertIn(
            "LOOMQ_BACKEND_NO_MATCH",
            agent_module.agent_chat("要免费、无排队地运行 100 比特电路"),
        )

    @patch.object(agent_module, "chat_completion")
    def test_rejects_three_unverifiable_model_answers(self, chat):
        chat.return_value = completion("我建议使用不存在的 fast_quantum_machine。")
        with self.assertRaisesRegex(RuntimeError, "tried three times"):
            agent_module.agent_chat("帮我选后端")
        self.assertEqual(chat.call_count, 3)

    def test_rejects_an_empty_user_prompt(self):
        with self.assertRaisesRegex(ValueError, "non-empty"):
            agent_module.agent_chat("  ")


if __name__ == "__main__":
    unittest.main()
