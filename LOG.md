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

此节在每个文件独立验证、Conventional Commit 提交完成后汇总实际结果。

## 踩坑记录

- GitHub CLI 不在当前 PATH，使用 `C:\Program Files\GitHub CLI\gh.exe`。
- 初次 git 直连失败；首次代理重试出现 schannel 握手错误，按用户指令暂停。用户修复持久代理后恢复，先执行 `git ls-remote https://github.com/shareAI-lab/learn-claude-code HEAD` 和 `gh api user --jq .login`，两项成功才克隆。
- Windows 控制台 GBK 无法打印部分 README 字符；审查脚本改用 `python -X utf8`，原始数据以 UTF-8 保存。
- Git 全局未配置提交身份，项目内使用 GitHub 登录名和 GitHub noreply 邮箱，未修改全局身份。

## 最终验证与发布

### 发布前补充决策

- 恢复工作后读取到工作区 SOP 新增的发布条款，按“仓库名沿用项目名”发布为 `2855313278/learn-claude-code`；本地工作目录和新功能名称仍为 Agent Provider Lab。
- 相同 UTF-8 命令下，原始提交与改造分支的完整 Windows 测试均有 124 次失败，失败用例标识集合一致（121 个唯一标识，部分 unittest subtest 重复计数），新增模块没有引入上游回归。最初未启用 `-X utf8` 时为 123 次失败，差异来自上游子进程编码测试。
- 发现上游 `web/src/data/generated/versions.json` 的源码快照未跟随最近的课程提交刷新，导致跨平台的生成元数据测试失败。在新模块与上层 CLI/API 切换完成后，使用原有生成器在独立 worktree 生成，只同步这一份派生产物并单独提交；原课程实现不变。这是生成产物更新，不删除或替换旧课程实现。
- 其余验证与发布结果待下方记录。
