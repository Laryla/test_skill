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
        agent = create_agent(model, tools=TOOLS,middleware=[ThreadDataMiddleware(), SandboxMiddleware(),SkillsMiddleware()])
        from langchain_core.runnables import RunnableConfig
        config_with_plan_mode = RunnableConfig(
            configurable={
                "thread_id": "example-thread",
                "user_id": "example-user",
            }
        )

        # 方式1：传递消息和配置
        result = agent.invoke(
            {"messages": [HumanMessage(content="你好，你是谁？")]},
            config=config_with_plan_mode
        )
        # 打印所有消息，包括中间步骤
        for msg in result["messages"]:
            print(f"\n[{msg.type}]: {msg.content}")


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
