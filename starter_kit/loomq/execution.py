"""Execute neutral LoomQ circuits through isolated vendor SDK workers.

SpinQit and Amazon Braket currently pin incompatible ANTLR runtime versions.
Importing every vendor SDK into one Python process would therefore make a
reproducible installation impossible.  This module keeps the public result
contract in the main process and starts one short-lived worker with only the
selected SDK on its import path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

from .circuit import Circuit, Measurement
from .targets import TargetError, render_braket, render_spinq


class BackendExecutionError(RuntimeError):
    """Raised when a backend cannot execute or returns an invalid result."""


class BackendUnavailableError(BackendExecutionError):
    """Raised when the selected backend SDK is not installed or configured."""


_SDK_PATH_ENV = {
    "spinq": "LOOMQ_SPINQ_PYTHONPATH",
    "originq": "LOOMQ_ORIGINQ_PYTHONPATH",
    "braket": "LOOMQ_BRAKET_PYTHONPATH",
}
_DEFAULT_SDK_PATH = {
    "spinq": "/opt/loomq-sdk/spinq",
    "originq": "/opt/loomq-sdk/originq",
    "braket": "/opt/loomq-sdk/braket",
}


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _worker_environment(target: str) -> Dict[str, str]:
    environment = os.environ.copy()
    configured = environment.get(_SDK_PATH_ENV[target])
    sdk_path = configured or _DEFAULT_SDK_PATH[target]
    if configured and not os.path.isdir(configured):
        raise BackendUnavailableError(
            "%s points to a missing directory: %s"
            % (_SDK_PATH_ENV[target], configured)
        )
    if os.path.isdir(sdk_path):
        previous = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            sdk_path + os.pathsep + previous if previous else sdk_path
        )
    environment.setdefault(
        "MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "loomq-matplotlib")
    )
    return environment


def _timeout_seconds() -> float:
    raw = os.environ.get("LOOMQ_BACKEND_TIMEOUT_SECONDS", "120")
    try:
        value = float(raw)
    except ValueError as exc:
        raise BackendExecutionError(
            "LOOMQ_BACKEND_TIMEOUT_SECONDS must be a number"
        ) from exc
    if value <= 0:
        raise BackendExecutionError(
            "LOOMQ_BACKEND_TIMEOUT_SECONDS must be positive"
        )
    return value


def _invoke_worker(
    target: str,
    source: str,
    shots: int,
    qubit_count: int,
    clbit_count: int,
    measurements: list[list[int]],
) -> Mapping[str, Any]:
    worker_path = Path(__file__).with_name("sdk_worker.py")
    payload = {
        "source": source,
        "shots": shots,
        "qubit_count": qubit_count,
        "clbit_count": clbit_count,
        "measurements": measurements,
    }
    with tempfile.TemporaryDirectory(prefix="loomq-worker-") as directory:
        output_path = Path(directory) / "result.json"
        try:
            completed = subprocess.run(
                [sys.executable, str(worker_path), target, str(output_path)],
                input=json.dumps(payload),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_worker_environment(target),
                timeout=_timeout_seconds(),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise BackendExecutionError(
                "%s backend exceeded the execution timeout" % target
            ) from exc

        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            detail = detail[-1500:] if detail else "worker exited without an error message"
            error_type = (
                BackendUnavailableError
                if completed.returncode == 3
                else BackendExecutionError
            )
            raise error_type("%s backend failed: %s" % (target, detail))
        try:
            result = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BackendExecutionError(
                "%s backend returned no valid worker result" % target
            ) from exc
    if not isinstance(result, dict):
        raise BackendExecutionError("%s backend result must be an object" % target)
    return result


def _validated_counts(
    raw_counts: Any, clbit_count: int, shots: int
) -> Dict[str, int]:
    if not isinstance(raw_counts, Mapping) or not raw_counts:
        raise BackendExecutionError("backend returned no measurement counts")
    counts: Dict[str, int] = {}
    for raw_key, raw_value in raw_counts.items():
        if not isinstance(raw_key, str) or not raw_key or set(raw_key) - {"0", "1"}:
            raise BackendExecutionError("backend returned a non-binary count key")
        if len(raw_key) > clbit_count:
            raise BackendExecutionError("backend returned a count key wider than the creg")
        if (
            not isinstance(raw_value, int)
            or isinstance(raw_value, bool)
            or raw_value < 0
        ):
            raise BackendExecutionError("backend returned an invalid count value")
        key = raw_key.zfill(clbit_count)
        counts[key] = counts.get(key, 0) + raw_value
    if sum(counts.values()) != shots:
        raise BackendExecutionError("backend counts total does not equal requested shots")
    return dict(sorted(counts.items()))


def execute(circuit: Circuit, target: str, shots: int) -> Dict[str, Any]:
    """Execute ``circuit`` and produce the competition's unified result schema."""

    if not isinstance(target, str):
        raise TargetError("target must be one of: braket, originq, spinq")
    normalized = target.strip().lower()
    if normalized not in _SDK_PATH_ENV:
        raise TargetError(
            "unsupported target %r; choose braket, originq or spinq" % target
        )
    if not isinstance(shots, int) or isinstance(shots, bool) or shots <= 0:
        raise ValueError("shots must be a positive integer")
    if circuit.clbit_count <= 0 or not any(
        isinstance(item, Measurement) for item in circuit.instructions
    ):
        raise ValueError("run() requires at least one measurement and classic bit")

    source = render_braket(circuit) if normalized == "braket" else render_spinq(circuit)
    measurements = [
        [item.qubit, item.clbit]
        for item in circuit.instructions
        if isinstance(item, Measurement)
    ]
    worker_result = _invoke_worker(
        normalized,
        source,
        shots,
        circuit.qubit_count,
        circuit.clbit_count,
        measurements,
    )
    counts = _validated_counts(worker_result.get("counts"), circuit.clbit_count, shots)

    backend = worker_result.get("backend")
    if not isinstance(backend, str) or not backend:
        raise BackendExecutionError("backend worker omitted its canonical backend name")
    job_id = worker_result.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        job_id = "%s-local-%s" % (normalized, uuid.uuid4().hex)
    metadata = worker_result.get("meta")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata = dict(metadata)
    metadata.update(
        {
            "transpiled_gates": circuit.gate_count,
            "depth": circuit.depth,
        }
    )
    return {
        "backend": backend,
        "job_id": job_id,
        "shots": shots,
        "counts": counts,
        "bit_order": "little",
        "timestamp": _utc_timestamp(),
        "meta": metadata,
    }


__all__ = (
    "BackendExecutionError",
    "BackendUnavailableError",
    "execute",
)
