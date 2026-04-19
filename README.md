# DeepAgent Skills Framework

一个基于 LangGraph 的智能 Agent 框架，支持渐进式披露的技能系统和安全的沙箱执行环境。

## 特性

### 🧠 智能技能系统
- **渐进式披露** - 技能元数据始终可见，但完整指令仅在需要时加载
- **用户隔离** - 每个用户拥有独立的技能目录，互不干扰
- **热插拔** - 支持动态加载和卸载技能，无需重启系统
- **多格式支持** - 支持脚本、参考文档、资源文件等多种技能资源

### 🛡️ 安全沙箱环境
- **容器化执行** - 基于 Docker 的隔离执行环境
- **文件操作保护** - 安全的文件读写、搜索操作
- **网络访问控制** - 可配置的网络访问策略
- **资源限制** - CPU、内存、磁盘使用限制

### 🔌 中间件系统
- **技能中间件** - 自动加载和注入技能到系统提示
- **沙箱中间件** - 管理 Agent 和沙箱的交互
- **错误处理中间件** - 优雅处理 LLM 和工具调用错误
- **线程数据中间件** - 管理会话状态和上下文

### 🤖 多模型支持
- **GLM 系列** - 支持 GLM-4、GLM-5 等智谱 AI 模型
- **OpenAI 兼容** - 支持所有 OpenAI API 兼容的模型
- **流式输出** - 实时响应和思考过程展示

## 项目结构

```
test_skill/
├── agents/                    # Agent 核心代码
│   ├── middleware/            # 中间件实现
│   │   ├── skills_middleware.py       # 技能系统中间件
│   │   ├── sandbox_middleware.py       # 沙箱管理中间件
│   │   └── thread_data_middleware.py   # 会话状态中间件
│   ├── sandbox/              # 沙箱环境
│   │   ├── tools.py          # 沙箱工具定义
│   │   └── sandbox_provider.py  # 沙箱提供者
│   └── config/               # 配置管理
├── skills/                   # 技能目录
│   ├── global/              # 全局共享技能
│   └── example-user/        # 用户专属技能
└── tests/                   # 测试脚本
```

## 快速开始

### 环境要求

- Python >= 3.12
- Docker (用于沙箱环境)

### 安装

```bash
# 克隆项目
git clone <repository-url>
cd test_skill

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# 或 .venv\Scripts\activate  # Windows

# 安装依赖
pip install -e .
```

### 配置

创建 `.env` 文件：

```bash
# GLM 模型配置
GLM_API_KEY=your_api_key_here
GLM_API_BASE=https://open.bigmodel.cn/api/paas/v4/

# 沙箱配置
SANDBOX_ENABLED=true
```

### Docker 配置

#### 拉取沙箱镜像

项目使用字节跳动的 AIO 沙箱镜像，需要先拉取：

```bash
# 拉取官方沙箱镜像
docker pull enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest
```

#### 验证 Docker 安装

```bash
# 检查 Docker 是否运行
docker ps

# 检查 Docker 版本
docker --version

# 检查镜像是否已下载
docker images | grep all-in-one-sandbox
```

#### 镜像说明

| 镜像 | 用途 | 大小 | 说明 |
|------|------|------|------|
| `enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest` | AIO 沙箱环境 | ~2GB | 官方镜像，包含完整的工具链和 Python 环境 |

#### 容器配置

沙箱容器自动配置以下参数：

```bash
# 容器名称前缀
DEER_FLOW_SANDBOX_PREFIX=deer-flow-sandbox

# 基础端口（会自动分配可用端口）
SANDBOX_BASE_PORT=8080

# 空闲超时（秒）
SANDBOX_IDLE_TIMEOUT=600

# 最大并发容器数
SANDBOX_REPLICAS=3
```

#### 挂载目录

沙箱会自动挂载以下目录到容器内：

| 宿主机路径 | 容器路径 | 说明 |
|-----------|----------|------|
| `./data/threads/{thread_id}/workspace` | `/mnt/user-data/workspace` | 工作目录 |
| `./data/threads/{thread_id}/uploads` | `/mnt/user-data/uploads` | 上传文件 |
| `./data/threads/{thread_id}/outputs` | `/mnt/user-data/outputs` | 输出文件 |
| `./skills/{user_id}` 或 `./skills/global` | `/mnt/skills` | 技能目录（只读） |

#### 检查运行中的容器

```bash
# 查看所有运行中的沙箱容器
docker ps | grep deer-flow-sandbox

# 查看容器日志
docker logs <container_name>

# 进入容器调试
docker exec -it <container_name> /bin/bash
```

### 运行测试

```bash
# 运行基础测试
python tests/run_tests.py
```

## 技能系统

### 技能结构

每个技能是一个包含 `SKILL.md` 文件的目录：

```
skill-name/
├── SKILL.md          # 必需：技能定义文件
├── scripts/          # 可选：可执行脚本
├── references/       # 可选：参考文档
└── assets/           # 可选：资源文件
```

### SKILL.md 格式

```markdown
---
name: skill-name
description: 技能的简短描述，说明何时使用
---

# 技能标题

## 使用时机
- 用户请求特定任务时

## 使用方法
...
```

### 创建新技能

使用 `skill-creator` 技能来创建新技能：

```python
# 在对话中让 Claude 使用 skill-creator
# "使用 skill-creator 创建一个 XXX 技能"
```

### 技能加载规则

1. **全局技能** - `skills/global/` 目录下的技能对所有用户可用
2. **用户技能** - `skills/{user_id}/` 目录下的技能仅对特定用户可用
3. **优先级** - 用户技能优先于全局技能

## 中间件开发

### 创建自定义中间件

```python
from langchain.agents.middleware import AgentMiddleware
from agents.middleware.types import ModelRequest, ModelResponse

class MyCustomMiddleware(AgentMiddleware):
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable,
    ) -> ModelResponse:
        # 修改请求
        modified_request = request.override(...)
        # 调用下一个处理器
        return handler(modified_request)
```

### 中间件执行顺序

1. **before_agent** - Agent 执行前
2. **wrap_model_call** - 模型调用前/后
3. **after_agent** - Agent 执行后

## 沙箱工具

### 可用工具

- `bash` - 执行 Shell 命令
- `ls` - 列出目录内容
- `glob` - 文件模式匹配
- `grep` - 文件内容搜索
- `read_file` - 读取文件内容
- `write_file` - 写入文件
- `str_replace` - 字符串替换编辑

### 工具权限

工具在沙箱内执行，具有以下限制：
- 只能访问工作目录
- 文件大小限制
- 执行超时限制
- 网络访问控制

## GLM 模型兼容性

### Reasoning/Thinking 处理

GLM 模型返回的 `reasoning` 和 `thinking` 内容块会被自动处理：

- **reasoning** - 转换为普通文本并添加前缀
- **thinking** - 被过滤，不发送给模型
- **text/image_url** - 保留原样

### 消息清理

中间件会自动清理不符合 OpenAI API 规范的内容块，确保消息兼容性。

## 故障排除

### 常见问题

**Q: 沙箱启动失败**
```
A: 检查 Docker 是否运行，端口是否被占用
```

**Q: 技能加载失败**
```
A: 检查 SKILL.md 格式是否正确，YAML frontmatter 是否有效
```

**Q: 模型调用错误**
```
A: 检查 API 密钥和 API 基础 URL 是否正确
```

## 开发指南

### 代码风格

- 使用类型注解
- 编写文档字符串
- 遵循 PEP 8 规范

### 测试

```bash
# 运行所有测试
python -m pytest tests/

# 运行特定测试
python tests/run_tests.py
```

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！

## 更新日志

### v0.1.0 (2024-04-19)
- ✨ 初始版本发布
- ✨ 实现技能系统
- ✨ 实现沙箱环境
- ✨ 支持 GLM 模型
- 🐛 修复消息兼容性问题
