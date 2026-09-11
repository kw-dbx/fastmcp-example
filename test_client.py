"""Smoke test for the deployed OBO SQL MCP server.

Usage:
    export MCP_URL="https://<app-url>/mcp"
    export DBX_TOKEN=$(databricks auth token --profile <PROFILE> | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
    python test_client.py
"""
import asyncio
import os

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

URL = os.environ["MCP_URL"]
TOKEN = os.environ["DBX_TOKEN"]


async def main():
    transport = StreamableHttpTransport(URL, headers={"Authorization": f"Bearer {TOKEN}"})
    async with Client(transport) as client:
        tools = await client.list_tools()
        print("TOOLS:", [t.name for t in tools])

        res = await client.call_tool(
            "execute_sql",
            {"query": "SELECT current_user() AS me, current_catalog() AS cat"},
        )
        print("RESULT:", res.data)


if __name__ == "__main__":
    asyncio.run(main())
