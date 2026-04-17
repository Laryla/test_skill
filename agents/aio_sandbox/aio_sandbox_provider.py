"""AIO 沙箱提供者 — 使用可插拔后端编排沙箱生命周期。

此提供者组成：
- SandboxBackend：沙箱如何供应（本地容器 vs 远程/K8s）

提供者本身处理：
- 进程内缓存以实现快速重复访问
- 空闲超时管理
- 带有信号处理的优雅关闭
- 挂载计算（线程特定，技能）
"""

import atexit
import hashlib
import logging
import os
import signal
import threading
import time
import uuid

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]
    import msvcrt

from typing import TYPE_CHECKING

from agents.config.paths import get_paths
from agents.sandbox.sandbox import Sandbox

from .aio_sandbox import AioSandbox

# 延迟导入避免循环 - 运行时手动设置基类
if TYPE_CHECKING:
    from agents.sandbox.sandbox_provider import SandboxProvider as _SandboxProviderBase
else:
    _SandboxProviderBase = object
from .backend import SandboxBackend, wait_for_sandbox_ready
from .local_backend import LocalContainerBackend
from .remote_backend import RemoteSandboxBackend
from .sandbox_info import SandboxInfo

logger = logging.getLogger(__name__)

# 默认配置
# 镜像
DEFAULT_IMAGE = "enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest"
# 初始端口
DEFAULT_PORT = 8080
# 容器前缀
DEFAULT_CONTAINER_PREFIX = "deer-flow-sandbox"
# 空闲超时（秒）
DEFAULT_IDLE_TIMEOUT = 600  # 10 minutes in seconds
# 最大并发
DEFAULT_REPLICAS = 3  # Maximum concurrent sandbox containers
# 空闲检查间隔（秒）
IDLE_CHECK_INTERVAL = 60  # Check every 60 seconds
# 虚拟路径前缀
VIRTUAL_PATH_PREFIX = "/mnt/user-data" 

def _lock_file_exclusive(lock_file) -> None:
    if fcntl is not None:
        fcntl.flock(lock_file, fcntl.LOCK_EX)  # type: ignore[attr-defined]
        return

    lock_file.seek(0)
    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]


def _unlock_file(lock_file) -> None:
    if fcntl is not None:
        fcntl.flock(lock_file, fcntl.LOCK_UN) # type: ignore[attr-defined]
        return

    lock_file.seek(0)
    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1) # type: ignore[attr-defined]


class AioSandboxProvider(_SandboxProviderBase):
    """管理运行AIO沙箱的容器的沙箱提供者。

    架构：
        此提供者组成SandboxBackend（如何供应），启用：
        - 本地Docker/Apple容器模式（自动启动容器）
        - 远程/K8s模式（连接到预先存在的沙箱URL）

    config.yaml中sandbox下的配置选项：
        use: deerflow.community.aio_sandbox:AioSandboxProvider
        image: <container image>
        port: 8080                      # 本地容器的基础端口
        container_prefix: deer-flow-sandbox
        idle_timeout: 600               # 空闲超时秒数（0禁用）
        replicas: 3                     # 最大并发沙箱容器（超过时LRU驱逐）
        mounts:                         # 本地容器的卷挂载
          - host_path: /path/on/host
            container_path: /path/in/container
            read_only: false
        environment:                    # 容器的环境变量
          NODE_ENV: production
          API_KEY: $MY_API_KEY
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._sandboxes: dict[str, AioSandbox] = {}  # sandbox_id -> AioSandbox instance
        self._sandbox_infos: dict[str, SandboxInfo] = {}  # sandbox_id -> SandboxInfo (for destroy)
        self._thread_sandboxes: dict[str, str] = {}  # thread_id -> sandbox_id
        self._thread_locks: dict[str, threading.Lock] = {}  # thread_id -> in-process lock
        self._last_activity: dict[str, float] = {}  # sandbox_id -> last activity timestamp
        # Warm pool: released sandboxes whose containers are still running.
        # 映射 sandbox_id -> (SandboxInfo, release_timestamp)。
        # 此处的容器可以快速回收（无冷启动）或在副本容量耗尽时销毁。
        self._warm_pool: dict[str, tuple[SandboxInfo, float]] = {}
        self._shutdown_called = False
        self._idle_checker_stop = threading.Event()
        self._idle_checker_thread: threading.Thread | None = None

        self._config = self._load_config()
        self._backend: SandboxBackend = self._create_backend()

        # 注册关闭处理器
        atexit.register(self.shutdown)
        self._register_signal_handlers()

        # 协调前一个进程生命周期留下的孤立容器
        self._reconcile_orphans()

        # 如果启用则启动空闲检查器
        if self._config.get("idle_timeout", DEFAULT_IDLE_TIMEOUT) > 0:
            self._start_idle_checker()

    @property
    def uses_thread_data_mounts(self) -> bool:
        """线程工作区/上传/输出是否通过挂载可见。

        本地容器后端绑定挂载线程数据目录，因此网关写入的文件
        在沙箱启动时已经可见。
        远程后端可能需要显式文件同步。
        """
        return isinstance(self._backend, LocalContainerBackend)

    # ── 工厂方法 ──────────────────────────────────────────────────

    def _create_backend(self) -> SandboxBackend:
        """根据配置创建适当的后端。

        选择逻辑（按顺序检查）：
        1. ``provisioner_url`` 设置 → RemoteSandboxBackend（供应器模式）
              供应器动态在k3s中创建Pods + Services。
        2. 默认 → LocalContainerBackend（本地模式）
              本地提供者直接管理容器生命周期（启动/停止）。
        """

        # 这里可以选择外部的docker的容器编排器（例如k3s）提供的URL，或者直接使用本地容器后端。
        # 现在是本地的
        provisioner_url = self._config.get("provisioner_url")
        if provisioner_url:
            logger.info(f"Using remote sandbox backend with provisioner at {provisioner_url}")
            return RemoteSandboxBackend(provisioner_url=provisioner_url)

        logger.info("Using local container sandbox backend")
        return LocalContainerBackend(
            image=self._config["image"],
            base_port=self._config["port"],
            container_prefix=self._config["container_prefix"],
            config_mounts=self._config["mounts"],
            environment=self._config["environment"],
        )

    # ── 配置 ────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        """从应用配置加载沙箱配置。"""

        return {
            "image":  DEFAULT_IMAGE,
            "port":  DEFAULT_PORT,
            "container_prefix": DEFAULT_CONTAINER_PREFIX,
            "idle_timeout":  DEFAULT_IDLE_TIMEOUT,
            "replicas":  DEFAULT_REPLICAS,
            "mounts": [],
            "environment": {},
            # provisioner URL for dynamic pod management (e.g. http://provisioner:8002)
            # 远程的镜像地址（本地的docekr不需要，k8s动态调度需要）
            # "provisioner_url": getattr(sandbox_config, "provisioner_url", None) or "",
        }

    @staticmethod
    def _resolve_env_vars(env_config: dict[str, str]) -> dict[str, str]:
        """解析环境变量引用（以$开头的值）。"""
        resolved = {}
        for key, value in env_config.items():
            if isinstance(value, str) and value.startswith("$"):
                env_name = value[1:]
                resolved[key] = os.environ.get(env_name, "")
            else:
                resolved[key] = str(value)
        return resolved

    # ── 启动协调 ────────────────────────────────────────────

    def _reconcile_orphans(self) -> None:
        """协调由前一个进程生命周期留下的孤立容器。

        启动时，枚举所有运行的匹配前缀的容器
        并将它们全部收养到暖池中。空闲检查器将回收
        没有人重新获取的容器在 ``idle_timeout`` 内。

        所有容器都被无条件收养，因为我们无法
        仅基于年龄区分“孤立”和“另一个进程正在积极使用”
        — ``idle_timeout`` 表示不活动，而不是
        正常运行时间。将它们收养到暖池并让空闲检查器
        决定避免销毁并发进程可能仍在使用的容器。

        这弥补了内存状态丢失（进程
        重启、崩溃、SIGKILL）导致Docker容器永远运行的基本差距。
        """
        try:
            running = self._backend.list_running()
        except Exception as e:
            logger.warning(f"Failed to enumerate running containers during startup reconciliation: {e}")
            return

        if not running:
            return

        current_time = time.time()
        adopted = 0

        for info in running:
            age = current_time - info.created_at if info.created_at > 0 else float("inf")
            # Single lock acquisition per container: atomic check-and-insert.
            # Avoids a TOCTOU window between the "already tracked?" check and
            # the warm-pool insert.
            with self._lock:
                if info.sandbox_id in self._sandboxes or info.sandbox_id in self._warm_pool:
                    continue
                self._warm_pool[info.sandbox_id] = (info, current_time)
            adopted += 1
            logger.info(f"Adopted container {info.sandbox_id} into warm pool (age: {age:.0f}s)")

        logger.info(f"Startup reconciliation complete: {adopted} adopted into warm pool, {len(running)} total found")

    # ── 确定性ID ─────────────────────────────────────────────────

    @staticmethod
    def _deterministic_sandbox_id(thread_id: str) -> str:
        """从线程ID生成确定性的沙箱ID。

        确保所有进程为给定线程派生相同的sandbox_id，
        启用跨进程沙箱发现而无需共享内存。
        """
        return hashlib.sha256(thread_id.encode()).hexdigest()[:8]

    # ── 挂载助手 ────────────────────────────────────────────────────

    def _get_extra_mounts(self, thread_id: str | None, user_id: str | None = None) -> list[tuple[str, str, bool]]:
        """收集沙箱的所有额外挂载（线程特定 + 技能）。"""
        mounts: list[tuple[str, str, bool]] = []

        if thread_id:
            mounts.extend(self._get_thread_mounts(thread_id))
            logger.info(f"Adding thread mounts for thread {thread_id}: {mounts}")

        skills_mount = self._get_skills_mount(user_id)
        if skills_mount:
            mounts.append(skills_mount)
            logger.info(f"Adding skills mount for user {user_id}: {skills_mount}")

        return mounts

    @staticmethod
    def _get_thread_mounts(thread_id: str) -> list[tuple[str, str, bool]]:
        """获取线程数据目录的卷挂载。

        如果不存在则创建目录（延迟初始化）。
        挂载源使用host_base_dir以便当在Docker中运行时使用挂载的Docker套接字（DooD），
        主机Docker守护程序可以解析路径。
        """
        paths = get_paths()
        paths.ensure_thread_dirs(thread_id)

        return [
            (paths.host_sandbox_work_dir(thread_id), f"{VIRTUAL_PATH_PREFIX}/workspace", False),
            (paths.host_sandbox_uploads_dir(thread_id), f"{VIRTUAL_PATH_PREFIX}/uploads", False),
            (paths.host_sandbox_outputs_dir(thread_id), f"{VIRTUAL_PATH_PREFIX}/outputs", False),
        ]

    @staticmethod
    def _get_skills_mount(user_id: str | None) -> tuple[str, str, bool] | None:
        """获取技能目录挂载配置。

        直接写死宿主机技能根目录，并区分全局和用户目录。
        """

        # 获取项目根目录
        host_skills = os.path.join(os.getcwd(), "skills")
        container_path = "/mnt/skills"
        global_skills_dir = os.path.join(host_skills, "global")

        os.makedirs(global_skills_dir, exist_ok=True)
        if user_id:
            user_skills_dir = os.path.join(host_skills, user_id)
            os.makedirs(user_skills_dir, exist_ok=True)

        return (host_skills, container_path, True)  # 出于安全原因为只读

    # ── 空闲超时管理 ──────────────────────────────────────────

    def _start_idle_checker(self) -> None:
        """启动检查空闲沙箱的后台线程。"""
        self._idle_checker_thread = threading.Thread(
            target=self._idle_checker_loop,
            name="sandbox-idle-checker",
            daemon=True,
        )
        self._idle_checker_thread.start()
        logger.info(f"Started idle checker thread (timeout: {self._config.get('idle_timeout', DEFAULT_IDLE_TIMEOUT)}s)")

    def _idle_checker_loop(self) -> None:
        idle_timeout = self._config.get("idle_timeout", DEFAULT_IDLE_TIMEOUT)
        while not self._idle_checker_stop.wait(timeout=IDLE_CHECK_INTERVAL):
            try:
                self._cleanup_idle_sandboxes(idle_timeout)
            except Exception as e:
                logger.error(f"Error in idle checker loop: {e}")

    def _cleanup_idle_sandboxes(self, idle_timeout: float) -> None:
        current_time = time.time()
        active_to_destroy = []
        warm_to_destroy: list[tuple[str, SandboxInfo]] = []

        with self._lock:
            # Active sandboxes: tracked via _last_activity
            for sandbox_id, last_activity in self._last_activity.items():
                idle_duration = current_time - last_activity
                if idle_duration > idle_timeout:
                    active_to_destroy.append(sandbox_id)
                    logger.info(f"Sandbox {sandbox_id} idle for {idle_duration:.1f}s, marking for destroy")

            # Warm pool: tracked via release_timestamp stored in _warm_pool
            for sandbox_id, (info, release_ts) in list(self._warm_pool.items()):
                warm_duration = current_time - release_ts
                if warm_duration > idle_timeout:
                    warm_to_destroy.append((sandbox_id, info))
                    del self._warm_pool[sandbox_id]
                    logger.info(f"Warm-pool sandbox {sandbox_id} idle for {warm_duration:.1f}s, marking for destroy")

        # Destroy active sandboxes (re-verify still idle before acting)
        for sandbox_id in active_to_destroy:
            try:
                # Re-verify the sandbox is still idle under the lock before destroying.
                # Between the snapshot above and here, the sandbox may have been
                # re-acquired (last_activity updated) or already released/destroyed.
                with self._lock:
                    last_activity = self._last_activity.get(sandbox_id)
                    if last_activity is None:
                        # Already released or destroyed by another path — skip.
                        logger.info(f"Sandbox {sandbox_id} already gone before idle destroy, skipping")
                        continue
                    if (time.time() - last_activity) < idle_timeout:
                        # Re-acquired (activity updated) since the snapshot — skip.
                        logger.info(f"Sandbox {sandbox_id} was re-acquired before idle destroy, skipping")
                        continue
                logger.info(f"Destroying idle sandbox {sandbox_id}")
                self.destroy(sandbox_id)
            except Exception as e:
                logger.error(f"Failed to destroy idle sandbox {sandbox_id}: {e}")

        # Destroy warm-pool sandboxes (already removed from _warm_pool under lock above)
        for sandbox_id, info in warm_to_destroy:
            try:
                self._backend.destroy(info)
                logger.info(f"Destroyed idle warm-pool sandbox {sandbox_id}")
            except Exception as e:
                logger.error(f"Failed to destroy idle warm-pool sandbox {sandbox_id}: {e}")

    # ── 信号处理 ──────────────────────────────────────────────────

    def _register_signal_handlers(self) -> None:
        """注册信号处理器以实现优雅关闭。

        处理SIGTERM、SIGINT和SIGHUP（终端关闭）以确保
        即使用户关闭终端时沙箱容器也会被清理。
        """
        self._original_sigterm = signal.getsignal(signal.SIGTERM)
        self._original_sigint = signal.getsignal(signal.SIGINT)
        self._original_sighup = signal.getsignal(signal.SIGHUP) if hasattr(signal, "SIGHUP") else None

        def signal_handler(signum, frame):
            self.shutdown()
            if signum == signal.SIGTERM:
                original = self._original_sigterm
            elif hasattr(signal, "SIGHUP") and signum == signal.SIGHUP:
                original = self._original_sighup
            else:
                original = self._original_sigint
            if callable(original):
                original(signum, frame)
            elif original == signal.SIG_DFL:
                signal.signal(signum, signal.SIG_DFL)
                signal.raise_signal(signum)

        try:
            signal.signal(signal.SIGTERM, signal_handler)
            signal.signal(signal.SIGINT, signal_handler)
            if hasattr(signal, "SIGHUP"):
                signal.signal(signal.SIGHUP, signal_handler)
        except ValueError:
            logger.debug("Could not register signal handlers (not main thread)")

    # ── 线程锁定（进程内） ──────────────────────────────────────

    def _get_thread_lock(self, thread_id: str) -> threading.Lock:
        """获取或创建特定thread_id的进程内锁。"""
        with self._lock:
            if thread_id not in self._thread_locks:
                self._thread_locks[thread_id] = threading.Lock()
            return self._thread_locks[thread_id]

    # ── 核心：获取 / 获取 / 释放 / 关闭 ─────────────────────────

    def acquire(self, thread_id: str | None = None, user_id: str | None = None) -> str:
        """获取沙箱环境并返回其ID。

        对于相同的thread_id，此方法将在多个回合、多个进程和（使用共享存储时）
        多个pod中返回相同的sandbox_id。

        线程安全，包括进程内和跨进程锁定。

        参数：
            thread_id：可选的线程ID，用于线程特定配置。
            user_id：可选的用户ID，用于用户隔离的技能目录。

        返回：
            获取的沙箱环境的ID。
        """
        if thread_id:
            thread_lock = self._get_thread_lock(thread_id)
            with thread_lock:
                return self._acquire_internal(thread_id, user_id)
        else:
            return self._acquire_internal(thread_id, user_id)

    def _acquire_internal(self, thread_id: str | None, user_id: str | None = None) -> str:
        """具有两层一致性的内部沙箱获取。

        第1层：进程内缓存（最快，覆盖同一进程重复访问）
        第2层：后端发现（覆盖由其他进程启动的容器；
                 sandbox_id是从thread_id确定性派生的，因此不需要共享状态文件
                 — 任何进程都可以为同一容器派生相同的容器名称）
        """
        # ── 第1层：进程内缓存（快速路径） ──
        if thread_id:
            with self._lock:
                if thread_id in self._thread_sandboxes:
                    existing_id = self._thread_sandboxes[thread_id]
                    if existing_id in self._sandboxes:
                        logger.info(f"Reusing in-process sandbox {existing_id} for thread {thread_id}")
                        self._last_activity[existing_id] = time.time()
                        return existing_id
                    else:
                        del self._thread_sandboxes[thread_id]

        # 线程特定为确定性，匿名为随机
        sandbox_id = self._deterministic_sandbox_id(thread_id) if thread_id else str(uuid.uuid4())[:8]

        # ── 第1.5层：暖池（容器仍在运行，无冷启动） ──
        if thread_id:
            with self._lock:
                if sandbox_id in self._warm_pool:
                    info, _ = self._warm_pool.pop(sandbox_id)
                    sandbox = AioSandbox(id=sandbox_id, base_url=info.sandbox_url)
                    self._sandboxes[sandbox_id] = sandbox
                    self._sandbox_infos[sandbox_id] = info
                    self._last_activity[sandbox_id] = time.time()
                    self._thread_sandboxes[thread_id] = sandbox_id
                    logger.info(f"Reclaimed warm-pool sandbox {sandbox_id} for thread {thread_id} at {info.sandbox_url}")
                    return sandbox_id

        # ── 第2层：后端发现 + 创建（受跨进程锁保护） ──
        # 使用文件锁以便两个进程竞争创建同一沙箱
        # 为同一thread_id在这里序列化：第二个进程将发现
        # 第一个进程启动的容器而不是命中名称冲突。
        if thread_id:
            return self._discover_or_create_with_lock(thread_id, sandbox_id, user_id)

        return self._create_sandbox(thread_id, sandbox_id, user_id)

    def _discover_or_create_with_lock(self, thread_id: str, sandbox_id: str, user_id: str | None = None) -> str:
        """在跨进程文件锁下发现现有沙箱或创建新沙箱。

        文件锁序列化跨多个进程为同一thread_id创建沙箱，
        防止容器名称冲突。
        """
        paths = get_paths()
        paths.ensure_thread_dirs(thread_id)
        lock_path = paths.thread_dir(thread_id) / f"{sandbox_id}.lock"

        with open(lock_path, "a", encoding="utf-8") as lock_file:
            locked = False
            try:
                _lock_file_exclusive(lock_file)
                locked = True
                # 在文件锁下重新检查进程内缓存，以防另一个
                # 线程在此进程中赢得竞争而在我们等待时。
                with self._lock:
                    if thread_id in self._thread_sandboxes:
                        existing_id = self._thread_sandboxes[thread_id]
                        if existing_id in self._sandboxes:
                            logger.info(f"Reusing in-process sandbox {existing_id} for thread {thread_id} (post-lock check)")
                            self._last_activity[existing_id] = time.time()
                            return existing_id
                    if sandbox_id in self._warm_pool:
                        info, _ = self._warm_pool.pop(sandbox_id)
                        sandbox = AioSandbox(id=sandbox_id, base_url=info.sandbox_url)
                        self._sandboxes[sandbox_id] = sandbox
                        self._sandbox_infos[sandbox_id] = info
                        self._last_activity[sandbox_id] = time.time()
                        self._thread_sandboxes[thread_id] = sandbox_id
                        logger.info(f"Reclaimed warm-pool sandbox {sandbox_id} for thread {thread_id} (post-lock check)")
                        return sandbox_id

                # 后端发现：另一个进程可能已经创建了容器。
                discovered = self._backend.discover(sandbox_id)
                if discovered is not None:
                    sandbox = AioSandbox(id=discovered.sandbox_id, base_url=discovered.sandbox_url)
                    with self._lock:
                        self._sandboxes[discovered.sandbox_id] = sandbox
                        self._sandbox_infos[discovered.sandbox_id] = discovered
                        self._last_activity[discovered.sandbox_id] = time.time()
                        self._thread_sandboxes[thread_id] = discovered.sandbox_id
                    logger.info(f"Discovered existing sandbox {discovered.sandbox_id} for thread {thread_id} at {discovered.sandbox_url}")
                    return discovered.sandbox_id

                return self._create_sandbox(thread_id, sandbox_id, user_id)
            finally:
                if locked:
                    _unlock_file(lock_file)

    def _evict_oldest_warm(self) -> str | None:
        """销毁暖池中最旧的容器以释放容量。

        返回：
            被驱逐的sandbox_id，或None如果暖池为空。
        """
        with self._lock:
            if not self._warm_pool:
                return None
            oldest_id = min(self._warm_pool, key=lambda sid: self._warm_pool[sid][1])
            info, _ = self._warm_pool.pop(oldest_id)

        try:
            self._backend.destroy(info)
            logger.info(f"Destroyed warm-pool sandbox {oldest_id}")
        except Exception as e:
            logger.error(f"Failed to destroy warm-pool sandbox {oldest_id}: {e}")
            return None
        return oldest_id

    def _create_sandbox(self, thread_id: str | None, sandbox_id: str, user_id: str | None = None) -> str:
        """通过后端创建新沙箱。

        参数：
            thread_id：可选的线程ID。
            sandbox_id：要使用的沙箱ID。
            user_id：可选的用户ID，用于用户隔离的技能挂载。

        返回：
            沙箱ID。

        引发：
            RuntimeError：如果沙箱创建或就绪检查失败。
        """
        # 指定skill upload等工作目录的挂载配置
        extra_mounts = self._get_extra_mounts(thread_id, user_id)

        # 强制执行副本：只有暖池容器计入驱逐预算。
        # 活跃沙箱由实时线程服务，我们绝不强制停止它们。
        replicas = self._config.get("replicas", DEFAULT_REPLICAS)
        with self._lock:
            total = len(self._sandboxes) + len(self._warm_pool)
        if total >= replicas:
            evicted = self._evict_oldest_warm()
            if evicted:
                logger.info(f"Evicted warm-pool sandbox {evicted} to stay within replicas={replicas}")
            else:
                # 所有插槽都被活跃沙箱占用 — 继续并记录。
                # 副本限制是软上限；我们绝不强制停止一个容器
                # 正在为线程服务的容器。
                logger.warning(f"All {replicas} replica slots are in active use; creating sandbox {sandbox_id} beyond the soft limit")
        if thread_id:
            info = self._backend.create(thread_id, sandbox_id, extra_mounts=extra_mounts or None)
        else:
            raise Exception("thread_id is required")

        # 等待沙箱就绪
        if not wait_for_sandbox_ready(info.sandbox_url, timeout=60):
            self._backend.destroy(info)
            raise RuntimeError(f"Sandbox {sandbox_id} failed to become ready within timeout at {info.sandbox_url}")

        sandbox = AioSandbox(id=sandbox_id, base_url=info.sandbox_url)
        with self._lock:
            self._sandboxes[sandbox_id] = sandbox
            self._sandbox_infos[sandbox_id] = info
            self._last_activity[sandbox_id] = time.time()
            if thread_id:
                self._thread_sandboxes[thread_id] = sandbox_id

        logger.info(f"Created sandbox {sandbox_id} for thread {thread_id} at {info.sandbox_url}")
        return sandbox_id

    def get(self, sandbox_id: str) -> Sandbox | None:
        """通过ID获取沙箱。更新最后活动时间戳。

        参数：
            sandbox_id：沙箱的ID。

        返回：
            如果找到则返回沙箱实例，否则返回None。
        """
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
            if sandbox is not None:
                self._last_activity[sandbox_id] = time.time()
            return sandbox

    def release(self, sandbox_id: str) -> None:
        """将沙箱从活跃使用中释放到暖池。

        容器保持运行以便同一线程在下一次回合时
        快速回收而无需冷启动。容器仅在副本限制强制驱逐或关闭期间停止。

        参数：
            sandbox_id：要释放的沙箱的ID。
        """
        info = None
        thread_ids_to_remove: list[str] = []

        with self._lock:
            self._sandboxes.pop(sandbox_id, None)
            info = self._sandbox_infos.pop(sandbox_id, None)
            thread_ids_to_remove = [tid for tid, sid in self._thread_sandboxes.items() if sid == sandbox_id]
            for tid in thread_ids_to_remove:
                del self._thread_sandboxes[tid]
            self._last_activity.pop(sandbox_id, None)
            # 停放在暖池 — 容器保持运行
            if info and sandbox_id not in self._warm_pool:
                self._warm_pool[sandbox_id] = (info, time.time())

        logger.info(f"Released sandbox {sandbox_id} to warm pool (container still running)")

    def destroy(self, sandbox_id: str) -> None:
        """销毁沙箱：停止容器并释放所有资源。

        与release()不同，这实际上停止了容器。用于
        显式清理、容量驱动的驱逐或关闭。

        参数：
            sandbox_id：要销毁的沙箱的ID。
        """
        info = None
        thread_ids_to_remove: list[str] = []

        with self._lock:
            self._sandboxes.pop(sandbox_id, None)
            info = self._sandbox_infos.pop(sandbox_id, None)
            thread_ids_to_remove = [tid for tid, sid in self._thread_sandboxes.items() if sid == sandbox_id]
            for tid in thread_ids_to_remove:
                del self._thread_sandboxes[tid]
            self._last_activity.pop(sandbox_id, None)
            # 也从暖池拉取如果它停放在那里
            if info is None and sandbox_id in self._warm_pool:
                info, _ = self._warm_pool.pop(sandbox_id)
            else:
                self._warm_pool.pop(sandbox_id, None)

        if info:
            self._backend.destroy(info)
            logger.info(f"Destroyed sandbox {sandbox_id}")

    def shutdown(self) -> None:
        """关闭所有沙箱。线程安全且幂等。"""
        with self._lock:
            if self._shutdown_called:
                return
            self._shutdown_called = True
            sandbox_ids = list(self._sandboxes.keys())
            warm_items = list(self._warm_pool.items())
            self._warm_pool.clear()

        # 停止空闲检查器
        self._idle_checker_stop.set()
        if self._idle_checker_thread is not None and self._idle_checker_thread.is_alive():
            self._idle_checker_thread.join(timeout=5)
            logger.info("Stopped idle checker thread")

        logger.info(f"Shutting down {len(sandbox_ids)} active + {len(warm_items)} warm-pool sandbox(es)")

        for sandbox_id in sandbox_ids:
            try:
                self.destroy(sandbox_id)
            except Exception as e:
                logger.error(f"Failed to destroy sandbox {sandbox_id} during shutdown: {e}")

        for sandbox_id, (info, _) in warm_items:
            try:
                self._backend.destroy(info)
                logger.info(f"Destroyed warm-pool sandbox {sandbox_id} during shutdown")
            except Exception as e:
                logger.error(f"Failed to destroy warm-pool sandbox {sandbox_id} during shutdown: {e}")
