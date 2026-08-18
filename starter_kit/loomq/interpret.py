"""Plain-language interpreter for a LoomQ web run.

The competition ``adapter.run()`` contract stays untouched.  This overlay is
computed only by the local web API so beginners can read the experiment and its
parameters without opening QASM.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .circuit import Circuit, GateOperation, Measurement, QASMParseError, parse_qasm2

_GATE_COPY = {
    "h": ("H", "把确定的答案拆成两种一起参与后续操作的可能"),
    "x": ("X", "把 0 和 1 对调"),
    "s": ("S", "给其中一种可能转一个直角相位；这次测量的 0/1 比例不变"),
    "sdg": ("S†", "把 S 转过的相位转回去"),
    "t": ("T", "给其中一种可能转 45° 相位"),
    "tdg": ("T†", "把 T 转过的相位转回去"),
    "rz": ("RZ", "绕竖直轴转一个指定角度"),
    "ry": ("RY", "按指定角度改掉 0 和 1 的混合比例"),
    "cx": ("CNOT", "控制位是 1 时，目标位跟着翻面"),
    "cu1": ("CU1", "两个位都参与时才转相位，用来制造更细的干涉"),
    "swap": ("SWAP", "两枚硬币互换位置"),
    "ccx": ("Toffoli", "前两个控制都是 1 时，才翻转第三枚"),
}

_TARGET_COPY = {
    "originq": (
        "本源 · 本地模拟器",
        "不排队、无需账号，也没有真机噪声，适合先看清电路本身。",
    ),
    "spinq": (
        "量旋 · Taurus 模拟器",
        "用与量旋同一套电路语言在本地试跑，仍然没有真机噪声。",
    ),
    "braket": (
        "AWS · Braket 本地模拟器",
        "用 Amazon Braket 的本地模拟器试跑，仍然没有真机噪声。",
    ),
}


def _angle_text(angle: float | None) -> str:
    if angle is None:
        return ""
    ratio = angle / math.pi
    for numerator, label in (
        (0.0, ""),
        (1.0, "π"),
        (-1.0, "−π"),
        (0.5, "π/2"),
        (-0.5, "−π/2"),
        (0.25, "π/4"),
        (-0.25, "−π/4"),
        (0.125, "π/8"),
        (-0.125, "−π/8"),
    ):
        if abs(ratio - numerator) < 1e-9:
            return f"（{label}）" if label else ""
    return f"（{angle:.2f}）"


def _gates(circuit: Circuit) -> List[GateOperation]:
    return [item for item in circuit.instructions if isinstance(item, GateOperation)]


def _measurements(circuit: Circuit) -> List[Measurement]:
    return [item for item in circuit.instructions if isinstance(item, Measurement)]


def _qubit_phrase(qubits: Sequence[int]) -> str:
    labels = "、".join(str(index + 1) for index in qubits)
    if len(qubits) == 1:
        return f"第 {labels} 枚"
    return f"第 {labels} 枚"


def _operation_summary(gates: Sequence[GateOperation]) -> str:
    if not gates:
        return "无（直接测量）"
    counts = Counter(gate.name for gate in gates)
    parts = []
    for name, count in counts.items():
        label = _GATE_COPY.get(name, (name.upper(),))[0]
        parts.append(f"{label} × {count}")
    return " · ".join(parts)


def _is_ghz(gates: Sequence[GateOperation], qubit_count: int) -> bool:
    if qubit_count < 2 or len(gates) != qubit_count:
        return False
    if gates[0].name != "h" or gates[0].qubits != (0,):
        return False
    if any(gate.name != "cx" for gate in gates[1:]):
        return False
    used = {0}
    for gate in gates[1:]:
        control, target = gate.qubits
        if control not in used or target in used:
            return False
        used.add(target)
    return used == set(range(qubit_count))


def _pattern(circuit: Circuit) -> Tuple[str, str, List[str]]:
    gates = _gates(circuit)
    names = [gate.name for gate in gates]
    n = circuit.qubit_count

    if not names:
        return (
            "对照组：什么都不做",
            "量子位从确定的 0 出发。这次中间没有任何操作，直接揭晓，用来确认后面的变化来自电路，而不是模拟器自己在随机开奖。",
            ["所有量子位从 0 出发", "中间不做任何操作", "直接测量"],
        )
    if n == 1 and names == ["h"]:
        return (
            "一次 H：制造两种可能",
            "H 把确定的 0 变成 0 和 1 两种机会，然后立刻测量。看起来会像一枚公平硬币，但那只是揭晓后的样子。",
            ["从确定的 0 出发", "作用一次 H", "立刻测量"],
        )
    if n == 1 and names == ["h", "h"]:
        return (
            "两次 H：看见干涉",
            "两次 H 之间没有测量。两条可能路径会重新汇合：通往 0 的部分相加，通往 1 的部分相消。所以结果会回到确定的 0。",
            ["从确定的 0 出发", "第一次 H 拆出两条路径", "第二次 H 让路径汇合", "测量"],
        )
    if _is_ghz(gates, n):
        if n == 2:
            return (
                "贝尔实验：两枚硬币总是一起翻面",
                "先让第 1 枚进入两种可能，再让第 2 枚跟着第 1 枚变。理想结果只会出现 00 和 11；01 和 10 被抬起来，通常是真机噪声。",
                ["两枚都从 0 出发", "H 作用在第 1 枚", "CNOT 让第 2 枚跟随", "两枚一起测量"],
            )
        return (
            f"{n} 枚硬币总是一起翻面",
            f"先让第 1 枚进入两种可能，再让后面 {n - 1} 枚依次跟着变。理想情况下，它们会一起成为全 0 或一起成为全 1。",
            ["全部从 0 出发", "H 作用在第 1 枚", "用 CNOT 把这种同步关系传下去", "全部测量"],
        )
    if "cu1" in names and "h" in names:
        return (
            "相位干涉实验",
            "用 H 制造多种可能，再用受控相位让不同路径带上不同方向。测量时，有的组合会被加强，有的会被抵消。",
            ["准备若干从 0 出发的量子位", "用 H 和相位门编排路径", "测量，看哪些组合更常出现"],
        )
    return (
        f"{n} 枚量子硬币的实验",
        f"这次电路含有 {_operation_summary(gates)}。下面按发生顺序列出每一步在做什么，以及这次运行用了哪些参数。",
        [],
    )


def _fallback_steps(circuit: Circuit) -> List[str]:
    steps = [f"准备 {circuit.qubit_count} 枚从 0 出发的量子硬币"]
    for gate in _gates(circuit)[:8]:
        label, meaning = _GATE_COPY.get(gate.name, (gate.name.upper(), "执行这个操作"))
        steps.append(
            f"{label}{_angle_text(gate.angle)} 作用在{_qubit_phrase(gate.qubits)}：{meaning}"
        )
    remaining = max(0, len(_gates(circuit)) - 8)
    if remaining:
        steps.append(f"还有 {remaining} 步同类操作")
    measured = len(_measurements(circuit))
    if measured:
        steps.append(f"测量 {measured} 位，记下 0 和 1")
    return steps


def _parameters(circuit: Circuit, target: str, shots: int) -> List[Dict[str, str]]:
    place, place_why = _TARGET_COPY.get(
        target,
        (target, "在选定的后端上重复这个实验。"),
    )
    bits = circuit.clbit_count
    bit_example = "0 和 1" if bits <= 1 else "、".join(
        format(index, f"0{bits}b") for index in range(min(4, 1 << bits))
    )
    if bits > 2:
        bit_example += " 等"
    return [
        {
            "label": "运行地点",
            "value": place,
            "why": place_why,
        },
        {
            "label": "重复开奖",
            "value": f"{shots:,} 次",
            "why": "一次测量只揭晓一个样本。次数越多，彩色的本次采样越靠近灰色理想分布。",
        },
        {
            "label": "量子硬币",
            "value": f"{circuit.qubit_count} 枚",
            "why": "每枚在揭晓前都可以让多种可能一起参与后续操作。",
        },
        {
            "label": "记下的位数",
            "value": f"{bits} 位",
            "why": f"所以图上的标签是 {bit_example} 这样的组合。",
        },
        {
            "label": "中间操作",
            "value": _operation_summary(_gates(circuit)),
            "why": "按电路里实际发生的门统计；专业名字只是机器说明书。",
        },
    ]


def _reading(result: Mapping[str, Any], shots: int) -> str:
    counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
    ideal = result.get("ideal") if isinstance(result.get("ideal"), dict) else {}
    keys = set(counts) | set(ideal)
    gap = 0.0
    if keys and shots > 0:
        gap = max(
            abs((counts.get(state, 0) or 0) / shots - float(ideal.get(state, 0) or 0))
            for state in keys
        )
    if gap < 0.08:
        return "彩色柱贴着灰色柱：这次采样和理想分布一致。剩下的小差别来自有限次开奖，不是电路写错了。真机才会把 01 / 10 明显抬起来。"
    return "两条柱分开得比较明显。先看开奖次数是不是太少；本地无噪声模拟器通常不会单独把 01 / 10 抬高，那是真机噪声的特征。"


def interpret_circuit(circuit: Circuit, target: str, shots: int, result: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    title, summary, steps = _pattern(circuit)
    if not steps:
        steps = _fallback_steps(circuit)
    payload = {
        "title": title,
        "summary": summary,
        "steps": steps,
        "parameters": _parameters(circuit, target, shots),
        "reading": _reading(result or {}, shots),
    }
    return payload


def interpret_run(qasm: str, target: str, shots: int, result: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    """Return a beginner-facing explanation, or an empty dict if parsing fails."""

    try:
        circuit = parse_qasm2(qasm)
    except (QASMParseError, ValueError, MemoryError):
        return {}
    return interpret_circuit(circuit, target, shots, result)


__all__ = ("interpret_circuit", "interpret_run")
