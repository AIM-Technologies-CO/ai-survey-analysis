"""Smoke test for the /mcp/ endpoint — no interactive Claude client needed.

    cd src && MCP_AUTH_TOKEN=test123 uv run uvicorn server:app --port 8766   # terminal 1
    MCP_AUTH_TOKEN=test123 uv run scripts/mcp_smoke.py [--llm]               # terminal 2

Checks the initialize handshake, the tool list (names + annotations), and the
read-only Mongo-backed tools. --llm additionally calls suggest_questions (one
real LLM call — set ANTHROPIC_MODEL=claude-haiku-4-5 on the server to keep it cheap).
"""

import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

BASE = os.environ.get("MCP_URL", "http://localhost:8766/mcp/")
TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")

EXPECTED_TOOLS = {
    "list_surveys": True,
    "get_survey_questions": True,
    "count_eligible": True,
    "suggest_questions": True,
    "ask_one_respondent": True,
    "preview_synthetic_answers": True,
    "generate_all": False,  # name -> expected readOnlyHint
    "get_job_status": True,
}


def show(label: str, payload) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(payload, indent=2, default=str)[:1500])


async def main() -> None:
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    async with streamablehttp_client(BASE, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"connected: {init.serverInfo.name} (protocol {init.protocolVersion})")

            tools = (await session.list_tools()).tools
            by_name = {t.name: t for t in tools}
            missing = set(EXPECTED_TOOLS) - set(by_name)
            assert not missing, f"missing tools: {missing}"
            for name, want_ro in EXPECTED_TOOLS.items():
                got = by_name[name].annotations.readOnlyHint
                assert got == want_ro, f"{name}: readOnlyHint={got}, expected {want_ro}"
            print(f"tools ok: {sorted(by_name)}")

            res = await session.call_tool("list_surveys", {"limit": 3})
            assert not res.isError, res.content
            surveys = res.structuredContent["surveys"]
            show("list_surveys", surveys)
            if not surveys:
                print("no surveys in DB — stopping after connectivity checks")
                return
            sid = surveys[0]["id"]

            res = await session.call_tool("get_survey_questions", {"survey_id": sid})
            assert not res.isError, res.content
            sc = res.structuredContent
            show("get_survey_questions", {"n_questions": len(sc["questions"]),
                                          "bounds": sc["submit_date_bounds"]})

            res = await session.call_tool("count_eligible", {"survey_id": sid})
            assert not res.isError, res.content
            show("count_eligible", res.structuredContent)

            res = await session.call_tool("get_job_status", {"job_id": "nope"})
            assert res.isError, "expected error for unknown job"
            print("\nunknown-job error ok:", res.content[0].text)

            if "--llm" in sys.argv:
                res = await session.call_tool("suggest_questions", {"survey_id": sid, "n": 2})
                assert not res.isError, res.content
                show("suggest_questions", res.structuredContent)

    print("\nSMOKE OK")


if __name__ == "__main__":
    asyncio.run(main())
