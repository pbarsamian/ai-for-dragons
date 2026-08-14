"""
ai-for-dragons — async MCP stdio server for DragonOS / HackRF One
Transport: stdio  |  Protocol: MCP 2024-11-05

Tool calls run in a thread pool so the stdin reader loop stays live
during long-running tools (hackrf_sweep, hackrf_capture, adsb_scan).
Ping/keepalive messages are answered immediately even while a tool runs.
"""

import asyncio
import concurrent.futures
import json
import logging
import sys
import threading
from typing import Any

from .tools import TOOL_REGISTRY, execute_tool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ai-for-dragons] %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("ai-for-dragons")

_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="sdr-tool"
)
_stdout_lock = threading.Lock()

# MCP-level cap per tool call. Subprocess timeouts inside each tool fire first;
# this only triggers if a tool hangs past its own internal timeout.
TOOL_TIMEOUT = 90


def _write(data: bytes) -> None:
    with _stdout_lock:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()


def _msg(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode()

def _ok(req_id: Any, result: dict) -> bytes:
    return _msg({"jsonrpc": "2.0", "id": req_id, "result": result})

def _err(req_id: Any, code: int, message: str, data: Any = None) -> bytes:
    e = {"code": code, "message": message}
    if data is not None:
        e["data"] = data
    return _msg({"jsonrpc": "2.0", "id": req_id, "error": e})


# ── Synchronous handlers — respond instantly from the event loop ───────────

def _handle_initialize(req_id: Any, params: dict) -> bytes:
    return _ok(req_id, {
        "protocolVersion": "2024-11-05",
        "serverInfo": {
            "name": "ai-for-dragons",
            "version": "0.3.0",
            "description": "DragonOS SDR tool bridge for Ollama and MCP-compatible clients",
        },
        "capabilities": {"tools": {}},
    })


def _handle_tools_list(req_id: Any, _params: dict) -> bytes:
    return _ok(req_id, {
        "tools": [
            {"name": n, "description": s["description"], "inputSchema": s["schema"]}
            for n, s in TOOL_REGISTRY.items()
        ]
    })


def _handle_ping(req_id: Any, _params: dict) -> bytes:
    return _ok(req_id, {})


SYNC_HANDLERS: dict[str, Any] = {
    "initialize":                _handle_initialize,
    "tools/list":                _handle_tools_list,
    "ping":                      _handle_ping,
    "notifications/initialized": None,  # notification — no reply needed
}


# ── Async tool dispatch ────────────────────────────────────────────────────

async def _dispatch_tool(req_id: Any, tool_name: str, arguments: dict) -> None:
    if tool_name not in TOOL_REGISTRY:
        _write(_err(req_id, -32601, f"Tool not found: {tool_name}"))
        return

    loop = asyncio.get_event_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(_executor, execute_tool, tool_name, arguments),
            timeout=TOOL_TIMEOUT,
        )
        _write(_ok(req_id, {
            "content": [{"type": "text", "text": result}],
            "isError": False,
        }))
    except asyncio.TimeoutError:
        log.warning("Tool %s timed out after %ds", tool_name, TOOL_TIMEOUT)
        _write(_ok(req_id, {
            "content": [{"type": "text", "text": (
                f"[TIMEOUT] {tool_name} exceeded {TOOL_TIMEOUT}s. "
                "If HackRF is held by GQRX, click the stop button (■) then retry."
            )}],
            "isError": True,
        }))
    except Exception as exc:
        log.exception("Tool error in %s", tool_name)
        _write(_ok(req_id, {
            "content": [{"type": "text", "text": f"[ERROR] {exc}"}],
            "isError": True,
        }))


# ── Main async stdio loop ──────────────────────────────────────────────────

async def _main() -> None:
    log.info("ai-for-dragons v0.3 ready — async, %d worker threads", _executor._max_workers)

    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader),
        sys.stdin.buffer,
    )

    in_flight: set[asyncio.Task] = set()

    while True:
        try:
            line = await reader.readline()
        except Exception:
            break
        if not line:
            break

        raw = line.strip()
        if not raw:
            continue

        try:
            req = json.loads(raw)
        except json.JSONDecodeError as exc:
            _write(_err(None, -32700, f"Parse error: {exc}"))
            continue

        req_id = req.get("id")
        method  = req.get("method", "")
        params  = req.get("params", {})

        if method == "tools/call":
            # Fire into thread pool — doesn't block the reader loop
            task = asyncio.create_task(
                _dispatch_tool(
                    req_id,
                    params.get("name", ""),
                    params.get("arguments", {}),
                )
            )
            in_flight.add(task)
            task.add_done_callback(in_flight.discard)
            continue

        handler = SYNC_HANDLERS.get(method)
        if handler is None:
            if req_id is not None:
                _write(_err(req_id, -32601, f"Method not found: {method}"))
            continue

        resp = handler(req_id, params)
        if resp is not None:
            _write(resp)

    # Drain in-flight tool calls before exit
    if in_flight:
        await asyncio.gather(*in_flight, return_exceptions=True)
    _executor.shutdown(wait=False)


def run() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    run()
