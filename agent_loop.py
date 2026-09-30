"""Agent loop: Claude on Bedrock, calling MCP tools through agentgateway with an Okta access token.

No Flask, no Okta logic here: this module receives a ready access token and a question.
Keeping it separate means it can later move to AgentCore Runtime unchanged.
"""
import boto3
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

MODEL_ID = "us.anthropic.claude-sonnet-5"
MCP_URL = "http://127.0.0.1:3000/mcp"   # agentgateway on EC2, through the SSH tunnel
MAX_TURNS = 8                           # safety stop for the tool loop

bedrock = boto3.Session(profile_name="default").client("bedrock-runtime", region_name="us-east-1")


def to_bedrock_tools(mcp_tools) -> dict:
    """Translate MCP tool definitions into Bedrock's toolConfig format."""
    return {"tools": [
        {"toolSpec": {
            "name": t.name,
            "description": t.description or t.name,
            "inputSchema": {"json": t.input_schema},
        }}
        for t in mcp_tools
    ]}


def to_tool_result(tool_use_id: str, result) -> dict:
    """Translate an MCP tool result into a Bedrock toolResult block."""
    if result.structured_content is not None:
        content = [{"json": result.structured_content}]
    else:
        text = "\n".join(b.text for b in result.content if isinstance(b, TextContent))
        content = [{"text": text or "(no content)"}]
    block = {"toolUseId": tool_use_id, "content": content}
    if result.is_error:
        block["status"] = "error"
    return {"toolResult": block}


async def run_agent(question: str, access_token: str) -> dict:
    """Answer one question. Returns the answer, the tools the gateway exposed, and a trace."""
    trace = []
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {access_token}"},   # sent on every MCP request
        timeout=httpx2.Timeout(30.0, read=300.0),
    ) as http_client:
        async with Client(streamable_http_client(MCP_URL, http_client=http_client)) as mcp:
            listed = await mcp.list_tools()
            tool_names = [t.name for t in listed.tools]
            trace.append(f"MCP tools/list -> {tool_names}")

            messages = [{"role": "user", "content": [{"text": question}]}]
            request = {"modelId": MODEL_ID}
            if listed.tools:
                request["toolConfig"] = to_bedrock_tools(listed.tools)

            answer = "(no answer)"
            for turn in range(1, MAX_TURNS + 1):
                resp = bedrock.converse(**request, messages=messages)
                msg = resp["output"]["message"]
                messages.append(msg)
                trace.append(f"Bedrock turn {turn}: stopReason={resp['stopReason']}, "
                              f"tokens in/out={resp['usage']['inputTokens']}/{resp['usage']['outputTokens']}")

                if resp["stopReason"] != "tool_use":
                    answer = "\n".join(b["text"] for b in msg["content"] if "text" in b)
                    break

                results = []
                for block in msg["content"]:
                    if "toolUse" in block:
                        call = block["toolUse"]
                        result = await mcp.call_tool(call["name"], call["input"])
                        trace.append(f"MCP tools/call {call['name']}({call['input']}) -> "
                                     f"{'ERROR' if result.is_error else 'ok'}")
                        results.append(to_tool_result(call["toolUseId"], result))
                messages.append({"role": "user", "content": results})
            else:
                trace.append(f"Stopped after {MAX_TURNS} turns without a final answer")

    return {"answer": answer, "tools": tool_names, "trace": trace}
