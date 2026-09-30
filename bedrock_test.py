import boto3

session = boto3.Session(profile_name="default")
bedrock = session.client("bedrock-runtime", region_name="us-east-1")

resp = bedrock.converse(
    modelId="us.anthropic.claude-sonnet-5",
    messages=[{"role": "user", "content": [{"text": "Say hello in five words."}]}],
)

print(resp["output"]["message"]["content"][0]["text"])