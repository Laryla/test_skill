"""Skills 中间件 - 加载和暴露技能到系统提示。

实现 Anthropic 的 agent skills 模式，使用渐进式披露，
从本地文件系统加载技能。

## 技能结构

每个技能是一个包含 SKILL.md 文件的目录：

/skills/skill-name/
├── SKILL.md          # 必需：YAML frontmatter + markdown 指令
└── scripts/          # 可选：辅助脚本

SKILL.md 格式：
---
name: skill-name
description: 技能的简短描述
license: MIT
---

# 技能标题

## 使用时机
- 用户请求特定任务时
...
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Generic

import yaml
from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime
from typing_extensions import NotRequired, override

from agents.sandbox.sandbox_provider import get_sandbox_provider

if TYPE_CHECKING:
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langgraph.typing import ContextT

logger = logging.getLogger(__name__)

# 最大技能文件大小（防止 DoS 攻击）
MAX_SKILL_FILE_SIZE = 10 * 1024 * 1024

# 沙箱内技能挂载路径
SANDBOX_SKILLS_PATH = "/mnt/skills"


class SkillMetadata:
    """技能元数据。"""

    def __init__(
        self,
        name: str,
        description: str,
        path: str,
        license: str | None = None,
        metadata: dict[str, str] | None = None,
    ):
        self.name = name
        self.description = description
        self.path = path
        self.license = license
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        """转换为字典。"""
        return {
            "name": self.name,
            "description": self.description,
            "path": self.path,
            "license": self.license,
            "metadata": self.metadata,
        }


class SkillsState(AgentState):
    """技能中间件的状态。"""

    skills_metadata: NotRequired[list[dict]]


class SkillsMiddleware(AgentMiddleware[SkillsState]):
    """加载和暴露技能到系统提示的中间件。

    从本地文件系统加载技能并使用渐进式披露注入到系统提示中。

    Example:
        ```python
        middleware = SkillsMiddleware(
            skills_dir="skills",
            sources=["global", "skill-creator"],
        )
        ```
    """

    state_schema = SkillsState

    # 系统提示模板
    SYSTEM_PROMPT_TEMPLATE = """

## 技能系统

你可以访问技能库，这提供了专业的能力和领域知识。

**可用技能：**

{skills_list}

**如何使用技能（渐进式披露）：**

技能遵循**渐进式披露**模式 - 你在上面看到它们的名称和描述，但只在需要时阅读完整指令：

1. **识别适用的技能**：检查用户的任务是否匹配技能的描述
2. **阅读技能的完整指令**：使用 `read_file` 读取上面技能列表中显示的路径。传递 `limit=1000`，因为默认的 100 行对大多数技能文件来说太少了
3. **遵循技能的指令**：SKILL.md 包含分步工作流程、最佳实践和示例
4. **访问辅助文件**：技能可能包括辅助脚本、配置或参考文档 - 使用绝对路径

**何时使用技能：**
- 用户的请求匹配技能的领域（例如，"研究 X" -> web-research 技能）
- 你需要专业知识或结构化工作流程
- 技能为复杂任务提供经过验证的模式

**执行技能脚本：**
技能可能包含 Python 脚本或其他可执行文件。始终使用技能列表中的绝对路径。

记住：技能让你更有能力和一致性。如有疑问，检查是否存在适用于任务的技能！


"""

    def __init__(self, skills_dir: str = "skills", user_id: str | None = None, sources: list[str] | None = None):
        """初始化技能中间件。

        Args:
            skills_dir: 技能根目录（用于本地回退）
            user_id: 用户ID，用于用户隔离的技能目录
            sources: 要加载的技能子目录列表，默认为全部
        """
        super().__init__()
        self._skills_dir = Path(skills_dir)
        self._sources = sources
        self._user_id = user_id
        self._skills_cache: list[SkillMetadata] | None = None

    def _load_skills(self, runtime: Runtime[ContextT] | None = None) -> list[SkillMetadata]:
        """从沙箱或文件系统加载技能。

        Args:
            runtime: 运行时上下文，用于获取沙箱实例

        Returns:
            技能元数据列表
        """
        if self._skills_cache is not None:
            return self._skills_cache

        skills: list[SkillMetadata] = []

        # 尝试从沙箱加载
        sandbox = self._get_sandbox(runtime)
        if sandbox is not None:
            skills = self._load_skills_from_sandbox(sandbox)
        else:
            # 回退到本地文件系统（使用用户目录）
            skills = self._load_skills_from_filesystem()

        self._skills_cache = skills
        logger.info("加载了 %d 个技能", len(skills))
        return skills

    def _get_sandbox(self, runtime: Runtime[ContextT] | None) -> object | None:
        """从运行时获取沙箱实例。

        Args:
            runtime: 运行时上下文

        Returns:
            沙箱实例，如果不可用则返回 None
        """
        if runtime is None or runtime.context is None:
            return None

        # context 可能是字典或其他类型，需要安全访问
        context = runtime.context
        try:
            if hasattr(context, 'get'):
                sandbox_id = context.get("sandbox_id")
            elif isinstance(context, dict):
                sandbox_id = context.get("sandbox_id")
            else:
                # 尝试作为属性访问
                sandbox_id = getattr(context, "sandbox_id", None)
        except (AttributeError, TypeError):
            return None

        if not sandbox_id:
            return None

        try:
            provider = get_sandbox_provider()
            return provider.get(sandbox_id)
        except Exception as e:
            logger.warning("无法获取沙箱 %s：%s", sandbox_id, e)
            return None

    def _load_skills_from_sandbox(self, sandbox) -> list[SkillMetadata]:
        """从沙箱加载技能。

        Args:
            sandbox: 沙箱实例

        Returns:
            技能元数据列表
        """
        skills: list[SkillMetadata] = []

        try:
            # 确定要扫描的目录
            if self._sources:
                scan_paths = [f"{SANDBOX_SKILLS_PATH}/{source}" for source in self._sources]
            else:
                # 列出所有子目录
                entries = sandbox.list_dir(SANDBOX_SKILLS_PATH, max_depth=1)
                scan_paths = [f"{SANDBOX_SKILLS_PATH}/{e}" for e in entries if e and not e.startswith('.')]

            # 扫描每个技能目录
            for skill_path in scan_paths:
                try:
                    skill_md_path = f"{skill_path}/SKILL.md"

                    # 读取 SKILL.md
                    content = sandbox.read_file(skill_md_path)

                    # 解析元数据
                    skill_metadata = self._parse_skill_metadata(
                        content=content,
                        skill_path=skill_md_path,
                        directory_name=skill_path.split('/')[-1],
                    )

                    if skill_metadata:
                        skills.append(skill_metadata)

                except Exception as e:
                    logger.warning("无法从沙箱读取技能 %s：%s", skill_path, e)
                    continue

        except Exception as e:
            logger.warning("从沙箱加载技能失败：%s", e)
            return []

        return skills

    def _load_skills_from_filesystem(self) -> list[SkillMetadata]:
        """从本地文件系统加载技能（回退方案）。

        Returns:
            技能元数据列表
        """
        skills: list[SkillMetadata] = []

        # 确定要扫描的基础目录（包含用户ID）
        if self._user_id:
            base_dir = self._skills_dir / self._user_id
        else:
            base_dir = self._skills_dir / "global"

        # 如果目录不存在，返回空列表
        if not base_dir.exists():
            logger.warning("本地技能目录不存在：%s", base_dir)
            return []

        # 确定要扫描的目录
        if self._sources:
            scan_dirs = [base_dir / source for source in self._sources]
        else:
            # 扫描所有子目录
            scan_dirs = [d for d in base_dir.iterdir() if d.is_dir()]

        # 扫描每个技能目录
        for skill_dir in scan_dirs:
            if not skill_dir.is_dir():
                continue

            skill_md_path = skill_dir / "SKILL.md"
            if not skill_md_path.exists():
                # 静默跳过没有 SKILL.md 的目录（可能是自动创建的空目录）
                continue

            # 读取 SKILL.md
            try:
                content = skill_md_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning("无法读取 %s：%s", skill_md_path, e)
                continue

            # 解析元数据（使用沙箱内路径格式）
            skill_metadata = self._parse_skill_metadata(
                content=content,
                skill_path=f"{SANDBOX_SKILLS_PATH}/{skill_dir.name}/SKILL.md",
                directory_name=skill_dir.name,
            )

            if skill_metadata:
                skills.append(skill_metadata)

        return skills

    def _parse_skill_metadata(
        self,
        content: str,
        skill_path: str,
        directory_name: str,
    ) -> SkillMetadata | None:
        """解析 SKILL.md 的 YAML frontmatter。

        Args:
            content: SKILL.md 的内容
            skill_path: SKILL.md 的路径
            directory_name: 父目录名

        Returns:
            SkillMetadata 如果解析成功，否则 None
        """
        if len(content) > MAX_SKILL_FILE_SIZE:
            logger.warning("跳过 %s：内容太大 (%d 字节)", skill_path, len(content))
            return None

        # 匹配 YAML frontmatter
        frontmatter_pattern = r"^---\s*\n(.*?)\n---\s*\n"
        match = re.match(frontmatter_pattern, content, re.DOTALL)

        if not match:
            logger.warning("跳过 %s：没有找到有效的 YAML frontmatter", skill_path)
            return None

        frontmatter_str = match.group(1)

        # 解析 YAML
        try:
            frontmatter_data = yaml.safe_load(frontmatter_str)
        except yaml.YAMLError as e:
            logger.warning("%s 中的 YAML 无效：%s", skill_path, e)
            return None

        if not isinstance(frontmatter_data, dict):
            logger.warning("跳过 %s：frontmatter 不是字典", skill_path)
            return None

        name = str(frontmatter_data.get("name", "")).strip()
        description = str(frontmatter_data.get("description", "")).strip()

        if not name or not description:
            logger.warning("跳过 %s：缺少必需的 'name' 或 'description'", skill_path)
            return None

        # 验证名称与目录名匹配
        if name != directory_name:
            logger.warning(
                "技能名称 '%s' 与目录名 '%s' 不匹配",
                name,
                directory_name,
            )

        return SkillMetadata(
            name=name,
            description=description,
            path=skill_path,
            license=str(frontmatter_data.get("license", "")).strip() or None,
            metadata=dict(frontmatter_data.get("metadata", {}) or {}),
        )

    def _format_skills_list(self, skills: list[SkillMetadata]) -> str:
        """格式化技能列表用于系统提示。"""
        if not skills:
            return f"(暂无可用技能。你可以在 {self._skills_dir} 中创建技能)"

        lines = []
        for skill in skills:
            desc_line = f"- **{skill.name}**: {skill.description}"
            if skill.license:
                desc_line += f" (License: {skill.license})"
            lines.append(desc_line)
            lines.append(f"  -> 使用 `read_file(path='{skill.path}', limit=1000)` 读取完整指令")

        return "\n".join(lines)

    def before_agent(self, state: SkillsState, runtime: Runtime[ContextT]) -> dict | None:
        """在 agent 执行前加载技能元数据。"""
        # 如果已经加载过，跳过
        if "skills_metadata" in state:
            return None

        skills = self._load_skills(runtime)
        return {
            "skills_metadata": [skill.to_dict() for skill in skills],
        }

    async def abefore_agent(self, state: SkillsState, runtime: Runtime[ContextT]) -> dict | None:
        """异步：在 agent 执行前加载技能元数据。"""
        # 如果已经加载过，跳过
        if "skills_metadata" in state:
            return None

        skills = self._load_skills(runtime)
        return {
            "skills_metadata": [skill.to_dict() for skill in skills],
        }

    def _append_to_system_message(self, system_message: SystemMessage | None, content: str) -> SystemMessage:
        """追加内容到系统消息。"""
        if system_message is None:
            return SystemMessage(content=content)

        # 处理 content 可能是列表的情况
        existing_content = system_message.content
        if isinstance(existing_content, list):
            # 如果是列表，保持列表格式并追加到文本块
            new_content = []
            text_appended = False
            for block in existing_content:
                if isinstance(block, dict) and block.get("type") == "text":
                    # 追加到现有文本块
                    new_content.append({
                        "type": "text",
                        "text": block.get("text", "") + content
                    })
                    text_appended = True
                else:
                    # 保留其他类型的块（如图片）
                    new_content.append(block)

            # 如果没有文本块，添加一个新的
            if not text_appended:
                new_content.append({"type": "text", "text": content})

            return SystemMessage(content=new_content)
        elif isinstance(existing_content, str):
            return SystemMessage(content=existing_content + content)
        else:
            return SystemMessage(content=content)

    def _is_valid_content_block(self, block: dict) -> bool:
        """验证 content 块是否有效且符合 OpenAI API 格式。

        OpenAI API 支持的格式：
        - {"type": "text", "text": "..."}
        - {"type": "image_url", "image_url": {...}}
        """
        if not isinstance(block, dict):
            return False

        block_type = block.get("type")
        if not block_type or not isinstance(block_type, str):
            return False

        if block_type == "text":
            # text 类型必须有 text 字段
            return "text" in block
        elif block_type == "image_url":
            # image_url 类型必须有 image_url 字段
            return "image_url" in block
        else:
            # 不支持的类型
            return False

    def _sanitize_message_content(self, content):
        """清理消息内容，移除不支持的content类型（如reasoning、thinking）。

        OpenAI API只支持text和image_url类型，需要过滤其他类型。
        """
        if isinstance(content, str):
            return content
        elif isinstance(content, list):
            sanitized = []
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type")

                    # 将reasoning转换为text（GLM-5 使用 reasoning）
                    if block_type == "reasoning":
                        reasoning_text = block.get("reasoning", "")
                        if reasoning_text:
                            sanitized.append({"type": "text", "text": f"[思考] {reasoning_text}"})

                    # thinking 类型的块直接跳过，不添加到 content 中
                    elif block_type == "thinking":
                        continue

                    # 只保留有效且支持的类型
                    elif self._is_valid_content_block(block):
                        sanitized.append(block)

                    # 其他类型（无效块）被丢弃
                else:
                    # 保留非字典类型的块
                    sanitized.append({"type":"text","text":block})

            # 如果过滤后没有有效块，返回空字符串
            return sanitized if sanitized else ""

        return content

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse],
    ) -> ModelResponse:
        """注入技能文档到系统提示。"""
        # 获取技能元数据
        skills_metadata = request.state.get("skills_metadata", [])
        skills_list = self._format_skills_list(
            [SkillMetadata(**meta) for meta in skills_metadata]
        )

        # 生成技能系统提示
        skills_section = self.SYSTEM_PROMPT_TEMPLATE.format(
            skills_list=skills_list,
        )

        # 追加到系统消息
        new_system_message = self._append_to_system_message(
            request.system_message,
            skills_section
        )

        # 清理所有消息的content，移除不支持的类型
        sanitized_messages = []
        for msg in request.messages:
            if hasattr(msg, 'content'):
                sanitized_content = self._sanitize_message_content(msg.content)
                # 如果content没有变化，直接使用原消息
                if sanitized_content == msg.content:
                    sanitized_messages.append(msg)
                else:
                    # 使用model_copy方法创建新消息，保留所有原始属性
                    try:
                        new_msg = msg.model_copy(update={"content": sanitized_content})
                        sanitized_messages.append(new_msg)
                    except Exception:
                        # 如果copy失败，直接使用原消息
                        sanitized_messages.append(msg)
            else:
                sanitized_messages.append(msg)

        # 创建修改后的请求
        modified_request = request.override(
            system_message=new_system_message,
            messages=sanitized_messages
        )

        # 调用处理器
        return handler(modified_request)

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """异步：注入技能文档到系统提示。"""
        # 获取技能元数据
        skills_metadata = request.state.get("skills_metadata", [])
        skills_list = self._format_skills_list(
            [SkillMetadata(**meta) for meta in skills_metadata]
        )

        # 生成技能系统提示
        skills_section = self.SYSTEM_PROMPT_TEMPLATE.format(
            skills_list=skills_list,
        )

        # 追加到系统消息
        new_system_message = self._append_to_system_message(
            request.system_message,
            skills_section
        )

        # 清理所有消息的content，移除不支持的类型
        sanitized_messages = []
        for msg in request.messages:
            if hasattr(msg, 'content'):
                sanitized_content = self._sanitize_message_content(msg.content)
                # 如果content没有变化，直接使用原消息
                if sanitized_content == msg.content:
                    sanitized_messages.append(msg)
                else:
                    # 使用model_copy方法创建新消息，保留所有原始属性
                    try:
                        new_msg = msg.model_copy(update={"content": sanitized_content})
                        sanitized_messages.append(new_msg)
                    except Exception:
                        # 如果copy失败，直接使用原消息
                        sanitized_messages.append(msg)
            else:
                sanitized_messages.append(msg)

        # 创建修改后的请求
        modified_request = request.override(
            system_message=new_system_message,
            messages=sanitized_messages
        )

        # 调用处理器
        return await handler(modified_request)

    def get_system_prompt_extension(self, skills_metadata: list[dict] | None = None) -> str:
        """获取系统提示扩展。

        Args:
            skills_metadata: 技能元数据列表，如果为 None 则重新加载

        Returns:
            系统提示扩展字符串
        """
        if skills_metadata is None:
            skills = self._load_skills()
        else:
            skills = [SkillMetadata(**meta) for meta in skills_metadata]

        skills_list = self._format_skills_list(skills)

        return self.SYSTEM_PROMPT_TEMPLATE.format(
            skills_list=skills_list,
        )


__all__ = ["SkillMetadata", "SkillsMiddleware", "SkillsState"]
