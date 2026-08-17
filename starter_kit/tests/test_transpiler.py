import math
import unittest

try:
    from starter_kit.adapter import transpile
    from starter_kit.loomq import (
        GateOperation,
        Measurement,
        QASMParseError,
        parse_qasm2,
    )
except ImportError:
    from adapter import transpile
    from loomq import GateOperation, Measurement, QASMParseError, parse_qasm2


ALL_GATES = """OPENQASM 2.0;
include "qelib1.inc";
qreg left[2];
qreg right[1];
creg out[3];
h left[0];
x left[1];
s right[0];
sdg left[0];
t left[1];
tdg right[0];
rz(pi/2) left[0];
ry(-pi/4) left[1];
cx left[0], left[1];
cu1(pi/8) left[1], right[0];
swap left[0], right[0];
ccx left[0], left[1], right[0];
measure left[0] -> out[0];
measure left[1] -> out[1];
measure right[0] -> out[2];
"""


class CircuitParserTests(unittest.TestCase):
    def test_parses_every_whitelisted_gate_and_flattens_registers(self):
        circuit = parse_qasm2(ALL_GATES)

        self.assertEqual(circuit.qubit_count, 3)
        self.assertEqual(circuit.clbit_count, 3)
        self.assertEqual(circuit.gate_count, 12)
        gates = [item for item in circuit.instructions if isinstance(item, GateOperation)]
        self.assertEqual(
            [gate.name for gate in gates],
            ["h", "x", "s", "sdg", "t", "tdg", "rz", "ry", "cx", "cu1", "swap", "ccx"],
        )
        self.assertAlmostEqual(gates[6].angle, math.pi / 2)
        self.assertAlmostEqual(gates[7].angle, -math.pi / 4)
        self.assertEqual(gates[-1].qubits, (0, 1, 2))
        measurements = [
            item for item in circuit.instructions if isinstance(item, Measurement)
        ]
        self.assertEqual(
            [(item.qubit, item.clbit) for item in measurements],
            [(0, 0), (1, 1), (2, 2)],
        )

    def test_expands_whole_register_measurement_and_ignores_comments(self):
        source = """/* heading */ OPENQASM 2.0;
        include "qelib1.inc";
        qreg q[2]; // two quantum wires
        creg c[2];
        h q[0];
        measure q -> c;
        """
        circuit = parse_qasm2(source)
        self.assertEqual(circuit.instructions[-2:], (Measurement(0, 0), Measurement(1, 1)))

    def test_broadcasts_whole_register_gates(self):
        source = """OPENQASM 2.0;
        include "qelib1.inc";
        qreg q[2];
        qreg r[2];
        creg c[2];
        h q;
        cx q, r;
        rz(pi/2) r;
        measure q -> c;
        """
        circuit = parse_qasm2(source)
        gates = [item for item in circuit.instructions if isinstance(item, GateOperation)]
        self.assertEqual(
            [(gate.name, gate.qubits, gate.angle is not None) for gate in gates],
            [
                ("h", (0,), False),
                ("h", (1,), False),
                ("cx", (0, 2), False),
                ("cx", (1, 3), False),
                ("rz", (2,), True),
                ("rz", (3,), True),
            ],
        )

    def test_rejects_mismatched_broadcast_registers(self):
        source = """OPENQASM 2.0;
        include "qelib1.inc";
        qreg q[2];
        qreg r[3];
        cx q, r;
        """
        with self.assertRaisesRegex(QASMParseError, "equal size"):
            parse_qasm2(source)

    def test_computes_logical_depth(self):
        source = """OPENQASM 2.0;
        include "qelib1.inc";
        qreg q[3]; creg c[3];
        h q[0]; x q[2]; cx q[0], q[1];
        measure q -> c;
        """
        self.assertEqual(parse_qasm2(source).depth, 2)

    def test_rejects_unsupported_or_unsafe_input_with_clear_errors(self):
        valid_prefix = 'OPENQASM 2.0; include "qelib1.inc"; qreg q[3]; creg c[3]; '
        cases = {
            "unknown gate": valid_prefix + "z q[0];",
            "out of range": valid_prefix + "h q[3];",
            "wrong arity": valid_prefix + "cx q[0];",
            "same qubit twice": valid_prefix + "swap q[1], q[1];",
            "unsafe angle": valid_prefix + "rz(__import__('os')) q[0];",
            "missing include": "OPENQASM 2.0; qreg q[1]; h q[0];",
            "wrong version": 'OPENQASM 3.0; include "qelib1.inc"; qreg q[1];',
        }
        for label, source in cases.items():
            with self.subTest(label=label), self.assertRaises(QASMParseError):
                parse_qasm2(source)


class TargetRendererTests(unittest.TestCase):
    def test_spinq_is_normalized_executable_qasm2(self):
        output = transpile(ALL_GATES, "spinq")
        reparsed = parse_qasm2(output)
        self.assertEqual(reparsed, parse_qasm2(ALL_GATES))
        self.assertIn("cu1(0.39269908169872414) q[1], q[2];", output)

    def test_braket_is_qasm3_with_standard_gate_names(self):
        output = transpile(ALL_GATES, "braket")
        self.assertTrue(output.startswith("OPENQASM 3.0;\n"))
        self.assertIn('include "stdgates.inc";', output)
        self.assertIn("cnot q[0], q[1];", output)
        self.assertIn("cp(0.39269908169872414) q[1], q[2];", output)
        self.assertIn("c[2] = measure q[2];", output)

    def test_originq_uses_contract_gate_names(self):
        output = transpile(ALL_GATES, "originq")
        self.assertTrue(output.startswith("QINIT 3\nCREG 3\n"))
        self.assertIn("SDAG q[0]", output)
        self.assertIn("TDAG q[2]", output)
        self.assertIn("CNOT q[0], q[1]", output)
        self.assertIn("CU1(0.39269908169872414) q[1], q[2]", output)
        self.assertIn("TOFFOLI q[0], q[1], q[2]", output)
        self.assertIn("MEASURE q[2], c[2]", output)

    def test_rejects_unknown_target(self):
        with self.assertRaisesRegex(ValueError, "unsupported target"):
            transpile(ALL_GATES, "not-a-backend")


if __name__ == "__main__":
    unittest.main()
