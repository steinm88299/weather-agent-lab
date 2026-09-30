import boto3
from weather_api import get_current_weather

MODEL_ID = "us.anthropic.claude-sonnet-5"

session = boto3.Session(profile_name="default")
bedrock = session.client("bedrock-runtime", region_name="us-east-1")

# What Claude is told about the tool: a name, a description, and the inputs
TOOLS = {
    "tools": [
        {
            "toolSpec": {
                "name": "get_current_weather",
                "description": "Get the current weather for a city. "
                               "Use 'City,CountryCode' format, e.g. 'Denver,US'.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string", "description": "e.g. 'Denver,US'"}
                        },
                        "required": ["city"],
                    }
                },
            }
        }
    ]
}


# Runs the Python function Claude asked for, and trims the result
def run_tool(name, args):
    if name == "get_current_weather":
        data = get_current_weather(args["city"])
        return {
            "city": data["name"],
            "temp_f": data["main"]["temp"],
            "humidity": data["main"]["humidity"],
            "conditions": data["weather"][0]["description"],
            "wind_mph": data["wind"]["speed"],
        }
    raise ValueError(f"Unknown tool: {name}")


def ask(question):
    messages = [{"role": "user", "content": [{"text": question}]}]

    while True:
        resp = bedrock.converse(modelId=MODEL_ID, messages=messages, toolConfig=TOOLS)
        msg = resp["output"]["message"]
        messages.append(msg)

        # Claude is finished and has answered
        if resp["stopReason"] != "tool_use":
            break

        # Claude asked for one or more tool calls: run them and send back results
        results = []
        for block in msg["content"]:
            if "toolUse" in block:
                call = block["toolUse"]
                print(f"[tool call] {call['name']}({call['input']})")
                try:
                    result = run_tool(call["name"], call["input"])
                    results.append({"toolResult": {
                        "toolUseId": call["toolUseId"],
                        "content": [{"json": result}],
                    }})
                except Exception as e:
                    results.append({"toolResult": {
                        "toolUseId": call["toolUseId"],
                        "content": [{"text": str(e)}],
                        "status": "error",
                    }})
        messages.append({"role": "user", "content": results})

    for block in msg["content"]:
        if "text" in block:
            print(block["text"])


if __name__ == "__main__":
    ask("Should I bring sunglasses if I'm going from Denver to Chicago today?")