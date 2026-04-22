"""将上传的文件信息注入到 Agent 上下文中的中间件。"""

import logging
from pathlib import Path
from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from agents.config.paths import Paths, get_paths
from agents.utils.file_conversion import extract_outline

logger = logging.getLogger(__name__)


_OUTLINE_PREVIEW_LINES = 5


def _extract_outline_for_file(file_path: Path) -> tuple[list[dict], list[str]]:
    """返回 *file_path* 的文档大纲和后备预览。

    查找由上传转换管道生成的同名 ``<stem>.md`` 文件。

    Returns:
        (outline, preview) 其中:
        - outline: ``{title, line}`` 字典列表（可能包含哨兵标记）。
          当未找到标题或 .md 文件不存在时为空。
        - preview: .md 文件的前几行非空内容，当大纲为空时作为内容
          锚点，以便 Agent 有一些上下文。
          当大纲非空时为空（不需要后备）。
    """
    md_path = file_path.with_suffix(".md")
    if not md_path.is_file():
        return [], []

    outline = extract_outline(md_path)
    if outline:
        logger.debug("从 %s 提取了 %d 个大纲条目", file_path.name, len(outline))
        return outline, []

    # 大纲为空 — 读取前几行非空内容作为预览
    preview: list[str] = []
    try:
        with md_path.open(encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    preview.append(stripped)
                if len(preview) >= _OUTLINE_PREVIEW_LINES:
                    break
    except Exception:
        logger.debug("从 %s 读取预览行失败", md_path, exc_info=True)
    return [], preview


class UploadsMiddlewareState(AgentState):
    """上传中间件的状态模式。"""

    uploaded_files: NotRequired[list[dict] | None]


class UploadsMiddleware(AgentMiddleware[UploadsMiddlewareState]):
    """将上传的文件信息注入到 Agent 上下文中的中间件。

    从当前消息的 additional_kwargs.files 中读取文件元数据
    （由前端在上传后设置），并在最后一条人类消息前添加
    <uploaded_files> 块，以便模型知道有哪些文件可用。
    """

    state_schema = UploadsMiddlewareState

    def __init__(self, base_dir: str | None = None):
        """初始化中间件。

        Args:
            base_dir: 线程数据的基础目录。默认使用 Paths 解析。
        """
        super().__init__()
        self._paths = Paths(base_dir) if base_dir else get_paths()

    def _format_file_entry(self, file: dict, lines: list[str]) -> None:
        """将单个文件条目（名称、大小、路径、可选大纲）追加到 lines 中。"""
        size_kb = file["size"] / 1024
        size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
        lines.append(f"- {file['filename']} ({size_str})")
        lines.append(f"  Path: {file['path']}")
        outline = file.get("outline") or []
        if outline:
            truncated = outline[-1].get("truncated", False)
            visible = [e for e in outline if not e.get("truncated")]
            lines.append("  文档大纲（使用 `read_file` 配合行号范围来读取各部分）:")
            for entry in visible:
                lines.append(f"    L{entry['line']}: {entry['title']}")
            if truncated:
                lines.append(f"    ... (显示前 {len(visible)} 个标题；使用 `read_file` 进一步探索)")
        else:
            preview = file.get("outline_preview") or []
            if preview:
                lines.append("  未检测到结构性标题。文档开头为:")
                for text in preview:
                    lines.append(f"    > {text}")
            lines.append("  使用 `grep` 搜索关键词 (例如 `grep(pattern='keyword', path='/mnt/user-data/uploads/')`)。")
        lines.append("")

    def _create_files_message(self, new_files: list[dict], historical_files: list[dict]) -> str:
        """创建一个列出上传文件的格式化消息。

        Args:
            new_files: 在当前消息中上传的文件。
            historical_files: 在之前消息中上传的文件。
                每个文件字典可能包含可选的 ``outline`` 键 — 从转换后的
                Markdown 文件中提取的 ``{title, line}`` 字典列表。

        Returns:
            <uploaded_files> 标签内的格式化字符串。
        """
        lines = ["<uploaded_files>"]

        lines.append("在此消息中上传了以下文件:")
        lines.append("")
        if new_files:
            for file in new_files:
                self._format_file_entry(file, lines)
        else:
            lines.append("(空)")
            lines.append("")

        if historical_files:
            lines.append("以下文件是在之前的消息中上传的，仍然可用:")
            lines.append("")
            for file in historical_files:
                self._format_file_entry(file, lines)

        lines.append("使用这些文件:")
        lines.append("- 首先从文件中读取 — 使用大纲行号和 `read_file` 来定位相关部分。")
        lines.append("- 使用 `grep` 搜索关键词，当你不确定应该查看哪个部分时")
        lines.append("  (例如 `grep(pattern='revenue', path='/mnt/user-data/uploads/')`)。")
        lines.append("- 使用 `glob` 按名称模式查找文件")
        lines.append("  (例如 `glob(pattern='**/*.md', path='/mnt/user-data/uploads/')`)。")
        lines.append("- 仅在文件内容明显不足以回答问题时才使用网络搜索作为后备。")
        lines.append("</uploaded_files>")

        return "\n".join(lines)

    def _files_from_kwargs(self, message: HumanMessage, uploads_dir: Path | None = None) -> list[dict] | None:
        """从消息的 additional_kwargs.files 中提取文件信息。

        前端在上传成功后，会将上传的文件元数据发送到 additional_kwargs.files。
        每个条目包含: filename, size (bytes), path (虚拟路径), status。

        Args:
            message: 要检查的人类消息。
            uploads_dir: 用于验证文件是否存在的物理上传目录。
                         当提供时，文件不再存在的条目将被跳过。

        Returns:
            包含虚拟路径的文件字典列表，如果字段不存在或为空则返回 None。
        """
        kwargs_files = (message.additional_kwargs or {}).get("files")
        if not isinstance(kwargs_files, list) or not kwargs_files:
            return None

        files = []
        for f in kwargs_files:
            if not isinstance(f, dict):
                continue
            filename = f.get("filename") or ""
            if not filename or Path(filename).name != filename:
                continue
            if uploads_dir is not None and not (uploads_dir / filename).is_file():
                continue
            files.append(
                {
                    "filename": filename,
                    "size": int(f.get("size") or 0),
                    "path": f"/mnt/user-data/uploads/{filename}",
                    "extension": Path(filename).suffix,
                }
            )
        return files if files else None

    @override
    def before_agent(self, state: UploadsMiddlewareState, runtime: Runtime) -> dict | None:
        """在 Agent 执行前注入上传的文件信息。

        新文件来自当前消息的 additional_kwargs.files。
        历史文件从线程的上传目录中扫描，排除新文件。

        将 <uploaded_files> 上下文添加到最后一条人类消息的内容前。
        原始的 additional_kwargs（包括文件元数据）在更新的消息中保留，
        以便前端可以从流中读取它。

        Args:
            state: 当前 Agent 状态。
            runtime: 包含 thread_id 的运行时上下文。

        Returns:
            包括上传文件列表的状态更新。
        """
        messages = list(state.get("messages", []))
        if not messages:
            return None

        last_message_index = len(messages) - 1
        last_message = messages[last_message_index]

        if not isinstance(last_message, HumanMessage):
            return None

        # 解析上传目录用于存在性检查
        thread_id = (runtime.context or {}).get("thread_id")
        if thread_id is None:
            try:
                from langgraph.config import get_config

                thread_id = get_config().get("configurable", {}).get("thread_id")
            except RuntimeError:
                pass  # get_config() 在可运行上下文之外会抛出异常（例如单元测试）
        uploads_dir = self._paths.sandbox_uploads_dir(thread_id) if thread_id else None

        # 从当前消息的 additional_kwargs.files 获取新上传的文件
        new_files = self._files_from_kwargs(last_message, uploads_dir) or []

        # 从上传目录收集历史文件（除新文件外的所有文件）
        new_filenames = {f["filename"] for f in new_files}
        historical_files: list[dict] = []
        if uploads_dir and uploads_dir.exists():
            for file_path in sorted(uploads_dir.iterdir()):
                if file_path.is_file() and file_path.name not in new_filenames:
                    stat = file_path.stat()
                    outline, preview = _extract_outline_for_file(file_path)
                    historical_files.append(
                        {
                            "filename": file_path.name,
                            "size": stat.st_size,
                            "path": f"/mnt/user-data/uploads/{file_path.name}",
                            "extension": file_path.suffix,
                            "outline": outline,
                            "outline_preview": preview,
                        }
                    )

        # 为新文件附加大纲
        if uploads_dir:
            for file in new_files:
                phys_path = uploads_dir / file["filename"]
                outline, preview = _extract_outline_for_file(phys_path)
                file["outline"] = outline
                file["outline_preview"] = preview

        if not new_files and not historical_files:
            return None

        logger.debug(f"新文件: {[f['filename'] for f in new_files]}, 历史: {[f['filename'] for f in historical_files]}")

        # 创建文件消息并添加到最后一条人类消息内容前
        files_message = self._create_files_message(new_files, historical_files)

        # 提取原始内容 - 处理字符串和列表格式
        original_content = last_message.content
        if isinstance(original_content, str):
            # 简单情况: 字符串内容，只需添加文件消息
            updated_content = f"{files_message}\n\n{original_content}"
        elif isinstance(original_content, list):
            # 复杂情况: 列表内容（多模态），保留所有块
            # 将文件消息作为第一个文本块添加
            files_block = {"type": "text", "text": f"{files_message}\n\n"}
            # 保留所有原始块（包括图片）
            updated_content = [files_block, *original_content]
        else:
            # 其他类型，按原样保留
            updated_content = original_content

        # 创建包含组合内容的新消息。
        # 保留 additional_kwargs（包括文件元数据），以便前端
        # 可以从流式消息中读取结构化文件信息。
        updated_message = HumanMessage(
            content=updated_content,
            id=last_message.id,
            additional_kwargs=last_message.additional_kwargs,
        )

        messages[last_message_index] = updated_message

        return {
            "uploaded_files": new_files,
            "messages": messages,
        }
