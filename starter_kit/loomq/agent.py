"""LoomQ L2 agent: natural language in, verified circuit or backend advice out."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

try:
    from ..llm_client import chat_completion
except ImportError:  # ``adapter.py`` imported from inside starter_kit/.
    from llm_client import chat_completion

from .circuit import Circuit, Measurement, parse_qasm2
from .execution import execute


_MAX_ATTEMPTS = 3
_QASM_RE = re.compile(
    r"OPENQASM\s+2\.0;.*?(?=^\s*```|\Z)", re.DOTALL | re.MULTILINE | re.IGNORECASE
)


def _load_capabilities() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "backend_capabilities.json"
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _system_prompt(capabilities: dict[str, Any]) -> str:
    return """你是 LoomQ，一位面向零量子背景用户的量子计算助手。你必须真正理解用户意图，不能靠关键词套固定答案。

你处理三类任务：
1. 生成量子电路；
2. 修复用户给出的错误电路，并优先保证用户声明的目标语义；
3. 根据官方能力表推荐后端。

电路规则：
- 只输出 OpenQASM 2.0；必须包含 include \"qelib1.inc\"、qreg、creg 和测量。
- 只能使用 h, x, s, sdg, t, tdg, rz(theta), ry(theta), cx, cu1(theta), swap, ccx。
- 不要使用 barrier、reset、if、u、u1、u2、u3、p、z 或白名单外的门。
- 严格满足用户指定的比特数、目标态和测量要求。GHZ 是先对第 0 位做 h，再用 cx 把关联逐位传下去；Bell 是它的 2 比特版本。
- 回复必须包含恰好一个完整的 ```qasm 代码块。代码块外用一两句不含术语的中文解释电路会得到什么结果。

后端推荐规则：
- 只以随本消息提供的官方能力表为准，同时满足用户的比特数、真机/模拟器、排队、费用和账号约束。
- 回复必须逐字包含恰好一个满足全部约束的规范后端 id；不要输出 QASM，也不要列出多个候选。
- 若没有满足全部约束的后端，应明确说无解、包含标记 LOOMQ_BACKEND_NO_MATCH，
  不要出现任何规范后端 id，并说明哪项约束冲突，不能编造后端。

官方后端能力表：
""" + json.dumps(capabilities, ensure_ascii=False, separators=(",", ":"))


def _completion_text(response: dict[str, Any]) -> str:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("LoomQ L2 API returned no assistant message") from exc
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("LoomQ L2 API returned an empty assistant message")
    return content.strip()


def _extract_qasm(text: str) -> str | None:
    match = _QASM_RE.search(text)
    return match.group(0).strip() if match else None


def _mentioned_backend_ids(text: str, backend_ids: set[str]) -> list[str]:
    return sorted(
        backend_id
        for backend_id in backend_ids
        if re.search(r"(?<![A-Za-z0-9_])" + re.escape(backend_id) + r"(?![A-Za-z0-9_])", text)
    )


def _backend_recommendation(text: str, backend_ids: set[str]) -> str | None:
    selected = _mentioned_backend_ids(text, backend_ids)
    has_no_match = "LOOMQ_BACKEND_NO_MATCH" in text
    if has_no_match and not selected:
        return text
    if not has_no_match and len(selected) == 1:
        return text
    return None


def _validate_circuit(qasm: str) -> Circuit:
    circuit = parse_qasm2(qasm)
    if circuit.clbit_count <= 0:
        raise ValueError("电路缺少经典寄存器 creg")
    measured = {
        operation.clbit
        for operation in circuit.instructions
        if isinstance(operation, Measurement)
    }
    if len(measured) != circuit.clbit_count:
        raise ValueError("电路必须测量全部经典位")
    return circuit


def _validation_settings() -> tuple[str, int]:
    target = os.environ.get("LOOMQ_AGENT_VALIDATION_TARGET", "originq").strip().lower()
    if target not in {"spinq", "originq", "braket"}:
        raise RuntimeError(
            "LOOMQ_AGENT_VALIDATION_TARGET must be spinq, originq or braket"
        )
    raw_shots = os.environ.get("LOOMQ_AGENT_VALIDATION_SHOTS", "512")
    try:
        shots = int(raw_shots)
    except ValueError as exc:
        raise RuntimeError("LOOMQ_AGENT_VALIDATION_SHOTS must be an integer") from exc
    if shots <= 0:
        raise RuntimeError("LOOMQ_AGENT_VALIDATION_SHOTS must be positive")
    return target, shots


def _verified_reply(model_text: str, qasm: str, result: dict[str, Any]) -> str:
    prefix = model_text[: model_text.lower().find("openqasm")]
    prefix = re.sub(r"```(?:qasm|openqasm)?\s*$", "", prefix, flags=re.IGNORECASE).strip()
    if not prefix:
        prefix = "已经按你的目标准备好电路。"
    leaders = sorted(result["counts"].items(), key=lambda item: item[1], reverse=True)[:4]
    result_text = "、".join(f"{state}（{count} 次）" for state, count in leaders)
    return (
        f"{prefix}\n\n```qasm\n{qasm}\n```\n\n"
        f"✅ 已用 {result['backend']} 做过 {result['shots']} 次试跑，电路可以执行。"
        f"最常见结果：{result_text}。"
    )


def agent_chat(prompt: str) -> str:
    """Answer a user request and verify generated circuits through LoomQ L1."""

    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")

    capabilities = _load_capabilities()
    backend_ids = {
        item["id"] for item in capabilities.get("backends", []) if "id" in item
    }
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _system_prompt(capabilities)},
        {"role": "user", "content": prompt.strip()},
    ]
    last_error = "模型没有给出可验证的答案"

    for _attempt in range(_MAX_ATTEMPTS):
        model_text = _completion_text(chat_completion(messages))
        qasm = _extract_qasm(model_text)
        if qasm is None:
            recommendation = _backend_recommendation(model_text, backend_ids)
            if recommendation is not None:
                return recommendation
            last_error = (
                "后端推荐必须恰好包含一个官方后端 id；"
                "无解时只能使用 LOOMQ_BACKEND_NO_MATCH，且不能同时给出后端 id"
            )
        else:
            try:
                circuit = _validate_circuit(qasm)
                target, shots = _validation_settings()
                result = execute(circuit, target, shots)
                return _verified_reply(model_text, qasm, result)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"

        messages.extend(
            [
                {"role": "assistant", "content": model_text},
                {
                    "role": "user",
                    "content": (
                        "上一个答案没有通过 LoomQ 的确定性检查："
                        + last_error
                        + "。请保持我最初的目标不变，重新给出完整且符合规则的答案。"
                    ),
                },
            ]
        )

    raise RuntimeError("LoomQ Agent tried three times but could not verify the answer: " + last_error)


__all__ = ("agent_chat",)
