"""MCP-Client als Inspector-Ersatz — Rohbeleg fuer die Abnahme (WP-8).

Verbindet sich per Streamable HTTP mit einem laufenden MCP-Server, gibt
``initialize`` und ``tools/list`` mit vollem ``inputSchema`` aus und ruft die
8 Tools auf. Standardlauf: Task-Lifecycle (create/update/delete mit
Bestaetigung) und Display-Steuerung.

Aufruf:
    MCP_URL=http://127.0.0.1:8001/mcp python tests/inspect_client.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8001/mcp")


def show(title: str, payload: Any) -> None:
    print(f"\n===== {title} =====")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


async def main() -> int:
    async with streamable_http_client(MCP_URL) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            show(
                "initialize",
                {
                    "server": init.server_info.name,
                    "version": init.server_info.version,
                    "protocolVersion": init.protocol_version,
                },
            )

            listing = await session.list_tools()
            print(f"\n===== tools/list ({len(listing.tools)}) =====")
            for tool in listing.tools:
                print(f"- {tool.name}: {tool.description.splitlines()[0]}")
                print(f"  inputSchema: {json.dumps(tool.input_schema, ensure_ascii=False)}")

            async def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
                result = await session.call_tool(name, arguments)
                text = result.content[0].text if result.content else ""
                print(f"\n----- tools/call {name} {json.dumps(arguments, ensure_ascii=False)}")
                print(text)
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"raw": text}

            show("dashboard_status", await call("dashboard_status", {}))
            show("task_list", await call("task_list", {"status": "all", "limit": 5}))

            created = await call(
                "task_create",
                {
                    "title": "Abnahme K3",
                    "description": "Round-trip-Test ueber Streamable HTTP",
                    "priority": "high",
                },
            )
            show("task_create -> task", created)
            task_id = (created.get("task") or {}).get("id")
            if not task_id:
                print("FEHLER: task_create hat keine id geliefert", file=sys.stderr)
                return 1

            show("task_update", await call("task_update", {"task_id": task_id, "status": "done"}))
            show(
                "task_delete ohne Bestaetigung",
                await call("task_delete", {"task_id": task_id, "confirm": False}),
            )
            show(
                "task_delete mit falschem expected_title",
                await call(
                    "task_delete",
                    {"task_id": task_id, "confirm": True, "expected_title": "etwas anderes"},
                ),
            )
            show(
                "task_delete mit Bestaetigung",
                await call(
                    "task_delete",
                    {"task_id": task_id, "confirm": True, "expected_title": "Abnahme K3"},
                ),
            )

            show("screen_off", await call("screen_off", {}))
            show("screen_on", await call("screen_on", {}))
            show("screen_dim", await call("screen_dim", {"brightness": 0.2}))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
