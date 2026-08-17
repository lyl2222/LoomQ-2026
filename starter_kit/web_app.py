#!/usr/bin/env python3
"""Zero-dependency local web experience for the LoomQ accessibility demo."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

try:
    from . import adapter
except ImportError:
    import adapter


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
_QASM_RE = re.compile(
    r"OPENQASM\s+2\.0;.*?(?=^\s*```|\Z)", re.DOTALL | re.MULTILINE | re.IGNORECASE
)


def load_optional_env_file(path: Path | None = None) -> None:
    """Fill missing LOOMQ_* variables from a local .env without printing secrets."""

    env_path = path or (ROOT / ".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip("'").strip('"')


def _model_configured() -> bool:
    return all(
        os.environ.get(name)
        for name in ("LOOMQ_LLM_BASE_URL", "LOOMQ_LLM_API_KEY", "LOOMQ_LLM_MODEL")
    )


def _capabilities() -> dict[str, Any]:
    with (ROOT / "backend_capabilities.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def _chat(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("请求内容必须是 JSON 对象")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("请先用一句话描述你想完成的实验")
    response = adapter.agent_chat(prompt.strip())
    match = _QASM_RE.search(response)
    return {
        "response": response,
        "qasm": match.group(0).strip() if match else None,
    }


def _run(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("请求内容必须是 JSON 对象")
    qasm = payload.get("qasm")
    target = payload.get("target", "originq")
    shots = payload.get("shots", 1024)
    if not isinstance(qasm, str) or not qasm.strip():
        raise ValueError("还没有可运行的电路，请先让 LoomQ 生成一个实验")
    if target not in {"spinq", "originq", "braket"}:
        raise ValueError("未知后端，请选择 spinq、originq 或 braket")
    if not isinstance(shots, int) or isinstance(shots, bool) or not 1 <= shots <= 100000:
        raise ValueError("运行次数必须是 1 到 100000 之间的整数")
    return adapter.run(qasm, target, shots)


class LoomQHandler(BaseHTTPRequestHandler):
    server_version = "LoomQ/1.0"

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(WEB_ROOT.resolve())
            body = resolved.read_bytes()
        except (OSError, ValueError):
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        route = unquote(urlparse(self.path).path)
        if route == "/api/config":
            capabilities = _capabilities()
            self._send_json(
                200,
                {
                    "model_configured": _model_configured(),
                    "backends": capabilities.get("backends", []),
                },
            )
            return
        relative = "index.html" if route == "/" else route.lstrip("/")
        self._send_file(WEB_ROOT / relative)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        route = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                raise ValueError("请求内容为空或过大")
            payload = json.loads(self.rfile.read(length))
            if route == "/api/chat":
                result = _chat(payload)
            elif route == "/api/run":
                result = _run(payload)
            else:
                self._send_json(404, {"error": "接口不存在"})
                return
            self._send_json(200, result)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    def log_message(self, pattern: str, *args: Any) -> None:
        print("[loomq-web] " + pattern % args)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the LoomQ beginner web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    load_optional_env_file()
    server = ThreadingHTTPServer((args.host, args.port), LoomQHandler)
    print(f"LoomQ is ready at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
