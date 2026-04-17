from abc import ABC, abstractmethod

from agents.sandbox.sandbox import Sandbox


class SandboxProvider(ABC):
    """沙箱提供者的抽象基类"""

    @property
    def uses_thread_data_mounts(self) -> bool:
        """线程工作区是否通过挂载可见"""
        return False

    @abstractmethod
    def acquire(self, thread_id: str | None = None, user_id: str | None = None) -> str:
        """获取沙箱环境并返回其ID。

        返回：
            获取的沙箱环境的ID。
        """
        pass

    @abstractmethod
    def get(self, sandbox_id: str) -> Sandbox | None:
        """通过ID获取沙箱环境。

        参数：
            sandbox_id：要保留的沙箱环境的ID。
        """
        pass

    @abstractmethod
    def release(self, sandbox_id: str) -> None:
        """释放沙箱环境。

        参数：
            sandbox_id：要销毁的沙箱环境的ID。
        """
        pass


_default_sandbox_provider: SandboxProvider | None = None


def get_sandbox_provider(**kwargs) -> SandboxProvider:
    """获取沙箱 Provider 单例（硬编码使用 SandboxProvider），目前就这一个实现。"""
    global _default_sandbox_provider
    if _default_sandbox_provider is None:
        # 延迟导入避免循环导入
        from agents.aio_sandbox.aio_sandbox_provider import AioSandboxProvider
        _default_sandbox_provider = AioSandboxProvider(**kwargs)
    return _default_sandbox_provider


def reset_sandbox_provider() -> None:
    """重置沙箱提供者单例。

    这会清除缓存的实例而不调用关闭。
    下次调用 `get_sandbox_provider()` 将创建一个新实例。
    用于测试或切换配置时。

    注意：如果提供者有活跃的沙箱，它们将被孤立。
    使用 `shutdown_sandbox_provider()` 进行适当的清理。
    """
    global _default_sandbox_provider
    _default_sandbox_provider = None


def shutdown_sandbox_provider() -> None:
    """关闭并重置沙箱提供者。

    这会正确关闭提供者（释放所有沙箱）
    在清除单例之前。当应用程序
    关闭或需要完全重置沙箱系统时调用此函数。
    """
    global _default_sandbox_provider
    if _default_sandbox_provider is not None:
        if hasattr(_default_sandbox_provider, "shutdown"):
            _default_sandbox_provider.shutdown()
        _default_sandbox_provider = None


def set_sandbox_provider(provider: SandboxProvider) -> None:
    """设置自定义沙箱提供者实例。

    这允许注入自定义或模拟提供者用于测试目的。

    参数：
        provider：要使用的 SandboxProvider 实例。
    """
    global _default_sandbox_provider
    _default_sandbox_provider = provider
