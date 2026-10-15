# Agent Provider Lab 改造记录

## 来源、边界与决策（2026-10-15）

- 原项目：[shareAI-lab/learn-claude-code](https://github.com/shareAI-lab/learn-claude-code)。
- 基线提交：`ce8f9f186058939da54c9d6fead78dfb5d0fd6c3`。
- 原作者：shareAI Lab 及原项目贡献者；原 MIT LICENSE 与全部历史保留。
- 改造维护者：2855313278；本次实现由 Codex 协助完成。
- 开工先执行 `git checkout -b refactor/auto`；所有改造均在该分支进行。
- 上游 CONTRIBUTING.md 针对向上游提交 PR，要求课程保持简洁。本项目是独立衍生仓库，不提交上游 PR；新功能放入独立 `provider_lab/`，不修改现行与旧版课程代码、三语课程文档或已有 Web 应用。

## 搜索与选品体检

使用 `gh search repos`，条件为 `topic:agent language:Python stars:>500 pushed:>2026-04-01 archived:false`。
整段 query 的首次返回混入非 Python、超期仓库；改为等价独立参数核验：

```powershell
gh search repos 'pushed:>2026-04-01' --topic agent --language Python --stars '>500' --archived=false --limit 30 --json fullName,description,stargazersCount,pushedAt,url,license,language
```

对返回前 10 名，通过 GitHub GraphQL 读取 README、根目录、LICENSE 信息、默认分支过去六个月提交数量和最近更新的 5 个 Issue（每个前 5 条评论）。原始查询结果保存在改造工作区 `work/search.json`、`work/audit.json`，不作为运行依赖。
评分是针对本次 Windows 本机可验证改造任务的人工判断，不是项目质量排名。README 项为文档与执行前置条件审查；除最终选中项目外，没有实际安装全部候选或声称端到端跑通。Issue 样本不能代表整个社区响应情况。

| 候选 | README 可复现条件 /35 | 活跃 /15 | Issue 回复 /15 | 协议偏好 /20 | 结构 /15 | 总分 | 观察 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| shareAI-lab/learn-claude-code | 33 | 13 | 0 | 20 | 14 | 80 | MIT，166 次半年提交；最近 5 个 Issue 无评论，轻量 Python 课程、有 tests 与 CI |
| headroomlabs-ai/headroom | 22 | 15 | 7 | 20 | 15 | 79 | Apache-2.0，2691 次；有协作者回复，Python/Rust 混合、运行模式较多 |
| unslothai/unsloth | 13 | 15 | 15 | 20 | 15 | 78 | Apache-2.0，5993 次；5/5 样本有成员回复，大模型/GPU/平台成本较高 |
| TauricResearch/TradingAgents | 22 | 13 | 7 | 20 | 15 | 77 | Apache-2.0，341 次；有成员回复，多模型与金融数据服务前置条件 |
| bytedance/deer-flow | 20 | 15 | 6 | 20 | 15 | 76 | MIT，2257 次；有贡献者回复，后端/前端/容器依赖较多 |
| bojieli/ai-agent-book | 23 | 14 | 5 | 20 | 12 | 74 | Apache-2.0，1686 次；有贡献者回复，跨章节多套依赖及外部实验 |
| hiyouga/LlamaFactory | 15 | 12 | 7 | 20 | 15 | 69 | Apache-2.0，110 次；有 OWNER 回复，训练栈验证成本高 |
| AstrBotDevs/AstrBot | 18 | 15 | 13 | 12 | 15 | 73 | AGPL-3.0，909 次；有成员回复，完整聊天平台，改造面较大 |
| calesthio/OpenMontage | 18 | 13 | 5 | 12 | 13 | 61 | AGPL-3.0，394 次；有贡献者回复，需要多媒体工具链 |
| datawhalechina/hello-agents | 19 | 12 | 6 | 6 | 11 | 54 | 160 次；有协作者回复，API 协议识别 NOASSERTION，教程为主 |

### 基线与三个可改造点

技术栈：Python、Anthropic SDK、python-dotenv、PyYAML；另有独立 Next.js 教学站点。
已有 pytest 测试和 GitHub Actions，因此不把“补上不存在的测试/CI”当作创新。
候选改造点：① Provider 协议与本地模型；② 非交互本地 API；③ 统一预算与可回放执行记录。

选取 SOP 中的两个方向：**模型 Provider 抽象**和**CLI 增加 API 层**。第三点作为这两个方向的运行机制。
原因：原脚本直接耦合 Anthropic 消息对象、环境变量和交互输入；独立新模块可以保持教学代码简洁，同时展示可复用接口与可测试服务。

### 实施方案（在新增核心模块之前记录）

从底层到上层逐文件新增：`providers.py`（统一消息与适配器）→ Provider 合约测试 → `runtime.py`（只读工具、预算和轨迹）→ runtime 测试 → `api.py`（本机 JSON HTTP）→ API 测试 → `__main__.py`（CLI）→ CLI 测试 → CI 与文档。
新目录采用 Python namespace package，不需要空的 `__init__.py`。
Provider：Anthropic、Ollama 原生 `/api/chat`、无网络 demo。不会自动使用或输出用户密钥；在线模式显式选择，模型由参数配置。
API：仅绑定 loopback，不接受请求指定模型端点或服务器目录；每次请求独立历史，读取文件限于服务启动时配置的目录，不提供 shell/写入工具。
测试使用协议模拟与真实本地 HTTP，不需要付费密钥或下载模型。没有真实云密钥/本地模型时，明确区分合约验证与在线推理验证。

## 逐文件执行与验证

表中 `python` 指项目虚拟环境解释器（Windows：`.\.venv\Scripts\python.exe`）。每个文件完成验证后单独提交；修复与格式调整也有独立 Conventional Commit。完整提交时间与内容可用 `git log --stat ce8f9f1..HEAD` 核对。

| 文件 | 修改内容与原因 | 影响范围 | 已运行的验证命令 / 结果 | 踩坑与边界 |
| --- | --- | --- | --- | --- |
| `LOG.md` | 开工先记录选品、方向、核心方案，最后汇总实际执行与发布 | 审查与复现 | UTF-8 读取、必备章节与文件覆盖检查；通过 | 评分是任务适配度，不冒充所有候选实际运行结果 |
| `provider_lab/providers.py` | 新增 `Turn`、`ToolCall`、Provider 协议；Claude、Ollama、demo 适配 | 新 runtime 与入口 | `python -m pytest tests/test_lab_providers.py -q`；21 通过；新增时另有导入和 demo 冒烟 | 修复 Ollama 非对象 message；远端错误不回显响应正文；在线模型需显式指定 |
| `tests/test_lab_providers.py` | SDK 响应映射、工具结果配对、真实本地 HTTP、错误响应与代理测试 | Provider 合约 | 同上；21 通过 | 大字节参数必须设置简短 pytest ID，避免 Windows 超长参数名错误 |
| `provider_lab/runtime.py` | 新增只读目录工具、步数预算、每次请求独立历史与 JSON 轨迹 | CLI/API 公用执行逻辑 | `python -m compileall -q provider_lab`；demo 两步工具往返；runtime 测试通过 | 路径约束用于可信本机目录，不是 OS 沙箱；输出截断与预算耗尽不报告完成 |
| `tests/test_lab_runtime.py` | 预算、重复调用 ID、多工具配对、输入边界、路径与读取上限测试 | runtime 行为 | `python -m pytest tests/test_lab_runtime.py -q`；本机 28 通过、1 跳过 | 本机无 symlink 创建权限，相关用例在 Linux CI 运行 |
| `provider_lab/api.py` | 新增 loopback JSON 服务、输入校验、独立会话、两并发上限 | 本机 HTTP 客户端 | 真实 loopback 绑定冒烟；`python -m pytest tests/test_lab_api.py -q`；21 通过 | 拒绝 Origin、非本机 Host、配置覆盖与超限请求；不提供公网认证能力 |
| `tests/test_lab_api.py` | HTTP 状态、中文 JSON、隔离、并发限制及错误处理测试 | API 合约 | 同上；21 通过 | 使用临时端口与真实 HTTPConnection，不依赖外网 |
| `provider_lab/__main__.py` | 新增 `run` / `serve` 入口、明确退出码、UTF-8 JSON 输出 | 命令行运行 | `python -m provider_lab --help`；实际 demo 运行；CLI 测试通过 | 配置错误退出 2；未完成退出 1；不自动读取上游 `.env` |
| `tests/test_lab_cli.py` | 子进程测试中文输出、错误码、实际服务启动和健康检查 | CLI 端到端 | `python -m pytest tests/test_lab_cli.py -q`；8 通过 | 添加显式 `check=False` 并格式化；Windows 后台进程隐藏运行 |
| `ruff-lab.toml` | 新增独立 lint/格式设置，避免继承个人机器配置 | 新代码与新测试 | `python -m ruff check --config ruff-lab.toml provider_lab tests/test_lab_providers.py tests/test_lab_runtime.py tests/test_lab_api.py tests/test_lab_cli.py`；通过 | 不重新格式化原课程代码 |
| `requirements-lab.txt` | 固定已安装验证的 Python 包版本 | 开发/测试环境 | `python -m pip install -r requirements-lab.txt`；`python -m pip check`；通过 | 来自本机 Python 3.13 环境；跨版本安装由 CI 验证 |
| `scripts/verify_lab.py` | 新增顺序验收入口；`--upstream` 包含完整上游测试 | 本机与 CI | `python -X utf8 scripts/verify_lab.py`；依赖/lint/格式/编译与 96 项测试通过，1 项跳过 | Windows 默认跑新模块及可移植上游检查；完整上游套件在 Linux 跑 |
| `.github/workflows/provider-lab.yml` | 新增 Ubuntu 3.11/3.13 与 Windows 3.13 矩阵；固定 Actions SHA | 自动验收 | 用 PyYAML 校验事件、三项矩阵及只读权限；远端结果在发布节记录 | 保留原 CI 与原 Web 构建工作流 |
| `README.upstream.md` | 在重写入口前完整备份原 README | 上游课程入口与作者信息 | 比较 `git show ce8f9f1:README.md` 与备份 Git blob；逐字节相同 | 原贡献者、课程与推广链接均保留在备份中 |
| `README.md` | 重写新架构、快速开始、模型配置、API、目录与限制 | 新项目入口 | 检查本地链接、代码围栏、出处；实际运行 CLI 与 PowerShell HTTP 示例；通过 | 在线推理未实测，明确标注；仓库 URL 按更新后的 SOP 沿用原项目名 |
| `web/src/data/generated/versions.json` | 最后阶段用原生成器刷新过期源码快照与元数据 | 原教学网站生成索引 | `python -m pytest tests/test_web_scenarios.py::test_generated_s16_metadata_extends_s15_without_registry_false_positives -q`；1 通过 | 在独立 worktree 生成后，只同步此文件；未改课程源码 |
| `web/src/data/generated/docs.json` | 同步生成器发现的 s07 三语文档快照更新 | 原教学网站文档数据 | JSON 解析、51 条文档完整性、变更仅 s07/en/zh/ja；通过；`npx tsc --noEmit` 与 Web build 通过 | 同一生成过程产生的必要派生数据一致性更新，单独提交 |

### 验证环境与复现命令

本机 Windows / Python 3.13.9 / Node.js 24.17.0。新模块的跨平台 CI 目标为 Python 3.11/3.13。

```powershell
Set-Location 'D:\GitHub改造工作区\projects\agent-provider-lab'
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lab.txt
.\.venv\Scripts\python.exe -X utf8 scripts/verify_lab.py
.\.venv\Scripts\python.exe -m provider_lab run '列出工作区文件' --provider demo --workspace .
.\.venv\Scripts\python.exe -m provider_lab serve --provider demo --workspace . --port 8000
```

最后一条启动服务后，在另一个 PowerShell 窗口按 README 的 `Invoke-RestMethod` 示例访问 `/health` 和 `/v1/runs`。实际验证使用临时端口，避免占用用户的 8000 端口；已确认 `status=completed`、`steps=2`，验证后停止测试服务。

Linux 完整 Python 验证：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lab.txt
.venv/bin/python scripts/verify_lab.py --upstream
```

原 Web 网站检查（在独立基线 worktree 执行，源码与发布目录一致，生成的两个 JSON 已同步）：

```powershell
Set-Location web
npm ci --no-audit --no-fund
npx tsc --noEmit
$env:NEXT_TELEMETRY_DISABLED = '1'
npm run build
```

TypeScript 检查与生产构建通过，生成 118 个静态页面。生成器的清理目标已先核验为该 worktree 内的 `web/public/course-assets`，且不是 reparse point。

## 踩坑记录

- GitHub CLI 不在当前 PATH，使用 `C:\Program Files\GitHub CLI\gh.exe`。
- 初次 git 直连失败；首次代理重试出现 schannel 握手错误，按用户指令暂停。用户修复持久代理后恢复，先执行 `git ls-remote https://github.com/shareAI-lab/learn-claude-code HEAD` 和 `gh api user --jq .login`，两项成功才克隆。
- Windows 控制台 GBK 无法打印部分 README 字符；审查脚本改用 `python -X utf8`，原始数据以 UTF-8 保存。
- Git 全局未配置提交身份，项目内使用 GitHub 登录名和 GitHub noreply 邮箱，未修改全局身份。
- Windows 原始完整测试依赖 `fcntl`、POSIX 路径/进程行为、LF 换行和 symlink 权限。相同环境下的基线比较确认失败集合相同；没有靠删除/修改上游测试来掩盖这些限制。
- 原 Web 生成索引与最近课程源码提交不同步；使用原生成器刷新版本索引及 s07 三语文档数据，修复生成元数据检查。
- Next.js 首次构建在本机遥测配置目录遇到 `EXDEV`；设置 `NEXT_TELEMETRY_DISABLED=1` 后构建成功，无需修改站点源码。
- 没有真实云密钥与本地模型的在线验证结果；不把 demo 或协议模拟称为真实推理成功。

## 最终验证与发布

### 发布前补充决策

- 恢复工作后读取到工作区 SOP 新增的发布条款，按“仓库名沿用项目名”发布为 `2855313278/learn-claude-code`；本地工作目录和新功能名称仍为 Agent Provider Lab。
- 相同 UTF-8 命令下，原始提交与改造分支的完整 Windows 测试均有 124 次失败，失败用例标识集合一致（121 个唯一标识，部分 unittest subtest 重复计数），新增模块没有引入上游回归。最初未启用 `-X utf8` 时为 123 次失败，差异来自上游子进程编码测试。
- 发现上游 `web/src/data/generated/versions.json` 的源码快照未跟随最近的课程提交刷新，导致跨平台的生成元数据测试失败。在新模块与上层 CLI/API 切换完成后，使用原有生成器在独立 worktree 生成；还发现 `docs.json` 中 s07 三语快照滞后，因此将两份派生产物分别验证并分别提交；原课程实现不变。这是生成产物更新，不删除或替换旧课程实现。
- 本地发布验收：96 通过、1 跳过；依赖、lint、格式、编译、CLI/API 示例与原 Web 生产构建均通过。完整 Windows 上游测试的既有平台限制仍存在；不将局部验收说成 Windows 全套零失败。
- 发布目标：`https://github.com/2855313278/learn-claude-code`，public，默认分支 main；保留 refactor/auto 改造提交历史。远端创建、推送、描述/topics 与 CI 状态将在完成后记录。

### 一句话创新点

在保留原 Agent 课程的基础上，实现支持 Claude 与 Ollama 的统一 Provider、可追踪且有调用预算的只读执行循环，并通过同一套 CLI/API 提供可离线复现的仓库助手。
