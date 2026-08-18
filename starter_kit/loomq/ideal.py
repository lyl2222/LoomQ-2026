"""Deterministic ideal probabilities for LoomQ's 12-gate subset.

The web charts overlay this noiseless distribution on a finite-shot sample so
beginners can see the same experiment/theory contrast as a hardware console.
``adapter.run()`` is left unchanged: this overlay is computed only by the web API.
"""

from __future__ import annotations

import cmath
import math
from typing import Dict, List

from .circuit import Circuit, GateOperation, Measurement

_SQRT2_INV = 1.0 / math.sqrt(2.0)
_MAX_QUBITS = 16


def _one_qubit(name: str, angle: float | None) -> List[List[complex]]:
    if name == "h":
        return [
            [_SQRT2_INV, _SQRT2_INV],
            [_SQRT2_INV, -_SQRT2_INV],
        ]
    if name == "x":
        return [[0.0, 1.0], [1.0, 0.0]]
    if name == "s":
        return [[1.0, 0.0], [0.0, 1j]]
    if name == "sdg":
        return [[1.0, 0.0], [0.0, -1j]]
    if name == "t":
        return [[1.0, 0.0], [0.0, cmath.exp(1j * math.pi / 4.0)]]
    if name == "tdg":
        return [[1.0, 0.0], [0.0, cmath.exp(-1j * math.pi / 4.0)]]
    if name == "rz":
        half = (angle or 0.0) / 2.0
        return [
            [cmath.exp(-1j * half), 0.0],
            [0.0, cmath.exp(1j * half)],
        ]
    if name == "ry":
        half = (angle or 0.0) / 2.0
        cosine, sine = math.cos(half), math.sin(half)
        return [[cosine, -sine], [sine, cosine]]
    raise ValueError("unsupported one-qubit gate: %s" % name)


def _apply_one(state: List[complex], qubit: int, matrix: List[List[complex]]) -> List[complex]:
    updated = [0j] * len(state)
    mask = 1 << qubit
    for source, amplitude in enumerate(state):
        source_bit = 1 if source & mask else 0
        base = source & ~mask
        for dest_bit in (0, 1):
            updated[base | (dest_bit << qubit)] += matrix[dest_bit][source_bit] * amplitude
    return updated


def _controlled_not(state: List[complex], control: int, target: int) -> List[complex]:
    updated = [0j] * len(state)
    control_mask = 1 << control
    target_mask = 1 << target
    for index, amplitude in enumerate(state):
        destination = index ^ target_mask if index & control_mask else index
        updated[destination] += amplitude
    return updated


def _controlled_phase(state: List[complex], control: int, target: int, angle: float) -> List[complex]:
    phase = cmath.exp(1j * angle)
    mask = (1 << control) | (1 << target)
    return [
        amplitude * phase if (index & mask) == mask else amplitude
        for index, amplitude in enumerate(state)
    ]


def _swap(state: List[complex], left: int, right: int) -> List[complex]:
    if left == right:
        return list(state)
    updated = [0j] * len(state)
    flip = (1 << left) | (1 << right)
    for index, amplitude in enumerate(state):
        left_bit = (index >> left) & 1
        right_bit = (index >> right) & 1
        destination = index if left_bit == right_bit else index ^ flip
        updated[destination] += amplitude
    return updated


def _toffoli(state: List[complex], first: int, second: int, target: int) -> List[complex]:
    updated = [0j] * len(state)
    controls = (1 << first) | (1 << second)
    target_mask = 1 << target
    for index, amplitude in enumerate(state):
        destination = index ^ target_mask if (index & controls) == controls else index
        updated[destination] += amplitude
    return updated


def _evolve(circuit: Circuit) -> List[complex]:
    state = [0j] * (1 << circuit.qubit_count)
    state[0] = 1.0 + 0j
    for item in circuit.instructions:
        if not isinstance(item, GateOperation):
            continue
        if item.name in {"h", "x", "s", "sdg", "t", "tdg", "rz", "ry"}:
            state = _apply_one(state, item.qubits[0], _one_qubit(item.name, item.angle))
        elif item.name == "cx":
            state = _controlled_not(state, item.qubits[0], item.qubits[1])
        elif item.name == "cu1":
            state = _controlled_phase(
                state, item.qubits[0], item.qubits[1], item.angle or 0.0
            )
        elif item.name == "swap":
            state = _swap(state, item.qubits[0], item.qubits[1])
        elif item.name == "ccx":
            state = _toffoli(state, item.qubits[0], item.qubits[1], item.qubits[2])
        else:
            raise ValueError("unsupported gate: %s" % item.name)
    return state


def ideal_probabilities(circuit: Circuit) -> Dict[str, float]:
    """Return little-endian classic-bit probabilities after all measurements."""

    if circuit.qubit_count > _MAX_QUBITS:
        raise ValueError("ideal overlay supports at most %d qubits" % _MAX_QUBITS)
    if circuit.clbit_count <= 0:
        return {}
    measurements = [item for item in circuit.instructions if isinstance(item, Measurement)]
    if not measurements:
        return {}
    state = _evolve(circuit)
    totals = [0.0] * (1 << circuit.clbit_count)
    for index, amplitude in enumerate(state):
        probability = abs(amplitude) ** 2
        if probability <= 1e-15:
            continue
        bits = 0
        for item in measurements:
            if index & (1 << item.qubit):
                bits |= 1 << item.clbit
        totals[bits] += probability
    width = circuit.clbit_count
    return {
        format(value, "0%db" % width): probability
        for value, probability in enumerate(totals)
        if probability > 1e-12
    }


__all__ = ("ideal_probabilities",)
