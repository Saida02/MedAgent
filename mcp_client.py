"""
Thin synchronous wrapper around the MCP Python SDK that talks to the MCP
servers already configured in .agents/mcp_config.json (gmail-mcp,
google-maps, google-sheets). Other tools call `call_tool(server, tool, args)`
and get back the plain result content; they fall back to direct APIs if the
MCP server is unavailable (e.g. not authenticated in this environment).
"""
import asyncio
import json
import os
import shutil
import sys
import threading
from functools import lru_cache

from config import MCP_CONFIG_PATH


class MCPUnavailableError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _load_server_configs():
    if not MCP_CONFIG_PATH.exists():
        return {}
    with open(MCP_CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("mcpServers", {})


def _resolve_command(command: str, args: list) -> tuple[str, list]:
    """
    On Windows, tools like npx/npm/uvx are `.cmd` batch-file shims, not real
    executables -- launching them without going through cmd.exe fails with
    WinError 2 ("cannot find the file specified") even though `where npx`
    finds them fine. Route through `cmd.exe /c` when the resolved binary is
    a batch file so MCP servers configured with a bare command name (as in
    .agents/mcp_config.json) actually start.
    """
    if sys.platform != "win32":
        return command, args
    resolved = shutil.which(command)
    if resolved and resolved.lower().endswith((".cmd", ".bat")):
        return "cmd.exe", ["/c", resolved, *args]
    return command, args


async def _call_tool_async(server_name: str, tool_name: str, arguments: dict, timeout: float):
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:
        raise MCPUnavailableError(f"mcp package not installed: {exc}") from exc

    servers = _load_server_configs()
    cfg = servers.get(server_name)
    if not cfg:
        raise MCPUnavailableError(f"No MCP server named '{server_name}' in mcp_config.json")

    command, args = _resolve_command(cfg["command"], cfg.get("args", []))
    # Merge onto the current environment rather than replacing it -- a bare
    # override (e.g. just {"GOOGLE_MAPS_API_KEY": "..."}) strips essential
    # Windows variables like SystemRoot/PATH, which crashes node.exe's
    # crypto init (ncrypto::CSPRNG) before it can even start the server.
    merged_env = {**os.environ, **(cfg.get("env") or {})}
    params = StdioServerParameters(
        command=command,
        args=args,
        env=merged_env,
    )

    async def _run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                if getattr(result, "isError", False):
                    raise MCPUnavailableError(f"{server_name}.{tool_name} returned an error: {result}")
                texts = []
                for block in result.content:
                    text = getattr(block, "text", None)
                    if text is not None:
                        texts.append(text)
                return "\n".join(texts) if texts else str(result.content)

    return await asyncio.wait_for(_run(), timeout=timeout)


def call_tool(server_name: str, tool_name: str, arguments: dict, timeout: float = 20.0) -> str:
    """
    Runs an MCP tool call synchronously, isolating it in its own event loop
    so it can be invoked safely from Flask's sync request handlers.
    Raises MCPUnavailableError if the server/tool can't be reached in time
    (missing auth, npx not resolvable, etc.) -- callers should catch this
    and fall back to a direct API implementation.
    """
    result_box = {}

    def _runner():
        try:
            result_box["value"] = asyncio.run(
                _call_tool_async(server_name, tool_name, arguments, timeout)
            )
        except Exception as exc:  # noqa: BLE001 - surface as MCPUnavailableError
            result_box["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join(timeout=timeout + 5)

    if thread.is_alive():
        raise MCPUnavailableError(f"{server_name}.{tool_name} timed out")
    if "error" in result_box:
        err = result_box["error"]
        if isinstance(err, MCPUnavailableError):
            raise err
        raise MCPUnavailableError(str(err))
    return result_box.get("value", "")
