import unittest
from datetime import datetime
from unittest.mock import patch

try:
    from starter_kit.adapter import run
    from starter_kit.loomq import BackendExecutionError
    from starter_kit.loomq import execution as execution_module
    from starter_kit.loomq.sdk_worker import (
        _braket_execution_source,
        _normalize_braket_counts,
        _normalize_spinq_counts,
    )
except ImportError:
    from adapter import run
    from loomq import BackendExecutionError
    from loomq import execution as execution_module
    from loomq.sdk_worker import (
        _braket_execution_source,
        _normalize_braket_counts,
        _normalize_spinq_counts,
    )


BELL = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0], q[1];
measure q -> c;
"""


class ExecutionContractTests(unittest.TestCase):
    @patch.object(execution_module, "_invoke_worker")
    def test_builds_the_unified_result_schema(self, worker):
        worker.return_value = {
            "backend": "originq_cpu_simulator",
            "counts": {"0": 61, "11": 67},
            "meta": {"sdk": "pyqpanda"},
        }

        result = run(BELL, "originq", 128)

        self.assertEqual(result["backend"], "originq_cpu_simulator")
        self.assertTrue(result["job_id"].startswith("originq-local-"))
        self.assertEqual(result["shots"], 128)
        self.assertEqual(result["counts"], {"00": 61, "11": 67})
        self.assertEqual(result["bit_order"], "little")
        self.assertEqual(result["meta"]["transpiled_gates"], 2)
        self.assertEqual(result["meta"]["depth"], 2)
        self.assertNotIn("is_mock", result["meta"])
        datetime.fromisoformat(result["timestamp"].replace("Z", "+00:00"))
        source = worker.call_args.args[1]
        self.assertTrue(source.startswith("OPENQASM 2.0;"))

    def test_rejects_invalid_shot_values_before_starting_a_worker(self):
        for value in (0, -1, 1.5, True):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "shots must be a positive integer"
            ):
                run(BELL, "originq", value)

    def test_requires_a_measurement(self):
        source = """OPENQASM 2.0;
        include "qelib1.inc";
        qreg q[1];
        creg c[1];
        h q[0];
        """
        with self.assertRaisesRegex(ValueError, "at least one measurement"):
            run(source, "originq", 10)

    @patch.object(execution_module, "_invoke_worker")
    def test_rejects_invalid_worker_counts(self, worker):
        worker.return_value = {
            "backend": "originq_cpu_simulator",
            "counts": {"00": 4, "11": 5},
        }
        with self.assertRaisesRegex(
            BackendExecutionError, "counts total does not equal"
        ):
            run(BELL, "originq", 10)

    def test_rejects_unknown_target(self):
        with self.assertRaisesRegex(ValueError, "unsupported target"):
            run(BELL, "not-a-backend", 10)


class BackendNormalizationTests(unittest.TestCase):
    def test_braket_execution_uses_local_simulator_gate_aliases(self):
        source = """OPENQASM 3.0;
include "stdgates.inc";
sdg q[0];
tdg q[0];
cp(0.5) q[0], q[1];
ccx q[0], q[1], q[2];
"""
        execution_source = _braket_execution_source(source)
        self.assertNotIn("stdgates.inc", execution_source)
        self.assertIn("si q[0];", execution_source)
        self.assertIn("ti q[0];", execution_source)
        self.assertIn("cphaseshift(0.5) q[0], q[1];", execution_source)
        self.assertIn("ccnot q[0], q[1], q[2];", execution_source)

    def test_spinq_qubit_order_is_mapped_to_little_endian_classic_bits(self):
        counts = _normalize_spinq_counts(
            {"10": 7, "01": 5},
            measurements=[(0, 0), (1, 1)],
            qubit_count=2,
            clbit_count=2,
            shots=12,
        )
        self.assertEqual(counts, {"01": 7, "10": 5})

    def test_spinq_respects_non_identity_measurement_mapping(self):
        counts = _normalize_spinq_counts(
            {"10": 9},
            measurements=[(0, 1), (1, 0)],
            qubit_count=2,
            clbit_count=2,
            shots=9,
        )
        self.assertEqual(counts, {"10": 9})

    def test_braket_qubit_order_is_mapped_to_little_endian_classic_bits(self):
        counts = _normalize_braket_counts(
            {"10": 7, "01": 5},
            measured_qubits=[0, 1],
            measurements=[(0, 0), (1, 1)],
            clbit_count=2,
            shots=12,
        )
        self.assertEqual(counts, {"01": 7, "10": 5})

    def test_braket_respects_non_identity_measurement_mapping(self):
        counts = _normalize_braket_counts(
            {"10": 9},
            measured_qubits=[0, 1],
            measurements=[(0, 1), (1, 0)],
            clbit_count=2,
            shots=9,
        )
        self.assertEqual(counts, {"10": 9})


if __name__ == "__main__":
    unittest.main()
