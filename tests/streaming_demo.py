"""流式返回示例 - 展示工具调用的开始、结束和入参，以及AI文本增量。"""

import asyncio
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langchain_litellm import ChatLiteLLM

def get_weather(city: str) -> str:
    """获取指定城市的天气信息。"""
    return f"{city}今天天气晴朗，温度25°C"


def search_database(query: str) -> str:
    """搜索数据库。"""
    return f"找到关于'{query}'的3条结果"


# 初始化模型
model = ChatLiteLLM(
    model="openai/glm-5",
    api_base="https://open.bigmodel.cn/api/paas/v4/",
    api_key="dacc5506fc9a5469d9cf80309b1ef300.FFswKpmbcQUQmgO5",
    streaming=True
)

# # 测试流式响应，查看原始数据
# print("=== 测试流式响应 ===")
# print(model.invoke("你好"))



# 创建 Agent
agent = create_agent(
    model,
    tools=[],
)


async def main():
    """演示流式输出工具调用信息。"""

    print("🚀 流式输出演示")
    print("=" * 60)

    # 用于追踪当前工具调用的状态
    current_tool_calls = {}

    # 流式处理
    async for chunk in agent.astream(
        {"messages": [HumanMessage(content="你思考一下，你好啊")]},
        stream_mode=["messages", "updates"],
        version="v2"
    ):
        chunk_type = chunk.get("type")

        # 处理 token 消息流（实时显示生成的文本和工具调用增量）
        if chunk_type == "messages":
            token, metadata = chunk["data"]
            node_name = metadata.get("langgraph_node", "unknown")

            # 1. 显示 AI 生成的文本增量
            if hasattr(token, 'text') and token.text:
                print(token.text, end="", flush=True)

            # 1.5. 显示思考内容增量（reasoning tokens）
            if hasattr(token, 'content_blocks'):
                for block in token.content_blocks:
                    if isinstance(block, dict) and block.get("type") == "reasoning":
                        reasoning_text = block.get("reasoning", "")
                        if reasoning_text:
                            print(f"\n🤔 [思考] {reasoning_text}", end="", flush=True)

            # 2. 显示工具调用的增量信息
            if hasattr(token, 'tool_call_chunks') and token.tool_call_chunks:
                for tc in token.tool_call_chunks:
                    tool_id = tc.get("id")
                    tool_name = tc.get("name")
                    args = tc.get("args", "")

                    # 工具调用开始（有工具名称）
                    if tool_name and tool_id not in current_tool_calls:
                        current_tool_calls[tool_id] = {"name": tool_name, "args": ""}
                        print(f"\n🔧 [开始] 工具: {tool_name}")

                    # 累积参数
                    if tool_id and tool_id in current_tool_calls:
                        current_tool_calls[tool_id]["args"] += args

        # 处理状态更新（完整的工具调用信息）
        elif chunk_type == "updates":
            for data in chunk["data"].values():
                messages = data.get("messages", [])
                for msg in messages:
                    # 4. 显示完整的工具调用信息（确认完成）
                    if msg.type == "ai" and hasattr(msg, 'tool_calls') and msg.tool_calls:
                        for call in msg.tool_calls:
                            tool_id = call.get("id")
                            tool_name = call.get("name")
                            tool_args = call.get("args", {})

                            print(f"\n✅ [完成] 工具: {tool_name}")
                            print(f"   📦 完整入参: {tool_args}")

                            # 清理已完成的工具调用
                            if tool_id in current_tool_calls:
                                del current_tool_calls[tool_id]

                    # 4. 显示工具返回结果
                    elif msg.type == "tool":
                        tool_name = msg.name
                        print(f"\n📤 [结果] 工具 {tool_name} 返回:")
                        if isinstance(msg.content, str):
                            print(f"   {msg.content}")
                        elif isinstance(msg.content, list):
                            for block in msg.content:
                                if isinstance(block, dict):
                                    if block.get("type") == "text":
                                        print(f"   {block.get('text', '')}")

    # print("\n🤔 使用 astream_events 监听思考内容...\n")

    # async for event in agent.astream_events(
    #     {"messages": [HumanMessage(content="你思考一下，你好啊")]},
    #     version="v2"
    # ):
    #     event_type = event.get("event")
    #     event_name = event.get("name", "")

    #     # 监听模型流式输出
    #     if event_type == "on_chat_model_stream":
            # data = event.get("data", {})
            # chunk = data.get("chunk")

            # if not chunk:
            #     continue

            # # 普通文本内容
            # if hasattr(chunk, 'content') and chunk.content:
            #     print(chunk.content, end="", flush=True)

            # # 思考内容 - 尝试多个可能的字段
            # if hasattr(chunk, 'additional_kwargs'):
            #     additional = chunk.additional_kwargs

            #     # GLM 可能返回的字段
            #     if "reasoning_content" in additional:
            #         print(f"\n🤔 [思考] {additional['reasoning_content']}", end="", flush=True)

            #     if "thinking" in additional:
            #         print(f"\n🤔 [思考] {additional['thinking']}", end="", flush=True)

            # # 检查 response_metadata
            # if hasattr(chunk, 'response_metadata'):
            #     metadata = chunk.response_metadata
            #     # 可能包含 reasoning_tokens 等信息
            #     if hasattr(chunk, 'content'):
            #         # 完整输出调试信息
            #         if event_name == "ChatLiteLLM":
            #             print(f"\n[DEBUG] Metadata: {metadata}", end="")
    print("\n" + "=" * 60)
    print("✅ 流式输出完成")


if __name__ == "__main__":
    asyncio.run(main())
