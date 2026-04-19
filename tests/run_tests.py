"""快速运行测试的脚本 - 测试大模型和 Docker 沙箱工具。"""

import asyncio
import sys
from pathlib import Path
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

# 导入模型
from langchain.chat_models import init_chat_model
from litellm import Thread

from agents.middleware.skills_middleware import SkillsMiddleware
from agents.sandbox.middleware import SandboxMiddleware
# from deepagents.middleware.skills import SkillsMiddleware
# 初始化模型
model = init_chat_model(
    model="glm-5",
    model_provider="openai",
    api_key="dacc5506fc9a5469d9cf80309b1ef300.FFswKpmbcQUQmgO5",
    base_url="https://open.bigmodel.cn/api/paas/v4/",
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
        agent = create_agent(model, tools=TOOLS,middleware=[ThreadDataMiddleware(), SandboxMiddleware(),SkillsMiddleware(user_id="example-user")])
        from langchain_core.runnables import RunnableConfig
        config_with_plan_mode = RunnableConfig(
            configurable={
                "thread_id": "example-thread",
                "user_id": "example-user",
            }
        )

        # 方式1：传递消息和配置 - 使用流式返回
        print("\n📡 开始流式输出...\n")

        for chunk in agent.stream(
            {"messages": [HumanMessage(content="你好，使用save-as-txt 帮我保存为txt：用户隔离：每个用户只能看到自己的技能目录呜呜呜呜")]},
            config=config_with_plan_mode,
            stream_mode="updates",
            version='v2'
        ):
            # 处理消息输出
            if chunk.get("type") == "updates":
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
                                if hasattr(msg, 'content'):
                                    if isinstance(msg.content, list):
                                        # 处理内容块（如工具调用）
                                        for block in msg.content:
                                            if hasattr(block, 'text'):
                                                print(f"    📝 {block.text}")
                                            elif hasattr(block, 'tool_calls'):
                                                for call in block.tool_calls:
                                                    print(f"    🔧 工具调用: {call.get('name', 'unknown')}")
                                                    print(f"       参数: {call.get('args', {{}})}")
                                    else:
                                        print(f"    📝 {msg.content}")
                                if hasattr(msg, 'name') and msg.name:
                                    print(f"    工具名: {msg.name}")
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
