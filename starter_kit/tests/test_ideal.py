import unittest

try:
    from starter_kit.loomq.circuit import parse_qasm2
    from starter_kit.loomq.ideal import ideal_probabilities
except ImportError:
    from loomq.circuit import parse_qasm2
    from loomq.ideal import ideal_probabilities


def _qasm(body: str, qubits: int = 1, clbits: int | None = None) -> str:
    width = clbits if clbits is not None else qubits
    return f"""OPENQASM 2.0;
include "qelib1.inc";
qreg q[{qubits}];
creg c[{width}];
{body}
measure q -> c;
"""


class IdealProbabilityTests(unittest.TestCase):
    def test_empty_circuit_is_a_sure_zero(self):
        probs = ideal_probabilities(parse_qasm2(_qasm("")))
        self.assertAlmostEqual(probs["0"], 1.0)
        self.assertEqual(set(probs), {"0"})

    def test_hadamard_is_a_fair_coin(self):
        probs = ideal_probabilities(parse_qasm2(_qasm("h q[0];")))
        self.assertAlmostEqual(probs["0"], 0.5)
        self.assertAlmostEqual(probs["1"], 0.5)

    def test_two_hadamards_interfere_back_to_zero(self):
        probs = ideal_probabilities(parse_qasm2(_qasm("h q[0];\nh q[0];")))
        self.assertAlmostEqual(probs["0"], 1.0)
        self.assertNotIn("1", probs)

    def test_bell_pair_peaks_on_00_and_11(self):
        probs = ideal_probabilities(
            parse_qasm2(_qasm("h q[0];\ncx q[0], q[1];", qubits=2))
        )
        self.assertAlmostEqual(probs["00"], 0.5)
        self.assertAlmostEqual(probs["11"], 0.5)
        self.assertNotIn("01", probs)
        self.assertNotIn("10", probs)

    def test_swap_moves_a_prepared_one(self):
        probs = ideal_probabilities(
            parse_qasm2(_qasm("x q[0];\nswap q[0], q[1];", qubits=2))
        )
        self.assertAlmostEqual(probs["10"], 1.0)

    def test_toffoli_flips_the_target_when_both_controls_are_one(self):
        probs = ideal_probabilities(
            parse_qasm2(_qasm("x q[0];\nx q[1];\nccx q[0], q[1], q[2];", qubits=3))
        )
        self.assertAlmostEqual(probs["111"], 1.0)

    def test_ry_pi_flips_zero_to_one(self):
        probs = ideal_probabilities(parse_qasm2(_qasm("ry(pi) q[0];")))
        self.assertAlmostEqual(probs["1"], 1.0)


if __name__ == "__main__":
    unittest.main()
