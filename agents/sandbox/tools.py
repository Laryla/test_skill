import re

from langchain.tools import ToolRuntime, tool
from langgraph.typing import ContextT

from agents.sandbox.exceptions import (
    SandboxError,
    SandboxNotFoundError,
    SandboxRuntimeError,
)
from agents.sandbox.file_operation_lock import get_file_operation_lock
from agents.sandbox.sandbox import Sandbox
from agents.sandbox.sandbox_provider import get_sandbox_provider
from agents.sandbox.search import GrepMatch
from agents.thread_state import ThreadDataState, ThreadState

_DEFAULT_GLOB_MAX_RESULTS = 200
_MAX_GLOB_MAX_RESULTS = 1000
_DEFAULT_GREP_MAX_RESULTS = 100
_MAX_GREP_MAX_RESULTS = 500


def _get_tool_config_int(_name: str, _key: str, default: int) -> int:
    """获取工具配置参数（当前未使用配置，直接返回默认值）。"""
    return default


def _clamp_max_results(value: int, *, default: int, upper_bound: int) -> int:
    if value <= 0:
        return default
    return min(value, upper_bound)


def _resolve_max_results(name: str, requested: int, *, default: int, upper_bound: int) -> int:
    requested_max_results = _clamp_max_results(requested, default=default, upper_bound=upper_bound)
    configured_max_results = _clamp_max_results(
        _get_tool_config_int(name, "max_results", default),
        default=default,
        upper_bound=upper_bound,
    )
    return min(requested_max_results, configured_max_results)


def _format_glob_results(root_path: str, matches: list[str], truncated: bool) -> str:
    if not matches:
        return f"No files matched under {root_path}"

    lines = [f"Found {len(matches)} paths under {root_path}"]
    if truncated:
        lines[0] += f" (showing first {len(matches)})"
    lines.extend(f"{index}. {path}" for index, path in enumerate(matches, start=1))
    if truncated:
        lines.append("Results truncated. Narrow the path or pattern to see fewer matches.")
    return "\n".join(lines)


def _format_grep_results(root_path: str, matches: list[GrepMatch], truncated: bool) -> str:
    if not matches:
        return f"No matches found under {root_path}"

    lines = [f"Found {len(matches)} matches under {root_path}"]
    if truncated:
        lines[0] += f" (showing first {len(matches)})"
    lines.extend(f"{match.path}:{match.line_number}: {match.line}" for match in matches)
    if truncated:
        lines.append("Results truncated. Narrow the path or add a glob filter.")
    return "\n".join(lines)


def _sanitize_error(error: Exception) -> str:
    """清理错误消息。"""
    return f"{type(error).__name__}: {error}"


def get_thread_data(runtime: ToolRuntime[ContextT, ThreadState] | None) -> ThreadDataState | None:
    """从运行时状态中提取 thread_data。"""
    if runtime is None:
        return None
    if runtime.state is None:
        return None
    return runtime.state.get("thread_data")


def sandbox_from_runtime(runtime: ToolRuntime[ContextT, ThreadState] | None = None) -> Sandbox:
    """从工具运行时中提取沙箱实例。

    已弃用：使用 ensure_sandbox_initialized() 以获得延迟初始化支持。
    此函数假设沙箱已初始化，如果未初始化则会引发错误。

    Raises:
        SandboxRuntimeError: 如果运行时不可用或沙箱状态缺失。
        SandboxNotFoundError: 如果找不到具有给定 ID 的沙箱。
    """
    if runtime is None:
        raise SandboxRuntimeError("Tool runtime not available")
    if runtime.state is None:
        raise SandboxRuntimeError("Tool runtime state not available")
    sandbox_state = runtime.state.get("sandbox")
    if sandbox_state is None:
        raise SandboxRuntimeError("Sandbox state not initialized in runtime")
    sandbox_id = sandbox_state.get("sandbox_id")
    if sandbox_id is None:
        raise SandboxRuntimeError("Sandbox ID not found in state")
    sandbox = get_sandbox_provider().get(sandbox_id)
    if sandbox is None:
        raise SandboxNotFoundError(f"Sandbox with ID '{sandbox_id}' not found", sandbox_id=sandbox_id)

    if runtime.context is not None:
        runtime.context["sandbox_id"] = sandbox_id  # Ensure sandbox_id is in context for downstream use
    return sandbox


def ensure_sandbox_initialized(runtime: ToolRuntime[ContextT, ThreadState] | None = None) -> Sandbox:
    """确保沙箱已初始化，如果需要则延迟获取。

    首次调用时，从提供程序获取沙箱并将其存储在运行时状态中。
    后续调用返回现有的沙箱。

    线程安全性由提供程序的内部锁定机制保证。

    Args:
        runtime: 包含状态和上下文的工具运行时。

    Returns:
        已初始化的沙箱实例。

    Raises:
        SandboxRuntimeError: 如果运行时不可用或 thread_id 缺失。
        SandboxNotFoundError: 如果沙箱获取失败。
    """
    if runtime is None:
        raise SandboxRuntimeError("Tool runtime not available")

    if runtime.state is None:
        raise SandboxRuntimeError("Tool runtime state not available")

    # 检查状态中是否已存在沙箱
    sandbox_state = runtime.state.get("sandbox")
    if sandbox_state is not None:
        sandbox_id = sandbox_state.get("sandbox_id")
        if sandbox_id is not None:
            sandbox = get_sandbox_provider().get(sandbox_id)
            if sandbox is not None:
                if runtime.context is not None:
                    runtime.context["sandbox_id"] = sandbox_id  # Ensure sandbox_id is in context for releasing in after_agent
                return sandbox
            # 沙箱已释放，继续获取新的

    # 延迟获取：获取 thread_id 和 user_id 并获取沙箱
    thread_id = runtime.context.get("thread_id") if runtime.context else None
    if thread_id is None:
        thread_id = runtime.config.get("configurable", {}).get("thread_id") if runtime.config else None
    if thread_id is None:
        raise SandboxRuntimeError("Thread ID not available in runtime context")

    user_id = runtime.context.get("user_id") if runtime.context else None
    if user_id is None:
        user_id = runtime.config.get("configurable", {}).get("user_id") if runtime.config else None

    provider = get_sandbox_provider()
    sandbox_id = provider.acquire(thread_id, user_id)

    # 更新运行时状态 - 这在工具调用之间持久存在
    runtime.state["sandbox"] = {"sandbox_id": sandbox_id}

    # 检索并返回沙箱
    sandbox = provider.get(sandbox_id)
    if sandbox is None:
        raise SandboxNotFoundError("Sandbox not found after acquisition", sandbox_id=sandbox_id)

    if runtime.context is not None:
        runtime.context["sandbox_id"] = sandbox_id  # Ensure sandbox_id is in context for releasing in after_agent
    return sandbox


def ensure_thread_directories_exist(_runtime: ToolRuntime[ContextT, ThreadState] | None) -> None:
    """确保线程数据目录存在（Docker 沙箱不需要手动创建）。"""
    pass  # Docker 沙箱中目录已自动挂载，无需手动创建


def _truncate_bash_output(output: str, max_chars: int) -> str:
    """从中间截断 bash 输出，保留头部和尾部（50/50 分割）。

    bash 输出可能在任一端都有错误（stderr/stdout 顺序是不确定的），
    因此两端都平等保留。

    返回的字符串（包括截断标记）保证不超过 max_chars 个字符。
    传递 max_chars=0 以禁用截断并完整返回输出。
    """
    if max_chars == 0:
        return output
    if len(output) <= max_chars:
        return output
    total_len = len(output)
    # 计算精确的最坏情况标记长度：跳过的字符最多为
    # total_len，因此这是一个紧密的上限。
    marker_max_len = len(f"\n... [middle truncated: {total_len} chars skipped] ...\n")
    kept = max(0, max_chars - marker_max_len)
    if kept == 0:
        return output[:max_chars]
    head_len = kept // 2
    tail_len = kept - head_len
    skipped = total_len - kept
    marker = f"\n... [middle truncated: {skipped} chars skipped] ...\n"
    return f"{output[:head_len]}{marker}{output[-tail_len:] if tail_len > 0 else ''}"


def _truncate_read_file_output(output: str, max_chars: int) -> str:
    """从头部截断 read_file 输出，保留文件的开头。

    源代码和文档从上到下阅读；头部包含最多的上下文（导入、类定义、
    函数签名）。

    返回的字符串（包括截断标记）保证不超过 max_chars 个字符。
    传递 max_chars=0 以禁用截断并完整返回输出。
    """
    if max_chars == 0:
        return output
    if len(output) <= max_chars:
        return output
    total = len(output)
    # 计算精确的最坏情况标记长度：两个数字字段都处于
    # 最大值（总字符数），因此这是一个紧密的上限。
    marker_max_len = len(f"\n... [truncated: showing first {total} of {total} chars. Use start_line/end_line to read a specific range] ...")
    kept = max(0, max_chars - marker_max_len)
    if kept == 0:
        return output[:max_chars]
    marker = f"\n... [truncated: showing first {kept} of {total} chars. Use start_line/end_line to read a specific range] ..."
    return f"{output[:kept]}{marker}"


def _truncate_ls_output(output: str, max_chars: int) -> str:
    """从头部截断 ls 输出，保留列表的开头。

    目录列表从上到下阅读；头部显示最相关的结构。

    返回的字符串（包括截断标记）保证不超过 max_chars 个字符。
    传递 max_chars=0 以禁用截断并完整返回输出。
    """
    if max_chars == 0:
        return output
    if len(output) <= max_chars:
        return output
    total = len(output)
    marker_max_len = len(f"\n... [truncated: showing first {total} of {total} chars. Use a more specific path to see fewer results] ...")
    kept = max(0, max_chars - marker_max_len)
    if kept == 0:
        return output[:max_chars]
    marker = f"\n... [truncated: showing first {kept} of {total} chars. Use a more specific path to see fewer results] ..."
    return f"{output[:kept]}{marker}"


@tool("bash", parse_docstring=True)
def bash_tool(runtime: ToolRuntime[ContextT, ThreadState], description: str, command: str) -> str:
    """在 Linux 环境中执行 bash 命令。


    - 使用 `python` 运行 Python 代码。
    - 优先使用 `/mnt/user-data/workspace/.venv` 中的线程本地虚拟环境。
    - 使用 `python -m pip`（在虚拟环境中）安装 Python 包。

    Args:
        description: 简要解释您为什么要运行此命令。务必首先提供此参数。
        command: 要执行的 bash 命令。始终使用文件和目录的绝对路径。
    """
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        try:
            max_chars = 20000
        except Exception:
            max_chars = 20000
        return _truncate_bash_output(sandbox.execute_command(command), max_chars)
    except SandboxError as e:
        return f"Error: {e}"
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: Unexpected error executing command: {_sanitize_error(e)}"


@tool("ls", parse_docstring=True)
def ls_tool(runtime: ToolRuntime[ContextT, ThreadState], description: str, path: str) -> str:
    """以树形格式列出目录内容，深度最多为 2 层。

    Args:
        description: 简要解释您为什么要列出此目录。务必首先提供此参数。
        path: 要列出的目录的**绝对**路径。
    """
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        requested_path = path
        children = sandbox.list_dir(path)
        if not children:
            return "(empty)"
        output = "\n".join(children)
        try:
            max_chars = 20000
        except Exception:
            max_chars = 20000
        return _truncate_ls_output(output, max_chars)
    except SandboxError as e:
        return f"Error: {e}"
    except FileNotFoundError:
        return f"Error: Directory not found: {requested_path}"
    except PermissionError:
        return f"Error: Permission denied: {requested_path}"
    except Exception as e:
        return f"Error: Unexpected error listing directory: {_sanitize_error(e)}"


@tool("glob", parse_docstring=True)
def glob_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    description: str,
    pattern: str,
    path: str,
    include_dirs: bool = False,
    max_results: int = _DEFAULT_GLOB_MAX_RESULTS,
) -> str:
    """在根目录下查找匹配 glob 模式的文件或目录。

    Args:
        description: 简要解释您为什么要搜索这些路径。务必首先提供此参数。
        pattern: 相对于根路径要匹配的 glob 模式，例如 `**/*.py`。
        path: 要搜索的**绝对**根目录。
        include_dirs: 是否也返回匹配的目录。默认为 False。
        max_results: 要返回的路径的最大数量。默认为 200。
    """
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        requested_path = path
        effective_max_results = _resolve_max_results(
            "glob",
            max_results,
            default=_DEFAULT_GLOB_MAX_RESULTS,
            upper_bound=_MAX_GLOB_MAX_RESULTS,
        )
        matches, truncated = sandbox.glob(path, pattern, include_dirs=include_dirs, max_results=effective_max_results)
        return _format_glob_results(requested_path, matches, truncated)
    except SandboxError as e:
        return f"Error: {e}"
    except FileNotFoundError:
        return f"Error: Directory not found: {requested_path}"
    except NotADirectoryError:
        return f"Error: Path is not a directory: {requested_path}"
    except PermissionError:
        return f"Error: Permission denied: {requested_path}"
    except Exception as e:
        return f"Error: Unexpected error searching paths: {_sanitize_error(e)}"


@tool("grep", parse_docstring=True)
def grep_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    description: str,
    pattern: str,
    path: str,
    glob: str | None = None,
    literal: bool = False,
    case_sensitive: bool = False,
    max_results: int = _DEFAULT_GREP_MAX_RESULTS,
) -> str:
    """在根目录下的文本文件中搜索匹配的行。

    Args:
        description: 简要解释您为什么要搜索文件内容。务必首先提供此参数。
        pattern: 要搜索的字符串或正则表达式模式。
        path: 要搜索的**绝对**根目录。
        glob: 候选文件的可选 glob 过滤器，例如 `**/*.py`。
        literal: 是否将 `pattern` 视为纯字符串。默认为 False。
        case_sensitive: 匹配是否区分大小写。默认为 False。
        max_results: 要返回的匹配行的最大数量。默认为 100。
    """
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        requested_path = path
        effective_max_results = _resolve_max_results(
            "grep",
            max_results,
            default=_DEFAULT_GREP_MAX_RESULTS,
            upper_bound=_MAX_GREP_MAX_RESULTS,
        )
        matches, truncated = sandbox.grep(
            path,
            pattern,
            glob=glob,
            literal=literal,
            case_sensitive=case_sensitive,
            max_results=effective_max_results,
        )
        return _format_grep_results(requested_path, matches, truncated)
    except SandboxError as e:
        return f"Error: {e}"
    except FileNotFoundError:
        return f"Error: Directory not found: {requested_path}"
    except NotADirectoryError:
        return f"Error: Path is not a directory: {requested_path}"
    except re.error as e:
        return f"Error: Invalid regex pattern: {e}"
    except PermissionError:
        return f"Error: Permission denied: {requested_path}"
    except Exception as e:
        return f"Error: Unexpected error searching file contents: {_sanitize_error(e)}"


@tool("read_file", parse_docstring=True)
def read_file_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    description: str,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """读取文本文件的内容。使用此工具检查源代码、配置文件、日志或任何基于文本的文件。

    Args:
        description: 简要解释您为什么要读取此文件。务必首先提供此参数。
        path: 要读取的文件的**绝对**路径。
        start_line: 可选的起始行号（从 1 开始，包含）。与 end_line 一起使用以读取特定范围。
        end_line: 可选的结束行号（从 1 开始，包含）。与 start_line 一起使用以读取特定范围。
    """
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        requested_path = path
        content = sandbox.read_file(path)
        if not content:
            return "(empty)"
        if start_line is not None and end_line is not None:
            content = "\n".join(content.splitlines()[start_line - 1 : end_line])
        try:
            max_chars = 50000
        except Exception:
            max_chars = 50000
        return _truncate_read_file_output(content, max_chars)
    except SandboxError as e:
        return f"Error: {e}"
    except FileNotFoundError:
        return f"Error: File not found: {requested_path}"
    except PermissionError:
        return f"Error: Permission denied reading file: {requested_path}"
    except IsADirectoryError:
        return f"Error: Path is a directory, not a file: {requested_path}"
    except Exception as e:
        return f"Error: Unexpected error reading file: {_sanitize_error(e)}"


@tool("write_file", parse_docstring=True)
def write_file_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    description: str,
    path: str,
    content: str,
    append: bool = False,
) -> str:
    """将文本内容写入文件。

    Args:
        description: 简要解释您为什么要写入此文件。务必首先提供此参数。
        path: 要写入的文件的**绝对**路径。务必第二个提供此参数。
        content: 要写入文件的内容。务必第三个提供此参数。
    """
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        requested_path = path
        with get_file_operation_lock(sandbox, path):
            sandbox.write_file(path, content, append)
        return "OK"
    except SandboxError as e:
        return f"Error: {e}"
    except PermissionError:
        return f"Error: Permission denied writing to file: {requested_path}"
    except IsADirectoryError:
        return f"Error: Path is a directory, not a file: {requested_path}"
    except OSError as e:
        return f"Error: Failed to write file '{requested_path}': {_sanitize_error(e)}"
    except Exception as e:
        return f"Error: Unexpected error writing file: {_sanitize_error(e)}"


@tool("str_replace", parse_docstring=True)
def str_replace_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    description: str,
    path: str,
    old_str: str,
    new_str: str,
    replace_all: bool = False,
) -> str:
    """将文件中的子字符串替换为另一个子字符串。
    如果 `replace_all` 为 False（默认），要替换的子字符串必须在文件中**恰好出现一次**。

    Args:
        description: 简要解释您为什么要替换此子字符串。务必首先提供此参数。
        path: 要在其中替换子字符串的文件的**绝对**路径。务必第二个提供此参数。
        old_str: 要替换的子字符串。务必第三个提供此参数。
        new_str: 新的子字符串。务必第四个提供此参数。
        replace_all: 是否替换所有出现的子字符串。如果为 False，则仅替换第一个出现的项。默认为 False。
    """
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        requested_path = path
        with get_file_operation_lock(sandbox, path):
            content = sandbox.read_file(path)
            if not content:
                return "OK"
            if old_str not in content:
                return f"Error: String to replace not found in file: {requested_path}"
            if replace_all:
                content = content.replace(old_str, new_str)
            else:
                content = content.replace(old_str, new_str, 1)
            sandbox.write_file(path, content)
        return "OK"
    except SandboxError as e:
        return f"Error: {e}"
    except FileNotFoundError:
        return f"Error: File not found: {requested_path}"
    except PermissionError:
        return f"Error: Permission denied accessing file: {requested_path}"
    except Exception as e:
        return f"Error: Unexpected error replacing string: {_sanitize_error(e)}"


TOOLS = [bash_tool, ls_tool, glob_tool, grep_tool, read_file_tool, write_file_tool, str_replace_tool]