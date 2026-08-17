"""Render LoomQ's neutral circuit into each competition target contract."""

from __future__ import annotations

import math
from typing import Callable, Dict, List

from .circuit import Circuit, GateOperation, Measurement


class TargetError(ValueError):
    """Raised when an unknown target backend is requested."""


def _angle(value: float) -> str:
    if math.isclose(value, 0.0, abs_tol=1e-15):
        return "0"
    return format(value, ".17g")


def _qasm_gate(item: GateOperation, names: Dict[str, str]) -> str:
    name = names.get(item.name, item.name)
    if item.angle is not None:
        name += "(%s)" % _angle(item.angle)
    operands = ", ".join("q[%d]" % index for index in item.qubits)
    return "%s %s;" % (name, operands)


def render_spinq(circuit: Circuit) -> str:
    lines: List[str] = [
        "OPENQASM 2.0;",
        'include "qelib1.inc";',
        "qreg q[%d];" % circuit.qubit_count,
        "creg c[%d];" % circuit.clbit_count,
    ]
    for item in circuit.instructions:
        if isinstance(item, GateOperation):
            lines.append(_qasm_gate(item, {}))
        else:
            lines.append("measure q[%d] -> c[%d];" % (item.qubit, item.clbit))
    return "\n".join(lines) + "\n"


def render_braket(circuit: Circuit) -> str:
    """Render portable OpenQASM 3 as required by target_ir_contract.md."""

    lines: List[str] = [
        "OPENQASM 3.0;",
        'include "stdgates.inc";',
        "qubit[%d] q;" % circuit.qubit_count,
        "bit[%d] c;" % circuit.clbit_count,
    ]
    names = {"cx": "cnot", "cu1": "cp"}
    for item in circuit.instructions:
        if isinstance(item, GateOperation):
            lines.append(_qasm_gate(item, names))
        else:
            lines.append("c[%d] = measure q[%d];" % (item.clbit, item.qubit))
    return "\n".join(lines) + "\n"


def render_originq(circuit: Circuit) -> str:
    names = {
        "h": "H",
        "x": "X",
        "s": "S",
        "sdg": "SDAG",
        "t": "T",
        "tdg": "TDAG",
        "rz": "RZ",
        "ry": "RY",
        "cx": "CNOT",
        "cu1": "CU1",
        "swap": "SWAP",
        "ccx": "TOFFOLI",
    }
    lines = ["QINIT %d" % circuit.qubit_count, "CREG %d" % circuit.clbit_count]
    for item in circuit.instructions:
        if isinstance(item, GateOperation):
            name = names[item.name]
            if item.angle is not None:
                name += "(%s)" % _angle(item.angle)
            operands = ", ".join("q[%d]" % index for index in item.qubits)
            lines.append("%s %s" % (name, operands))
        else:
            lines.append("MEASURE q[%d], c[%d]" % (item.qubit, item.clbit))
    return "\n".join(lines) + "\n"


_RENDERERS: Dict[str, Callable[[Circuit], str]] = {
    "spinq": render_spinq,
    "originq": render_originq,
    "braket": render_braket,
}


def render_target(circuit: Circuit, target: str) -> str:
    if not isinstance(target, str):
        raise TargetError("target must be one of: braket, originq, spinq")
    normalized = target.strip().lower()
    try:
        renderer = _RENDERERS[normalized]
    except KeyError as exc:
        raise TargetError(
            "unsupported target %r; choose braket, originq or spinq" % target
        ) from exc
    return renderer(circuit)
