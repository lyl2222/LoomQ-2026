#!/usr/bin/env python3
"""LoomQ submission adapter contract v1.0.

The public functions stay intentionally thin.  All vendors share the same
parser and neutral circuit model under ``loomq``; only rendering and execution
are backend-specific.
"""

from typing import Any, Dict, List, Tuple

try:
    from .loomq import execute, parse_qasm2, render_target
    from .loomq.agent import agent_chat as _agent_chat
except ImportError:  # Support ``python evaluator.py`` inside starter_kit/.
    from loomq import execute, parse_qasm2, render_target
    from loomq.agent import agent_chat as _agent_chat


SUPPORTED_TARGETS = ("spinq", "originq", "braket")


def transpile(qasm_str: str, target: str) -> str:
    """Translate OpenQASM 2.0 into the target backend's native representation."""
    circuit = parse_qasm2(qasm_str)
    return render_target(circuit, target)


def run(qasm_str: str, target: str, shots: int) -> Dict[str, Any]:
    """Execute a circuit and return the unified result schema from the rules."""
    circuit = parse_qasm2(qasm_str)
    return execute(circuit, target, shots)


def agent_chat(prompt: str) -> str:
    """Turn a plain-language request into a verified circuit or backend choice."""
    return _agent_chat(prompt)


def compile_hybrid(hybrid_qasm_str: str) -> Tuple[List[str], str]:
    """Optional L3 entry point. Return quantum operations and RISC-V assembly."""
    raise NotImplementedError(
        "L3 is optional; implement compile_hybrid(hybrid_qasm_str) to enter"
    )
