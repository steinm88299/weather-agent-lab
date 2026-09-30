import json

import anyio
import boto3
from mcp import Client
from mcp.types import TextContent

MODEL_ID = "us.anthropic.claude-sonnet-5"
MCP_URL = "http://127.0.0.1:3000/mcp"  # agentgateway on EC2, through the SSH tunnel
DEBUG = True

session = boto3.Session(profile_name="default")
bedrock = session.client("bedrock-runtime", region_name="us-east-1")


def dump(label, obj):
    if DEBUG:
        print(f"\n{'=' * 20} {label} {'=' * 20}")
        print(json.dumps(obj, indent=2, default=str))


# Translate MCP tool definitions into Bedrock's toolConfig format
def to_bedrock_tools(mcp_tools):
    return {
        "tools": [
            {
                "toolSpec": {
                    "name": t.name,
                    "description": t.description or t.name,
                    "inputSchema": {"json": t.input_schema},
                }
            }
            for t in mcp_tools
        ]
    }


# Translate an MCP tool result into a Bedrock toolResult block
def to_tool_result(tool_use_id, result):
    if result.structured_content is not None:
        content = [{"json": result.structured_content}]
    else:
        text = "\n".join(b.text for b in result.content if isinstance(b, TextContent))
        content = [{"text": text or "(no content)"}]

    tool_result = {"toolUseId": tool_use_id, "content": content}
    if result.is_error:
        tool_result["status"] = "error"
    return {"toolResult": tool_result}


async def ask(client, question):
    # Discovery: ask the MCP server what tools exist
    listed = await client.list_tools()
    tools = to_bedrock_tools(listed.tools)
    dump("TOOLS DISCOVERED FROM MCP (tools/list)", tools)

    messages = [{"role": "user", "content": [{"text": question}]}]
    turn = 0

    while True:
        turn += 1
        request = {"modelId": MODEL_ID, "messages": messages, "toolConfig": tools}
        dump(f"TURN {turn} -> REQUEST TO BEDROCK", request)

        resp = bedrock.converse(**request)

        dump(f"TURN {turn} <- RESPONSE FROM BEDROCK", {
            "stopReason": resp["stopReason"],
            "output": resp["output"],
            "usage": resp["usage"],
        })

        msg = resp["output"]["message"]
        messages.append(msg)

        if resp["stopReason"] != "tool_use":
            break

        # Execution: run each requested tool on the MCP server, through the gateway
        results = []
        for block in msg["content"]:
            if "toolUse" in block:
                call = block["toolUse"]
                print(f"\n[MCP tools/call] {call['name']}({call['input']})")
                result = await client.call_tool(call["name"], call["input"])
                dump("MCP RESULT", {
                    "is_error": result.is_error,
                    "structured_content": result.structured_content,
                })
                results.append(to_tool_result(call["toolUseId"], result))

        messages.append({"role": "user", "content": results})

    print("\n" + "=" * 20 + " FINAL ANSWER " + "=" * 20)
    for block in msg["content"]:
        if "text" in block:
            print(block["text"])


async def main():
    async with Client(MCP_URL) as client:
        name = client.server_info.name if client.server_info else "(not reported)"
        print(f"Connected to MCP server '{name}' at {MCP_URL}")
        print(f"Protocol version: {client.protocol_version}")
        await ask(client, "Should I bring a jacket if I'm going from Denver to Chicago today?")


if __name__ == "__main__":
    anyio.run(main)