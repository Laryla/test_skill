"""测试 GLM-5 思考内容 - 使用原生 OpenAI 客户端"""

from openai import OpenAI

client = OpenAI(
    api_key="dacc5506fc9a5469d9cf80309b1ef300.FFswKpmbcQUQmgO5",
    base_url="https://open.bigmodel.cn/api/paas/v4/",
)

print("=== 测试 GLM-5 思考内容 ===\n")

response = client.chat.completions.create(
    model="glm-5",
    messages=[{"role": "user", "content": "你好，请介绍一下你自己"}],
    stream=True,
    extra_body={
        "thinking": {
            "type": "enabled",
            "clear_thinking": False
        }
    }
)

reasoning = ""
content = ""

print("开始流式接收...")
for chunk in response:
    delta = chunk.choices[0].delta

    # 检查思考内容
    if hasattr(delta, "reasoning_content") and delta.reasoning_content:
        reasoning += delta.reasoning_content
        print(f"{delta.reasoning_content}", end="", flush=True)

    # 检查普通内容
    if hasattr(delta, "content") and delta.content:
        content += delta.content
        print(delta.content, end="", flush=True)

print("\n\n=== 总结 ===")
print(f"思考内容长度: {len(reasoning)}")
print(f"普通内容长度: {len(content)}")

if reasoning:
    print(f"\n完整思考内容:\n{reasoning}")
