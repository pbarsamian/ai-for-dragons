"""
ai-for-dragons — MCP stdio server for DragonOS / HackRF One
Exposes 18 SDR tools as callable tools for Claude Code or any MCP-compatible client.

Transport: stdio (default for Claude Code)
Protocol:  MCP 2024-11-05
"""

import asyncio
import json
import logging
import sys
from typing import Any

from .tools import TOOL_REGISTRY, execute_tool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ai-for-dragons] %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("ai-for-dragons")


# ── MCP wire protocol helpers ──────────────────────────────────────────────

def _msg(obj: dict) -> str:
    return json.dumps(obj) + "\n"

def _ok(req_id: Any, result: dict) -> str:
    return _msg({"jsonrpc": "2.0", "id": req_id, "result": result})

def _err(req_id: Any, code: int, message: str, data: Any = None) -> str:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return _msg({"jsonrpc": "2.0", "id": req_id, "error": err})


# ── Request handlers ───────────────────────────────────────────────────────

def handle_initialize(req_id: Any, params: dict) -> str:
    return _ok(req_id, {
        "protocolVersion": "2024-11-05",
        "serverInfo": {
            "name": "ai-for-dragons",
            "version": "0.2.0",
            "description": "DragonOS SDR tool bridge for Claude Code and local LLMs",
        },
        "capabilities": {"tools": {}},
    })


def handle_tools_list(req_id: Any, _params: dict) -> str:
    tools = []
    for name, spec in TOOL_REGISTRY.items():
        tools.append({
            "name": name,
            "description": spec["description"],
            "inputSchema": spec["schema"],
        })
    return _ok(req_id, {"tools": tools})


def handle_tools_call(req_id: Any, params: dict) -> str:
    tool_name = params.get("name", "")
    arguments  = params.get("arguments", {})

    if tool_name not in TOOL_REGISTRY:
        return _err(req_id, -32601, f"Tool not found: {tool_name}")

    try:
        result = execute_tool(tool_name, arguments)
        return _ok(req_id, {
            "content": [{"type": "text", "text": result}],
            "isError": False,
        })
    except Exception as exc:
        log.exception("Tool error: %s", tool_name)
        return _ok(req_id, {
            "content": [{"type": "text", "text": f"[ERROR] {exc}"}],
            "isError": True,
        })


# ── Main stdio loop ────────────────────────────────────────────────────────

def run() -> None:
    log.info("ai-for-dragons starting — waiting for MCP client on stdin")
    stdin  = sys.stdin
    stdout = sys.stdout

    HANDLERS = {
        "initialize":       handle_initialize,
        "tools/list":       handle_tools_list,
        "tools/call":       handle_tools_call,
        "notifications/initialized": lambda i, p: None,
    }

    for raw in stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError as e:
            sys.stdout.write(_err(None, -32700, f"Parse error: {e}"))
            sys.stdout.flush()
            continue

        req_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params", {})

        handler = HANDLERS.get(method)
        if handler is None:
            if req_id is not None:
                stdout.write(_err(req_id, -32601, f"Method not found: {method}"))
                stdout.flush()
            continue

        response = handler(req_id, params)
        if response is not None:
            stdout.write(response)
            stdout.flush()


if __name__ == "__main__":
    run()
