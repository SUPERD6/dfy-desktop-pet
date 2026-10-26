# dsh-whale-bridge — 大肥鱼桌宠 × DSH 只读状态桥

一个 **只读** 的 DSH Host 插件：DSH 里跑 Agent 时，它把「正在思考 / 调用工具 / 成功 / 失败 / 等待审批」这类**粗粒度状态**投影成两个等价出口，供现有的 Python 桌宠轮询：

1. **固定状态文件**（桌宠的主入口，不依赖端口）：
   `%USERPROFILE%\.dsh\whale-bridge\status.json`
2. **Loopback HTTP 路由**（用于验证 / future consumer）：
   `GET http://127.0.0.1:<当前 DSH GUI 端口>/api/whale/status`

> DSH Desktop 每次启动都随机分配 GUI 端口（`--port 0`，本次是 52208，重启后会变），所以**不要写死端口**；桌宠请读状态文件。

它不接模型、不做 UI、不改桌宠、不读任何用户内容。**已于 2026-10-25 11:38 安装到本机 desktop profile，并在运行中的 DSH 上热加载生效**（无需重启；详见 INSTALL.md 顶部记录）。

---

## 1. 交付物

| 路径 | 作用 |
|---|---|
| `plugin/dsh-whale-bridge/` | 可安装的 DSH Host 插件（纯 ESM，零运行时依赖） |
| `plugin/dsh-whale-bridge/lib/status.js` | 纯函数 reducer：DSH session 事件 → 公开状态对象 |
| `plugin/dsh-whale-bridge/lib/index.js` | Cordis 插件（`apply`/`inject`/`name`）、路由、原子写文件、5s 心跳 |
| `plugin/dsh-whale-bridge/cordis.patch.yml` | bundle 层：插入一行 Host 插件 |
| `plugin/dsh-whale-bridge/tests/status.test.mjs` | `node:test` 测试（假 ctx + 隐私红线） |
| `plugin/INSTALL.md` | desktop profile 的安装 / 验证 / 回滚精确命令 |
| `plugin/verify-live.ps1` | 重启 DSH 后：读状态文件 + 自动发现新端口 + 请求路由 + 一致性检查 |
| `plugin/dsh-whale-bridge-0.1.0-source.zip` | 插件源码备份（工作区被外部清理误删过一次，恢复用；安装用上面的目录，不要用 zip） |
| `research/dsh-plugin-api.md` | 0.2.1-rc.3 插件 API 调研笔记（含核对来源） |

## 2. 状态机

`state` 只有 6 个值：

| state | 触发（DSH session 事件） | 桌宠建议表现 |
|---|---|---|
| `idle` | 初始 / 插件启动 / turn 被中止（aborted、blocked、max-tokens 等） | 待机呼吸 |
| `running` | `turn/start`、`step/start`、工具成功返回、审批结束后 | 思考 / 分析 |
| `tool` | `tool/call`（带 `lastTool`）、`command/run`（`lastTool: "command"`） | 动手干活 |
| `success` | `turn/end` 且 `reason.kind === "completed"`（或旧信封无 reason） | 完成庆祝 |
| `failure` | `tool/result` 带 `error`/`message.isError`，或 `turn/end` 的 `reason.kind === "error"` | 失败反馈 |
| `approval` | `approval/asked`（`approvalOpen: true`） | 等用户点确认 |

状态对象（8 个字段，顺序固定，也是状态文件的完整内容）：

```json
{"ok":true,"state":"tool","lastTool":"pwsh","doneCount":3,"errorCount":1,"approvalOpen":false,"updatedAt":1792942038743,"version":1}
```

- `ok`：桥自身健康标记（恒为 `true`；写文件失败只会写 DSH 日志，不污染状态）
- `lastTool`：**仅工具名**（去除控制字符、截断 64 字符）；`command/run` 只暴露 `"command"` 类别，不暴露命令名/参数
- `doneCount` / `errorCount`：本插件启动以来的完成轮数 / 错误数
- `approvalOpen`：是否有待审批问题
- `updatedAt`：最后一次状态变化的 Unix 毫秒时间戳（**心跳不刷新它**，所以它能区分「状态没变」和「DSH 还活着」）
- `version`：公开 schema 版本（当前 `1`）

**写盘策略**：状态变化立即写 + 每 5 秒心跳重写（`heartbeatMs` 可配）。写入是原子替换（同目录唯一临时文件 `wx` 创建 → `rename` 覆盖，Windows 瞬态 `EACCES/EBUSY/EPERM` 有界重试），读方永远看到完整 JSON。

## 3. 隐私红线（已用测试钉死）

状态对象/状态文件/路由响应里**只会**出现：类别、工具名、计数、时间戳。以下内容在 reducer 里被结构性忽略，绝不转发、绝不写入：

- 用户提示词（`user/message`、`system/message`、`developer/message`）
- 工具参数（`tool/call.arguments`）与工具输出（`tool/result.message`）
- 文件路径（`meta`、工具结果里的路径）
- `todo/write` 的任务正文、`goal/change` 的 objective
- `command/run.args`、`command/done.text`
- `approval/asked.reason`、`approval/decided` 结果细节

测试里专门塞入 `SECRET_PROMPT`、`C:\secret\path` 作为哨兵，断言状态文件原文、路由 JSON、reducer 输出都不包含它们（见下方测试输出里的 *privacy red team*）。

## 4. 安装 / 验证 / 回滚

见 **[INSTALL.md](INSTALL.md)**（针对 `desktop` profile 的精确命令、备份、回滚、失败排查）。

**实际安装记录（2026-10-25 11:38，Lead 执行）**：在 DSH 运行中直接 `dsh.cmd plugin --profile desktop add ...` 成功；`dependencies` 写入 `link:`、`dsh.profile.bundles` 17 项、`cordis.patch.yml` 未改；运行中的实例随后热加载（状态文件出现、路由 200、`verify-live.ps1` PASS）。备份：`plugin/backup/20261025-1138/`。回滚：`dsh plugin --profile desktop remove dsh-whale-bridge`。

一句话版（以后的干净安装，仍建议先退出 DSH）：

```powershell
# 完全退出 DSH Desktop 后：
& "<DSH 安装目录>\resources\runtime\cli\bin\dsh.cmd" plugin --profile desktop add "<仓库目录>\plugin\dsh-whale-bridge"
# 启动 DSH Desktop 后：
powershell -NoProfile -File "<仓库目录>\plugin\verify-live.ps1"
```

## 5. 本机验证记录（实际输出）

环境：Windows 11 / DSH Desktop `0.2.1-rc.3` / Node `v24.21.0`（bundled）/ profile `desktop`。
所有验证都在临时目录或只读路径完成，**没有安装到用户正在使用的实例，没有改 `~/.dsh`**。

### 5.1 语法检查 + 单元/集成测试（全部通过）

```text
PS> $n = "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"
PS> node --check plugin/dsh-whale-bridge/lib/status.js       -> exit 0
PS> node --check plugin/dsh-whale-bridge/lib/index.js        -> exit 0
PS> node --check plugin/dsh-whale-bridge/tests/status.test.mjs -> exit 0
PS> node --test plugin/dsh-whale-bridge/tests/status.test.mjs
✔ initial state is exactly the public contract (0.8623ms)
✔ reducer migrations: turn/start → tool → failure → success (0.3021ms)
✔ reducer is pure and ignores malformed / unknown events (0.0973ms)
✔ turn/end reasons: completed → success, error → failure, aborted → idle (0.1123ms)
✔ tool names are sanitized (length, control characters, non-strings) (0.139ms)
✔ plugin mounts: turn/start → tool → tool error → turn/end success (file + route) (90.6758ms)
✔ tool/result failure detection also uses message.isError (29.5551ms)
✔ approval lifecycle: asked → approval (open), decided → running (closed) (46.4291ms)
✔ command lifecycle exposes the category only, never the command name/args/text (31.2725ms)
✔ privacy red team: every content-bearing event is stripped (31.6182ms)
✔ heartbeat rewrites the file while the state stays unchanged (61.01ms)
✔ writes are atomic and leave no temp files (15.3107ms)
✔ rapid events serialize: the last write always reflects the latest state (15.2481ms)
✔ writeFileAtomic creates parent directories and replaces content (3.7803ms)
✔ route method handling: GET/HEAD 200, POST 405 (0.2148ms)
✔ dispose: final idle snapshot, timer stopped, route removed (28.9729ms)
✔ configuration: statusFile resolution and heartbeat clamping (0.2586ms)
✔ malformed listener payloads cannot break the bridge (27.8213ms)
✔ empty-but-live bridge: initial file is already written before any event (15.3787ms)
ℹ tests 19
ℹ suites 0
ℹ pass 19
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 456.763
```

连续重复跑 5 次均为 19/19 通过（早期版本有一次 Windows 文件系统抖动导致 flaky，已通过写入合并 + 更宽超时修掉）。

### 5.2 真实 `node:http` 路由冒烟（事件 → 文件 → 网络响应）

把插件挂到临时 HTTP server（假 ctx，真实端口）上，按事件序列走一遍并 `fetch` 路由：

```text
listening http://127.0.0.1:59666
idle      => 200 {"ok":true,"state":"idle","lastTool":null,"doneCount":0,"errorCount":0,"approvalOpen":false,"updatedAt":1792942038642,"version":1}
running   => 200 {"ok":true,"state":"running","lastTool":null,"doneCount":0,"errorCount":0,"approvalOpen":false,"updatedAt":1792942038668,"version":1}
tool      => 200 {"ok":true,"state":"tool","lastTool":"pwsh","doneCount":0,"errorCount":0,"approvalOpen":false,"updatedAt":1792942038743,"version":1}
failure   => 200 {"ok":true,"state":"failure","lastTool":"pwsh","doneCount":0,"errorCount":1,"approvalOpen":false,"updatedAt":1792942038805,"version":1}
success   => 200 {"ok":true,"state":"success","lastTool":"pwsh","doneCount":1,"errorCount":1,"approvalOpen":false,"updatedAt":1792942038868,"version":1}
status file: {"ok":true,"state":"success","lastTool":"pwsh","doneCount":1,"errorCount":1,"approvalOpen":false,"updatedAt":1792942038868,"version":1}
privacy check (no 'secret' in file/route): PASS
HEAD status: 200 body bytes: 0
POST status: 405 allow: GET, HEAD
```

### 5.3 用 DSH 0.2.1-rc.3 自己的 bundle 机制校验挂载声明

用 Electron `ELECTRON_RUN_AS_NODE=1` 只读加载 `app.asar` 里的 `@deepseek-ai/dsh-app-boot`，把本包的 `dsh.bundle` 声明喂给 DSH 自己的解析函数：

```text
dsh.bundle = {"patch":"./cordis.patch.yml"}
bundlePatchFiles = ["./cordis.patch.yml"]
bundlePatchPaths = ["<仓库目录>\\plugin\\dsh-whale-bridge\\cordis.patch.yml"]
loadOverlayPatches(cordis.patch.yml) = [{"insert":[{"id":"dsh-whale-bridge","name":"dsh-whale-bridge"}]}]
```

### 5.4 模拟 desktop profile 组合（临时目录，真 profile 只读）

把真实 `desktop/package.json` 复制到 `%TEMP%` 的 mock profile，加 `dsh-whale-bridge` 依赖（`link:`）与 `dsh.profile.bundles` 条目，junction 到插件目录，再调用 DSH 自己的 `loadProfileDirectory` + `composeEntries`：

```text
loaded layers: 15 | skipped: [两个未安装的 @deepseek-ai/dsh-subagent-* 包（mock 环境正常现象）]
our bundle layer: {"packageDir":"...\\node_modules\\dsh-whale-bridge","patchPaths":["...\\cordis.patch.yml"],"patches":[{"insert":[{"id":"dsh-whale-bridge","name":"dsh-whale-bridge"}]}]}
composed entry: {"id":"dsh-whale-bridge","name":"dsh-whale-bridge"}
composed entry count: 213
```

即：**只要安装步骤把包写进 `dependencies` 和 `dsh.profile.bundles`，0.2.1-rc.3 的组合器就会把这一行挂进 213 项的桌面配置树。**

### 5.5 `verify-live.ps1` 三态自测（Windows PowerShell 5.1）

```text
A) 状态文件不存在        -> 打印缺失提示，exit 1
B) 合法假状态文件 -SkipHttp -> PASS，exit 0
C) 合法假状态文件、插件未装  -> 自动发现候选端口 9230, 52208，路由不可达，exit 2
```

C 的端口发现逻辑在**当前运行中的 DSH Desktop 进程**上实测有效（PID → `Get-NetTCPConnection` → 候选端口列表）；安装并重启后再跑就是完整 PASS。

## 6. 桌宠侧接入建议（Python，轮询状态文件）

```python
import json
import pathlib
import time

STATUS = pathlib.Path.home() / ".dsh" / "whale-bridge" / "status.json"
STALE_MS = 15_000  # 5s 心跳 + 容错；超过视为 DSH 不在

def read_status():
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

def poll():
    payload = read_status()
    if payload is None or time.time() * 1000 - payload["updatedAt"] > STALE_MS:
        return {"ok": False, "state": "idle"}
    return payload
```

映射建议：`running` → 思考动画；`tool` 按 `lastTool` 选道具；`success` 播一次庆祝后回 `idle`；`failure` 播失败反馈；`approval` 等她求确认。`doneCount` 变化可做完成音效，`errorCount` 变化可做失败提示。

> 插件对桌宠**只读**：桌宠不需要写任何东西，也不需要知道 DSH 端口。

## 7. 已知限制

- **单状态机**：多个会话同时跑时状态是全局合并的（后到事件覆盖先到），不做 per-session 聚合。桌宠场景够用；要精确的话可作为下一版。
- **DSH 退出即停心跳**：状态文件停留在最后状态（插件 dispose 时会写一次 `idle`，但 Electron 强杀时不会），桌宠侧必须用 `updatedAt` 判断 15s 陈旧，否则会一直演"干活中"。
- **路由无鉴权**：`/api/whale/status` 是 webServer 的 exact 路由，优先于 `connection` 的 `/api` 前缀路由，因此不经过其 loopback/token 策略。它只绑定在 127.0.0.1，且只返回上面 8 个字段；如果以后要暴露更多信息再考虑加 token。
- **端口每次重启都变**：`verify-live.ps1` 已自动发现；别的脚本不要硬编码 52208。
- 未声明 `engines.dsh`/DSH peer 依赖，因此 0.2.1-rc.3 的插件兼容性检查不设限；只要求 Host 组合里有 `webServer` 服务（Desktop 一定有，否则它自己起不来）。

## 8. 机器可读的验收清单

- [x] `node --test plugin/dsh-whale-bridge/tests/status.test.mjs` → 19/19 通过
- [x] 每个 `.js` / `.mjs` `node --check` 通过
- [x] `turn/start → running`、`tool/call → tool`、`tool/result(error) → failure`、`turn/end → success`、`approval/asked → approval`
- [x] 状态变化立即写文件 / 5s 心跳 / 原子写 / 无 `.tmp` 残留
- [x] 状态文件、路由 JSON、reducer 输出都不含 `SECRET_PROMPT`、`C:\secret\path`
- [x] bundle 声明被 DSH 0.2.1-rc.3 自带 `app-boot` 解析并通过 mock profile 组合
- [x] `INSTALL.md` 给出备份、安装、验证、回滚、失败排查；明确 `dsh plugin add` **不会**改 `cordis.patch.yml`（它自动改 `dsh.profile.bundles`）
- [x] 真实安装到用户 desktop profile；运行中的实例热加载成功（2026-10-25 11:38，`verify-live.ps1` PASS，无需重启）
