import itertools
import os
import unittest

try:
    from starter_kit.adapter import run
    from starter_kit.evaluator import (
        calculate_hellinger_fidelity,
        validate_schema,
    )
except ImportError:
    from adapter import run
    from evaluator import (
        calculate_hellinger_fidelity,
        validate_schema,
    )


ALL_WHITELIST_GATES = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[4];
creg c[4];
h q[0];
x q[1];
s q[0];
sdg q[0];
t q[1];
tdg q[1];
rz(pi/7) q[2];
ry(pi/5) q[3];
cx q[0], q[1];
cu1(pi/6) q[1], q[2];
swap q[2], q[3];
ccx q[0], q[1], q[2];
measure q -> c;
"""

GHZ5 = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[5];
creg c[5];
h q[0];
cx q[0], q[1];
cx q[1], q[2];
cx q[2], q[3];
cx q[3], q[4];
measure q -> c;
"""

QFT4 = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[4];
creg c[4];
h q[0];
cu1(pi/2) q[1], q[0];
h q[1];
cu1(pi/4) q[2], q[0];
cu1(pi/2) q[2], q[1];
h q[2];
cu1(pi/8) q[3], q[0];
cu1(pi/4) q[3], q[1];
cu1(pi/2) q[3], q[2];
h q[3];
swap q[0], q[3];
swap q[1], q[2];
measure q -> c;
"""


@unittest.skipUnless(
    os.environ.get("LOOMQ_RUN_SDK_INTEGRATION") == "1",
    "set LOOMQ_RUN_SDK_INTEGRATION=1 to run installed vendor SDKs",
)
class VendorSdkIntegrationTests(unittest.TestCase):
    def test_all_whitelisted_gates_agree_across_three_local_backends(self):
        results = {}
        for target in ("spinq", "originq", "braket"):
            with self.subTest(target=target):
                payload = run(ALL_WHITELIST_GATES, target, 8192)
                valid, reason = validate_schema(payload)
                self.assertTrue(valid, reason)
                self.assertEqual(payload["shots"], 8192)
                self.assertEqual(payload["meta"]["transpiled_gates"], 12)
                results[target] = {
                    state: count / payload["shots"]
                    for state, count in payload["counts"].items()
                }

        for left, right in itertools.combinations(results, 2):
            with self.subTest(backends=(left, right)):
                fidelity = calculate_hellinger_fidelity(
                    results[left], results[right]
                )
                self.assertGreaterEqual(
                    fidelity,
                    0.97,
                    f"{left} and {right} disagree (fidelity={fidelity:.6f})",
                )

    def test_hidden_style_ghz5_and_qft4_meet_the_fidelity_threshold(self):
        cases = {
            "ghz5": (GHZ5, {"00000": 0.5, "11111": 0.5}),
            "qft4": (QFT4, {format(index, "04b"): 1 / 16 for index in range(16)}),
        }
        for name, (qasm, expected) in cases.items():
            for target in ("spinq", "originq", "braket"):
                with self.subTest(name=name, target=target):
                    payload = run(qasm, target, 8192)
                    valid, reason = validate_schema(payload)
                    self.assertTrue(valid, reason)
                    observed = {
                        state: count / payload["shots"]
                        for state, count in payload["counts"].items()
                    }
                    self.assertGreaterEqual(
                        calculate_hellinger_fidelity(observed, expected),
                        0.97,
                    )


if __name__ == "__main__":
    unittest.main()
