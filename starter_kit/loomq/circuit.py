"""Parse the LoomQ OpenQASM 2.0 subset into a backend-neutral circuit.

The competition input is deliberately bounded to twelve qelib1 gates.  Keeping
the parser here independent from every vendor SDK is the key architectural
boundary: validation and register flattening happen once, then each backend is
only responsible for rendering or executing the same :class:`Circuit`.
"""

from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple, Union


class QASMParseError(ValueError):
    """Raised when input is outside LoomQ's documented OpenQASM 2.0 subset."""


@dataclass(frozen=True)
class GateOperation:
    """A normalized quantum gate whose qubits use flat, zero-based indices."""

    name: str
    qubits: Tuple[int, ...]
    angle: Optional[float] = None


@dataclass(frozen=True)
class Measurement:
    """A measurement from one normalized qubit to one normalized classic bit."""

    qubit: int
    clbit: int


Instruction = Union[GateOperation, Measurement]


@dataclass(frozen=True)
class Circuit:
    """The vendor-independent representation consumed by all LoomQ adapters."""

    qubit_count: int
    clbit_count: int
    instructions: Tuple[Instruction, ...]

    @property
    def gate_count(self) -> int:
        return sum(isinstance(item, GateOperation) for item in self.instructions)

    @property
    def depth(self) -> int:
        """Return a simple logical gate depth (measurements are not counted)."""

        levels = [0] * self.qubit_count
        for item in self.instructions:
            if not isinstance(item, GateOperation):
                continue
            level = max((levels[index] for index in item.qubits), default=0) + 1
            for index in item.qubits:
                levels[index] = level
        return max(levels, default=0)


# gate name -> (qubit arity, has one angle parameter)
GATE_SPECS = {
    "h": (1, False),
    "x": (1, False),
    "s": (1, False),
    "sdg": (1, False),
    "t": (1, False),
    "tdg": (1, False),
    "rz": (1, True),
    "ry": (1, True),
    "cx": (2, False),
    "cu1": (2, True),
    "swap": (2, False),
    "ccx": (3, False),
}

_REGISTER_RE = re.compile(
    r"^(?P<kind>qreg|creg)\s+(?P<name>[A-Za-z_]\w*)\s*\[\s*(?P<size>\d+)\s*\]$",
    re.IGNORECASE,
)
_REFERENCE_RE = re.compile(
    r"^(?P<name>[A-Za-z_]\w*)\s*\[\s*(?P<index>\d+)\s*\]$"
)
_GATE_RE = re.compile(
    r"^(?P<name>[A-Za-z_]\w*)\s*(?:\((?P<angle>.*)\))?\s+(?P<operands>.+)$"
)


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return "\n".join(line.split("//", 1)[0] for line in source.splitlines())


def _statements(source: str) -> Iterable[str]:
    for statement in _without_comments(source).split(";"):
        stripped = statement.strip()
        if stripped:
            yield stripped


def _evaluate_angle(expression: str) -> float:
    """Safely evaluate numeric/pi arithmetic accepted in qelib1 parameters."""

    if not expression.strip():
        raise QASMParseError("parameterized gate is missing its angle")
    try:
        root = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise QASMParseError("invalid angle expression: %s" % expression) from exc

    def evaluate(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise QASMParseError("angle may contain numbers and pi only")
            return float(node.value)
        if isinstance(node, ast.Name) and node.id == "pi":
            return math.pi
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)
        ):
            left = evaluate(node.left)
            right = evaluate(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            try:
                return left / right
            except ZeroDivisionError as exc:
                raise QASMParseError("angle expression divides by zero") from exc
        raise QASMParseError("angle may contain numbers, pi, +, -, * and / only")

    value = evaluate(root)
    if not math.isfinite(value):
        raise QASMParseError("angle must be finite")
    return value


def _resolve_reference(
    text: str,
    registers: Dict[str, Tuple[int, int]],
    expected_kind: str,
) -> int:
    match = _REFERENCE_RE.fullmatch(text.strip())
    if not match:
        raise QASMParseError("expected an indexed %s reference: %s" % (expected_kind, text))
    name = match.group("name")
    if name not in registers:
        raise QASMParseError("unknown %s register: %s" % (expected_kind, name))
    offset, size = registers[name]
    index = int(match.group("index"))
    if index >= size:
        raise QASMParseError(
            "%s index out of range: %s[%d] has size %d"
            % (expected_kind, name, index, size)
        )
    return offset + index


def _resolve_qubit_broadcast(
    text: str,
    qregs: Dict[str, Tuple[int, int]],
) -> Tuple[int, ...]:
    """Return one qubit or every qubit in a whole quantum register."""

    stripped = text.strip()
    if _REFERENCE_RE.fullmatch(stripped):
        return (_resolve_reference(stripped, qregs, "quantum"),)
    if stripped in qregs:
        offset, size = qregs[stripped]
        return tuple(range(offset, offset + size))
    raise QASMParseError("expected a qubit or quantum register: %s" % text)


def _broadcast_qubit_groups(groups: List[Tuple[int, ...]]) -> List[Tuple[int, ...]]:
    """Expand OpenQASM 2.0 register operands into per-qubit applications."""

    non_scalar = {len(group) for group in groups if len(group) != 1}
    if len(non_scalar) > 1:
        raise QASMParseError("broadcast registers must have equal size")
    width = next(iter(non_scalar)) if non_scalar else 1
    return [
        tuple(group[index] if len(group) > 1 else group[0] for group in groups)
        for index in range(width)
    ]


def _parse_measurement(
    statement: str,
    qregs: Dict[str, Tuple[int, int]],
    cregs: Dict[str, Tuple[int, int]],
) -> Tuple[Measurement, ...]:
    match = re.fullmatch(r"measure\s+(.+?)\s*->\s*(.+)", statement, re.IGNORECASE)
    if not match:
        raise QASMParseError("invalid measurement statement: %s" % statement)
    quantum = match.group(1).strip()
    classic = match.group(2).strip()

    quantum_is_register = quantum in qregs
    classic_is_register = classic in cregs
    if quantum_is_register or classic_is_register:
        if not (quantum_is_register and classic_is_register):
            raise QASMParseError("whole-register measurement needs two whole registers")
        q_offset, q_size = qregs[quantum]
        c_offset, c_size = cregs[classic]
        if q_size != c_size:
            raise QASMParseError("measured quantum and classic registers must have equal size")
        return tuple(
            Measurement(q_offset + index, c_offset + index) for index in range(q_size)
        )

    qubit = _resolve_reference(quantum, qregs, "quantum")
    clbit = _resolve_reference(classic, cregs, "classic")
    return (Measurement(qubit, clbit),)


def parse_qasm2(source: str) -> Circuit:
    """Parse one OpenQASM 2.0 program using the competition's gate whitelist."""

    if not isinstance(source, str) or not source.strip():
        raise QASMParseError("QASM source must be a non-empty string")

    statements = list(_statements(source))
    if not statements or not re.fullmatch(
        r"OPENQASM\s+2\.0", statements[0], re.IGNORECASE
    ):
        raise QASMParseError("program must start with OPENQASM 2.0;")

    qregs: Dict[str, Tuple[int, int]] = {}
    cregs: Dict[str, Tuple[int, int]] = {}
    qubit_count = 0
    clbit_count = 0
    instructions = []
    declarations_finished = False
    qelib_included = False

    for statement in statements[1:]:
        if re.fullmatch(r'include\s+"qelib1\.inc"', statement, re.IGNORECASE):
            if declarations_finished or qregs or cregs:
                raise QASMParseError("qelib1.inc must be included before declarations")
            if qelib_included:
                raise QASMParseError("qelib1.inc may only be included once")
            qelib_included = True
            continue
        if statement.lower().startswith("include"):
            raise QASMParseError("only qelib1.inc may be included")

        declaration = _REGISTER_RE.fullmatch(statement)
        if declaration:
            if declarations_finished:
                raise QASMParseError("register declarations must precede circuit operations")
            name = declaration.group("name")
            size = int(declaration.group("size"))
            if size <= 0:
                raise QASMParseError("register size must be positive")
            if name in qregs or name in cregs:
                raise QASMParseError("duplicate register name: %s" % name)
            if declaration.group("kind").lower() == "qreg":
                qregs[name] = (qubit_count, size)
                qubit_count += size
            else:
                cregs[name] = (clbit_count, size)
                clbit_count += size
            continue

        declarations_finished = True
        if statement.lower().startswith("measure"):
            instructions.extend(_parse_measurement(statement, qregs, cregs))
            continue

        gate_match = _GATE_RE.fullmatch(statement)
        if not gate_match:
            raise QASMParseError("unsupported or malformed statement: %s" % statement)
        name = gate_match.group("name").lower()
        if name not in GATE_SPECS:
            raise QASMParseError("gate is outside the 12-gate whitelist: %s" % name)
        arity, has_angle = GATE_SPECS[name]
        angle_text = gate_match.group("angle")
        if has_angle and angle_text is None:
            raise QASMParseError("gate %s requires one angle" % name)
        if not has_angle and angle_text is not None:
            raise QASMParseError("gate %s does not accept an angle" % name)
        operands = [part.strip() for part in gate_match.group("operands").split(",")]
        if len(operands) != arity or any(not part for part in operands):
            raise QASMParseError("gate %s requires %d qubit operand(s)" % (name, arity))
        angle = _evaluate_angle(angle_text) if angle_text is not None else None
        groups = [_resolve_qubit_broadcast(operand, qregs) for operand in operands]
        for qubits in _broadcast_qubit_groups(groups):
            if len(set(qubits)) != len(qubits):
                raise QASMParseError("gate %s cannot use the same qubit twice" % name)
            instructions.append(GateOperation(name=name, qubits=qubits, angle=angle))

    if not qelib_included:
        raise QASMParseError('program must include "qelib1.inc"')
    if not qregs:
        raise QASMParseError("program declares no quantum register")
    return Circuit(
        qubit_count=qubit_count,
        clbit_count=clbit_count,
        instructions=tuple(instructions),
    )
