"""Small process boundary around one vendor SDK.

The parent supplies a JSON request over stdin and a private output-file path.
Writing the response to a file keeps SDK log messages on stdout from corrupting
the machine-readable result.
"""

from __future__ import annotations

import json
import importlib.util
import os
import sys
import tempfile
import types
from importlib import metadata as importlib_metadata
from numbers import Integral
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple


class SDKUnavailableError(RuntimeError):
    """The selected vendor SDK cannot be imported in this worker."""


def _binary_key(value: Any, width: int) -> str:
    if isinstance(value, bool):
        raise ValueError("boolean measurement key is invalid")
    if isinstance(value, Integral):
        if value < 0:
            raise ValueError("negative measurement key is invalid")
        key = format(int(value), "b")
    elif isinstance(value, str):
        compact = value.strip().replace(" ", "")
        if compact.startswith(("0x", "0X")):
            key = format(int(compact, 16), "b")
        elif compact.startswith(("0b", "0B")):
            key = compact[2:]
        elif compact and not (set(compact) - {"0", "1"}):
            key = compact
        elif compact.isdigit():
            key = format(int(compact, 10), "b")
        else:
            raise ValueError("measurement key is not binary or numeric")
    else:
        raise ValueError("measurement key has an unsupported type")
    if not key or set(key) - {"0", "1"} or len(key) > width:
        raise ValueError("measurement key does not fit the classic register")
    return key.zfill(width)


def _normalize_counts(raw_counts: Any, width: int, shots: int) -> Dict[str, int]:
    if not isinstance(raw_counts, Mapping) or not raw_counts:
        raise ValueError("SDK returned no measurement counts")
    normalized: Dict[str, int] = {}
    for raw_key, raw_count in raw_counts.items():
        if (
            not isinstance(raw_count, Integral)
            or isinstance(raw_count, bool)
            or raw_count < 0
        ):
            raise ValueError("SDK returned an invalid measurement count")
        key = _binary_key(raw_key, width)
        normalized[key] = normalized.get(key, 0) + int(raw_count)
    if sum(normalized.values()) != shots:
        raise ValueError("SDK counts total does not equal requested shots")
    return dict(sorted(normalized.items()))


def _run_originq(source: str, shots: int, clbit_count: int) -> Dict[str, Any]:
    try:
        import pyqpanda as pq
    except (ImportError, OSError) as exc:
        raise SDKUnavailableError(
            "pyqpanda is unavailable; install the pinned OriginQ SDK layer"
        ) from exc

    machine = pq.CPUQVM()
    machine.init_qvm()
    try:
        program, _qubits, classical_bits = pq.convert_qasm_string_to_qprog(
            source, machine
        )
        raw_counts = machine.run_with_configuration(
            program, classical_bits, shots
        )
    finally:
        machine.finalize()
    version = getattr(pq, "__version__", None)
    metadata: Dict[str, Any] = {"sdk": "pyqpanda"}
    if isinstance(version, str):
        metadata["sdk_version"] = version
    return {
        "backend": "originq_cpu_simulator",
        "counts": _normalize_counts(raw_counts, clbit_count, shots),
        "meta": metadata,
    }


def _normalize_spinq_counts(
    raw_counts: Any,
    measurements: Sequence[Tuple[int, int]],
    qubit_count: int,
    clbit_count: int,
    shots: int,
) -> Dict[str, int]:
    """Map SpinQ's q0...qn strings to the required c[n-1]...c0 order."""

    if not isinstance(raw_counts, Mapping) or not raw_counts:
        raise ValueError("SpinQ returned no measurement counts")
    normalized: Dict[str, int] = {}
    for raw_key, raw_count in raw_counts.items():
        if not isinstance(raw_key, str):
            raise ValueError("SpinQ returned a non-string measurement key")
        compact = raw_key.replace(" ", "")
        if len(compact) != qubit_count or set(compact) - {"0", "1"}:
            raise ValueError("SpinQ measurement key does not match circuit qubits")
        if (
            not isinstance(raw_count, Integral)
            or isinstance(raw_count, bool)
            or raw_count < 0
        ):
            raise ValueError("SpinQ returned an invalid measurement count")
        classic_bits = ["0"] * clbit_count
        for qubit, clbit in measurements:
            classic_bits[clbit] = compact[qubit]
        key = "".join(reversed(classic_bits))
        normalized[key] = normalized.get(key, 0) + int(raw_count)
    if sum(normalized.values()) != shots:
        raise ValueError("SpinQ counts total does not equal requested shots")
    return dict(sorted(normalized.items()))


def _load_spinq_simulator() -> Tuple[Any, Any, Any]:
    """Load SpinQ's compiler/simulator without its unrelated ML facade.

    SpinQit 0.2.4 eagerly imports the optional Torch training interface from
    its package root.  The QASM compiler and BasicSimulator do not use Torch,
    so this narrow package bootstrap exposes the model symbols they officially
    consume while keeping the simulator layer small and reproducible.
    """

    try:
        spec = importlib.util.find_spec("spinqit")
    except (ImportError, AttributeError) as exc:
        raise SDKUnavailableError(
            "spinqit is unavailable; install the pinned SpinQ SDK layer"
        ) from exc
    locations = spec.submodule_search_locations if spec is not None else None
    if not locations:
        raise SDKUnavailableError(
            "spinqit is unavailable; install the pinned SpinQ SDK layer"
        )
    package_dir = next(iter(locations))
    package = types.ModuleType("spinqit")
    package.__path__ = [package_dir]
    package.__package__ = "spinqit"
    package.__file__ = os.path.join(package_dir, "__init__.py")
    sys.modules["spinqit"] = package
    try:
        import spinqit.model as model

        for name in dir(model):
            if not name.startswith("_"):
                setattr(package, name, getattr(model, name))
        from spinqit.compiler import get_compiler

        setattr(package, "get_compiler", get_compiler)
        from spinqit.backend.basic_simulator_backend import (
            BasicSimulatorBackend,
            BasicSimulatorConfig,
        )
    except (ImportError, OSError) as exc:
        raise SDKUnavailableError(
            "SpinQ compiler or BasicSimulator dependencies are unavailable"
        ) from exc
    return get_compiler, BasicSimulatorBackend, BasicSimulatorConfig


def _run_spinq(
    source: str,
    shots: int,
    qubit_count: int,
    clbit_count: int,
    measurements: Sequence[Tuple[int, int]],
) -> Dict[str, Any]:
    get_compiler, backend_type, config_type = _load_spinq_simulator()
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".qasm", encoding="utf-8", delete=False
    )
    try:
        with handle:
            handle.write(source)
        intermediate = get_compiler("qasm").compile(handle.name, 0)
        config = config_type()
        config.configure_shots(shots)
        result = backend_type().execute(intermediate, config)
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
    try:
        version = importlib_metadata.version("spinqit")
    except importlib_metadata.PackageNotFoundError:
        version = None
    metadata: Dict[str, Any] = {"sdk": "spinqit"}
    if version:
        metadata["sdk_version"] = version
    return {
        "backend": "spinq_basic_simulator",
        "counts": _normalize_spinq_counts(
            result.counts, measurements, qubit_count, clbit_count, shots
        ),
        "meta": metadata,
    }


def _normalize_braket_counts(
    raw_counts: Any,
    measured_qubits: Sequence[int],
    measurements: Sequence[Tuple[int, int]],
    clbit_count: int,
    shots: int,
) -> Dict[str, int]:
    """Map Braket's q0...qn strings to the required c[n-1]...c0 order."""

    if not isinstance(raw_counts, Mapping) or not raw_counts:
        raise ValueError("Braket returned no measurement counts")
    qubit_to_clbits: Dict[int, List[int]] = {}
    for qubit, clbit in measurements:
        qubit_to_clbits.setdefault(qubit, []).append(clbit)
    normalized: Dict[str, int] = {}
    for raw_key, raw_count in raw_counts.items():
        if not isinstance(raw_key, str):
            raise ValueError("Braket returned a non-string measurement key")
        compact = raw_key.replace(" ", "")
        if len(compact) != len(measured_qubits) or set(compact) - {"0", "1"}:
            raise ValueError("Braket measurement key does not match measured qubits")
        if (
            not isinstance(raw_count, Integral)
            or isinstance(raw_count, bool)
            or raw_count < 0
        ):
            raise ValueError("Braket returned an invalid measurement count")
        classic_bits = ["0"] * clbit_count
        for position, qubit in enumerate(measured_qubits):
            for clbit in qubit_to_clbits.get(int(qubit), []):
                classic_bits[clbit] = compact[position]
        key = "".join(reversed(classic_bits))
        normalized[key] = normalized.get(key, 0) + int(raw_count)
    if sum(normalized.values()) != shots:
        raise ValueError("Braket counts total does not equal requested shots")
    return dict(sorted(normalized.items()))


def _braket_execution_source(source: str) -> str:
    """Adapt contract-standard gate names to Braket LocalSimulator names.

    ``transpile(..., "braket")`` intentionally emits the OpenQASM 3 names
    required by the competition contract.  Braket's bundled standard-gate
    library uses its historical SDK names for four equivalent gates, so only
    the private execution copy is rewritten here.
    """

    replacements = {
        "sdg ": "si ",
        "tdg ": "ti ",
        "cp(": "cphaseshift(",
        "ccx ": "ccnot ",
    }
    lines: List[str] = []
    for line in source.splitlines():
        if line.strip() == 'include "stdgates.inc";':
            continue
        indentation = line[: len(line) - len(line.lstrip())]
        statement = line.lstrip()
        for standard_name, sdk_name in replacements.items():
            if statement.startswith(standard_name):
                statement = sdk_name + statement[len(standard_name) :]
                break
        lines.append(indentation + statement)
    return "\n".join(lines) + ("\n" if source.endswith("\n") else "")


def _run_braket(
    source: str,
    shots: int,
    clbit_count: int,
    measurements: Sequence[Tuple[int, int]],
) -> Dict[str, Any]:
    try:
        from braket.devices import LocalSimulator
        from braket.ir.openqasm import Program
    except (ImportError, OSError) as exc:
        raise SDKUnavailableError(
            "amazon-braket-sdk is unavailable; install the pinned Braket SDK layer"
        ) from exc

    execution_source = _braket_execution_source(source)
    task = LocalSimulator().run(Program(source=execution_source), shots=shots)
    result = task.result()
    counts = _normalize_braket_counts(
        result.measurement_counts,
        result.measured_qubits,
        measurements,
        clbit_count,
        shots,
    )
    try:
        version = importlib_metadata.version("amazon-braket-sdk")
    except importlib_metadata.PackageNotFoundError:
        version = None
    metadata: Dict[str, Any] = {"sdk": "amazon-braket-sdk"}
    if version:
        metadata["sdk_version"] = version
    response: Dict[str, Any] = {
        "backend": "braket_local_simulator",
        "counts": counts,
        "meta": metadata,
    }
    task_id = getattr(task, "id", None)
    if isinstance(task_id, str) and task_id:
        response["job_id"] = task_id
    return response


def _measurement_pairs(value: Any, clbit_count: int) -> List[Tuple[int, int]]:
    if not isinstance(value, list) or not value:
        raise ValueError("worker measurements must be a non-empty list")
    pairs: List[Tuple[int, int]] = []
    for pair in value:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or any(not isinstance(index, int) or isinstance(index, bool) for index in pair)
        ):
            raise ValueError("worker measurement must contain qubit and clbit indices")
        qubit, clbit = pair
        if qubit < 0 or clbit < 0 or clbit >= clbit_count:
            raise ValueError("worker measurement index is out of range")
        pairs.append((qubit, clbit))
    return pairs


def _execute(target: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    source = payload.get("source")
    shots = payload.get("shots")
    qubit_count = payload.get("qubit_count")
    clbit_count = payload.get("clbit_count")
    if not isinstance(source, str) or not source:
        raise ValueError("worker source must be a non-empty string")
    if not isinstance(shots, int) or isinstance(shots, bool) or shots <= 0:
        raise ValueError("worker shots must be a positive integer")
    if (
        not isinstance(qubit_count, int)
        or isinstance(qubit_count, bool)
        or qubit_count <= 0
    ):
        raise ValueError("worker qubit_count must be a positive integer")
    if (
        not isinstance(clbit_count, int)
        or isinstance(clbit_count, bool)
        or clbit_count <= 0
    ):
        raise ValueError("worker clbit_count must be a positive integer")
    measurements = _measurement_pairs(payload.get("measurements"), clbit_count)
    if any(qubit >= qubit_count for qubit, _clbit in measurements):
        raise ValueError("worker measurement qubit is out of range")
    if target == "originq":
        return _run_originq(source, shots, clbit_count)
    if target == "braket":
        return _run_braket(source, shots, clbit_count, measurements)
    if target == "spinq":
        return _run_spinq(
            source, shots, qubit_count, clbit_count, measurements
        )
    raise ValueError("unknown worker target: %s" % target)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: sdk_worker.py TARGET OUTPUT_JSON", file=sys.stderr)
        return 2
    target, output_name = sys.argv[1:]
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("worker request must be an object")
        result = _execute(target, payload)
        Path(output_name).write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8"
        )
    except SDKUnavailableError as exc:
        print("%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 3
    except Exception as exc:
        print("%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
