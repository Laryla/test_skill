"""快速运行测试的脚本 - 测试大模型和 Docker 沙箱工具。"""

import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

# 导入模型
from langchain.chat_models import init_chat_model
from litellm import Thread
from agents.middleware.memory_middleware import MemoryMiddleware
from langchain_litellm import ChatLiteLLM
from agents.middleware.skills_middleware import SkillsMiddleware
from agents.sandbox.middleware import SandboxMiddleware
# from deepagents.middleware.skills import SkillsMiddleware

# 加载环境变量
load_dotenv()

# 初始化模型
model = ChatLiteLLM(
    model="openai/glm-5",
    api_key=os.getenv("GLM_API_KEY"),
    api_base=os.getenv("GLM_API_BASE"),
    streaming=True
)

# 导入 tools
from agents.middleware.thread_data_middleware import ThreadDataMiddleware
from agents.sandbox.tools import (
    bash_tool,
    ls_tool,
    glob_tool,
    grep_tool,
    read_file_tool,
    write_file_tool,
    str_replace_tool,
)

TOOLS = [bash_tool, ls_tool, glob_tool, grep_tool, read_file_tool, write_file_tool, str_replace_tool]

agent = create_agent(model, tools=TOOLS,middleware=[ThreadDataMiddleware(), SandboxMiddleware(),SkillsMiddleware()])

async def main():
    """主函数。"""
    print("\n🚀 Docker 沙箱 + 大模型测试套件")
    print("=" * 70)

    print("\n测试内容:")
    print("  1. 大模型多轮对话（不保存历史）")
    print("  2. 带上下文的多轮对话")
    print("  3. Agent 使用工具")
    print("  4. 基本工具功能")

    print("\n⚠️  注意:")
    print("  - 对话不保存历史信息")
    print("  - 每轮对话都是独立的")
    print("  - Agent 不使用 middleware（避免类型错误）")

    print("\n" + "=" * 70)

    try:
        print("🚀 测试开始...")
        agent = create_agent(model, tools=TOOLS,middleware=[ThreadDataMiddleware(), SandboxMiddleware(),SkillsMiddleware(user_id="example-user")
                                                            ,MemoryMiddleware(agent_name="example-agent")])
        from langchain_core.runnables import RunnableConfig
        config_with_plan_mode = RunnableConfig(
            configurable={
                "thread_id": "example-thread",
                "user_id": "example-user",
            }
        )

        # 方式1：传递消息和配置 - 使用流式返回
        print("\n📡 开始流式输出...\n")

        # 用于追踪当前工具调用的状态
        current_tool_calls = {}

        for chunk in agent.stream(
            {"messages": [HumanMessage(content="你好，介绍一下你自己")]},
            config=config_with_plan_mode,
            stream_mode=["updates", "messages"],
            version='v2'
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
                                print(f"思考：{reasoning_text}", end="", flush=True)

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
                for step, data in chunk.get("data", {}).items():
                    # 跳过 None 值
                    if data is None:
                        continue

                    print(f"\n🔹 节点: {step}")
                    print("=" * 50)

                    # data 可能包含多种类型的输出
                    for key, value in data.items():
                        if key == "messages":
                            # 处理消息列表
                            for msg in value:
                                print(f"\n  [{msg.type}]")

                                # 3. 显示 AI 消息中的思考内容（完整版）
                                if msg.type == "ai" and hasattr(msg, 'content'):
                                    if isinstance(msg.content, list):
                                        for block in msg.content:
                                            if isinstance(block, dict) and block.get("type") == "reasoning":
                                                print(f"\n🤔 [完整思考] {block.get('reasoning', '')}")

                                if hasattr(msg, 'content'):
                                    if isinstance(msg.content, list):
                                        # 处理内容块（如工具调用）
                                        for block in msg.content:
                                            if hasattr(block, 'text'):
                                                print(f"    📝 {block.text}")
                                            elif hasattr(block, 'tool_calls'):
                                                for call in block.tool_calls:
                                                    print(f"    🔧 工具调用: {call.get('name', 'unknown')}")
                                                    print(f"       参数: {call.get('args', {})}")
                                    else:
                                        print(f"    📝 {msg.content}")
                                if hasattr(msg, 'name') and msg.name:
                                    print(f"    工具名: {msg.name}")

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

                                # 5. 显示工具返回结果
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

                        elif key == "thread_data":
                            # 处理线程数据（工作空间路径等）
                            print(f"  📁 工作空间信息:")
                            for k, v in value.items():
                                print(f"    {k}: {v}")
                        else:
                            # 其他数据
                            print(f"  {key}: {value}")

                    print("=" * 50)


    except KeyboardInterrupt:
        print("\n\n⏸️  测试被用户中断")
    except Exception as e:
        print(f"\n\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
