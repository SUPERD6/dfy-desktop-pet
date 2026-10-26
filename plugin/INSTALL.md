# 安装 / 验证 / 回滚（DSH Desktop 0.2.1-rc.3，profile = `desktop`）

> 本文给 **Lead** 用：把 `plugin/dsh-whale-bridge` 装进用户当前的 `desktop` profile。
> 插件本身**没有**改过 `~/.dsh`、profile 或用户实例；以下命令由 Lead 执行。
>
> 关键结论（已在 0.2.1-rc.3 上核对/实测，详见 `research/dsh-plugin-api.md`）：
>
> - `dsh plugin --profile desktop add <本地目录>` 会改 **`package.json`**（`dependencies` + `dsh.profile.bundles`）和 `pnpm-lock.yaml`/`node_modules`，
>   **不会改 `cordis.patch.yml`**；插件挂载方式来自包内 `cordis.patch.yml`（`dsh.bundle.patch` 指向它）自动成为 bundle 层。
> - 本地目录被 pnpm 记为 `link:` 并创建 junction，**插件代码留在 `plugin/` 原地**；以后改插件只需重启 DSH，不需要重装。
> - `desktop` profile 由 Electron 独占：必须用 **Desktop 自带的 CLI**
>   `<DSH 安装目录>\resources\runtime\cli\bin\dsh.cmd`（它带 `manageDesktopProfile=true`）；
>   npm 全局的 `dsh` 会报 `profile "desktop" is managed exclusively by the Electron application`。
> - Desktop 的 GUI 端口**每次启动随机**（webServer 配置是 `port: 0`），不要写死 52208；`verify-live.ps1` 会自动发现新端口。
>
> ✅ **实际安装记录（2026-10-25 11:38，Lead 执行）**：在 DSH **运行中**直接执行了 `dsh.cmd plugin --profile desktop add ...`，安装成功；profile 的 `dependencies` 写入 `link:`、`dsh.profile.bundles` 变为 17 项、`cordis.patch.yml` 未改动；运行中的实例随后**热加载**了插件（状态文件 11:36:08 出现、`/api/whale/status` HTTP 200、`verify-live.ps1` PASS），因此这次**没有重启 DSH**。安装前备份：`plugin/backup/20261025-1138/`。以后升级/卸载仍建议按本文「先退出 DSH」流程操作。

---

## 0. 前置检查

```powershell
$profileDir = Join-Path $env:USERPROFILE '.dsh\profiles\desktop'
$dshCli     = '<DSH 安装目录>\resources\runtime\cli\bin\dsh.cmd'
$pluginDir  = '<仓库目录>\plugin\dsh-whale-bridge'
$statusFile = Join-Path $env:USERPROFILE '.dsh\whale-bridge\status.json'

Test-Path $dshCli           # 期望 True
Test-Path (Join-Path $profileDir 'package.json')   # 期望 True（Desktop 至少启动过一次）
Test-Path (Join-Path $pluginDir 'package.json')    # 期望 True
Get-Process 'DeepSeek Harness' -ErrorAction SilentlyContinue   # 安装前应为空
```

## 1. 备份（必须）

```powershell
$backup = Join-Path $env:USERPROFILE ('.dsh\whale-bridge\backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item (Join-Path $profileDir 'package.json')    $backup
Copy-Item (Join-Path $profileDir 'pnpm-lock.yaml')  $backup
Copy-Item (Join-Path $profileDir 'cordis.patch.yml') $backup
Write-Host "backup -> $backup"
```

## 2. 安装（推荐路径：官方 CLI）

**先完全退出 DSH Desktop**（托盘图标 → 退出；确认没有 `DeepSeek Harness.exe` 进程），然后：

```powershell
& $dshCli plugin --profile desktop add $pluginDir
```

`dsh plugin` 对 desktop profile 的实际行为（0.2.1-rc.3 源码核对）：

1. 取 profile 写锁，用 Desktop 内置 pnpm 在 profile 目录执行 `pnpm add <本地目录>`；
2. `runProfilePnpm` 的 activate 阶段（`activateNewBundles` 默认开启）对新增依赖执行 `reconcile`：
   - 校验包声明了 `dsh.bundle`，用 DSH 自己的 `loadOverlayPatches` 预解析包内 patch（解析失败会阻止安装）；
   - 把包名**自动追加**到 `package.json` 的 `dsh.profile.bundles`；
3. **不修改** `cordis.patch.yml`（该文件保持原样，插件行来自包内 `cordis.patch.yml`）；
4. 若 pnpm 或校验失败，会回滚 `package.json` / `pnpm-lock.yaml`。

安装后检查（期望两条都 `True`）：

```powershell
$m = Get-Content (Join-Path $profileDir 'package.json') -Raw | ConvertFrom-Json
[bool]$m.dependencies.'dsh-whale-bridge'                 # True
[bool]($m.dsh.profile.bundles -contains 'dsh-whale-bridge')  # True
$m.dependencies.'dsh-whale-bridge'   # 期望 link:<仓库目录>/plugin/dsh-whale-bridge

# 确认 cordis.patch.yml 未被安装步骤改动（无输出 = 完全一致）
Compare-Object (Get-Content (Join-Path $backup 'cordis.patch.yml')) `
               (Get-Content (Join-Path $profileDir 'cordis.patch.yml'))
```

预期 `package.json` 变化（只这两处）：

```jsonc
{
  "dependencies": {
    "@deepseek-ai/dsh-subagent-claude-code": "0.2.1-rc.3",
    "@deepseek-ai/dsh-subagent-codex": "0.2.1-rc.3",
    "dsh-whale-bridge": "link:<仓库目录>/plugin/dsh-whale-bridge"
  },
  "dsh": {
    "profile": {
      "bundles": [
        // ... 原有 16 项保持不变 ...
        "dsh-whale-bridge"
      ]
    }
  }
}
```

> 如果 `dsh plugin add` 失败（网络、Desktop 未完全退出、锁冲突），先按第 6 节回滚，再走第 3 节的「手工安装」。

## 3. 手工安装（`dsh plugin add` 不可用时的最小改动）

任选其一，都比改 `node_modules` 干净：

### 3A. 只改 `cordis.patch.yml`（最少侵入：不碰依赖和 lockfile）

DSH app-boot 支持 insert 行的 `name` 直接写**绝对路径**（会转成 `file:` URL 再 import），所以不需要 pnpm：

```powershell
Copy-Item (Join-Path $profileDir 'cordis.patch.yml') (Join-Path $backup 'cordis.patch.yml.before-manual')
# 在 cordis.patch.yml 顶层数组末尾追加下面这段（YAML 数组元素，注意缩进与已有 `- id:` 同级）
```

```yaml
- insert:
    - id: dsh-whale-bridge
      name: <仓库目录>/plugin/dsh-whale-bridge/lib/index.js
```

注意：

- 路径用**正斜杠**；`name` 指向 `lib/index.js` 本体。
- 可选 config 覆盖（默认值见下，不写就用默认）：
  ```yaml
  - insert:
      - id: dsh-whale-bridge
        name: <仓库目录>/plugin/dsh-whale-bridge/lib/index.js
        config:
          statusFile: '%USERPROFILE%\.dsh\whale-bridge\status.json'
          heartbeatMs: 5000
  ```
- `cordis.patch.yml` 必须是合法 YAML 数组；不要写成空文件（空/纯注释会被判为 boot 失败）。改完重启 DSH。

### 3B. 手工走依赖 + bundles（等价于 `dsh plugin add` 的落盘结果）

```powershell
# 1) 在 profile/package.json 的 dependencies 里加一行（Windows 路径用正斜杠）：
#    "dsh-whale-bridge": "link:<仓库目录>/plugin/dsh-whale-bridge"
# 2) 在 dsh.profile.bundles 数组末尾加一项：
#    "dsh-whale-bridge"
# 3) 让 pnpm 建立 link，然后重启 DSH：
& $dshCli plugin --profile desktop install
```

改 JSON 前先确认备份已完成；任何一步失败都可以用第 5 节恢复。

## 4. 重启 + 验证

1. 启动 DSH Desktop（等 GUI 正常出现）。
2. 运行验证脚本（Windows 自带 PowerShell 5.1 已验证；有 pwsh 7 也可用）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<仓库目录>\plugin\verify-live.ps1"
```

期望输出：状态文件 JSON + 自动发现的端口 + 路由 JSON + `PASS`（exit 0）。脚本做三件事：

- 读 `%USERPROFILE%\.dsh\whale-bridge\status.json`；
- 从**当前运行中的 Desktop host 进程**枚举监听端口，逐个探测 `http://127.0.0.1:<port>/api/whale/status`（所以重启换端口也能找到）；
- 校验文件与路由的 8 个字段一致、`updatedAt` 偏差合理，并提示状态新鲜度。

3. 触发一次真实状态：在 DSH 里发一个小任务，另开终端观察：

```powershell
Get-Content "$env:USERPROFILE\.dsh\whale-bridge\status.json" -Wait
```

期望看到 `idle → running → tool → … → success`（或失败时 `failure`），并且无事件时每 5 秒文件被重写一次（心跳，内容不变）。

4. 路由单独验证（端口换成脚本打印的那个）：

```powershell
Invoke-RestMethod 'http://127.0.0.1:<port>/api/whale/status'
```

## 5. 回滚

### 5A. 正常卸载（推荐）

```powershell
# 完全退出 DSH Desktop
& $dshCli plugin --profile desktop remove dsh-whale-bridge
# remove 同样走 reconcile：依赖删除后会把 dsh-whale-bridge 从 dsh.profile.bundles 移除；
# cordis.patch.yml 未被安装步骤修改，无需还原（若你手工加过 config，见 5B 还原备份）。
# 启动 DSH Desktop 即回到安装前。
# 可选：删掉状态目录（桌宠应把文件缺失视为 idle）
Remove-Item (Join-Path $env:USERPROFILE '.dsh\whale-bridge') -Recurse -Force
```

### 5B. 从备份硬恢复（安装命令失败 / 手工改错 / 想立刻回到原状）

```powershell
# 完全退出 DSH Desktop
Copy-Item (Join-Path $backup 'package.json')     (Join-Path $profileDir 'package.json') -Force
Copy-Item (Join-Path $backup 'pnpm-lock.yaml')   (Join-Path $profileDir 'pnpm-lock.yaml') -Force
Copy-Item (Join-Path $backup 'cordis.patch.yml') (Join-Path $profileDir 'cordis.patch.yml') -Force
# 让 node_modules 与恢复后的清单一致（若离线环境先加 --offline）
& $dshCli plugin --profile desktop install
# 重启 DSH Desktop
```

只做过 3A（手工 patch）的，恢复 `cordis.patch.yml` 一个文件即可。

## 6. 失败排查

| 现象 | 原因 / 处理 |
|---|---|
| `dsh plugin ...` 报 `profile "desktop" is managed exclusively by the Electron application` | 用了 npm 全局 `dsh`。改用本文的 `$dshCli`（Desktop 自带 CLI）。 |
| `dsh plugin add` 卡住或报文件占用 | DSH Desktop 没完全退出。托盘退出，确认 `Get-Process 'DeepSeek Harness'` 为空后重试。 |
| 安装报 `declares no dsh.bundle` | 安装路径指错了（不是 `plugin/dsh-whale-bridge`，或 `package.json` 被改）。确认包内有 `dsh.bundle.patch`。 |
| DSH 启动日志出现 `plugin tree failed to load` / inactive entry 警告 | 插件 import 失败。先在本机跑 `node --check`（Node ≥ 22）检查三个 js；再确认插件目录可读、路径正确；回滚后排查。 |
| 状态文件一直不出现 | 插件没挂载（端口/日志确认）、或 `statusFile` 指向不可写位置。默认路径父目录会自动创建。 |
| `verify-live.ps1` 路由不可达但文件在 | 插件没加载，或探测到的端口不是 GUI 端口。脚本会打印候选端口；也可 `-Port <端口>` 手动指定。 |
| 状态文件停在 `running` 很久 | DSH 被强杀时不会执行 dispose。桌宠侧用 `updatedAt` 超过 ~15s 判为 DSH 不在。 |
| 改插件代码后没生效 | `link:` 安装指向原地目录，**重启 DSH** 即生效（HMR 可能自动重载，但重启最确定）。 |

## 7. 兼容性 / 影响面

- 插件不声明 `engines.dsh` 也不声明 DSH peerDependencies，因此 0.2.1-rc.3 的插件版本检查不设限；运行时只依赖 Node 内置模块和 Host 注入的 `webServer` 服务。
- 新增的只有：profile 的 `package.json` 两处、`pnpm-lock.yaml`、`node_modules` 里一个 link/junction、运行时状态文件 `~/.dsh/whale-bridge/status.json`，以及 loopback 上的一个 exact 路由 `/api/whale/status`。
- 该路由优先于 DSH `connection` 的 `/api` 前缀路由，因此不经过其 token 校验；只监听 127.0.0.1，只返回 8 个粗粒度字段（无提示词/参数/路径/输出）。若认为不可接受，卸掉插件即可，不影响 DSH 本身。
- 未改动：`~/.dsh` 其他文件、`cordis.yml`、桌宠 `pet/`、模型配置。
