# 大肥鱼桌宠（dfy-desktop-pet）

一条住在 Windows 桌面上的鲸鱼娘桌宠：透明置顶窗口、序列帧动画、摸头/投喂/拖拽抛出、
打字或语音聊天（接 DeepSeek 等 OpenAI 兼容接口）、长期记忆、表情包、可选的 Agent 工具（读写文件前先问你）。

> 非官方社区二创项目，与 DeepSeek 官方无关。代码以 MIT 发布；**素材不随仓库分发**，见 [THIRD_PARTY.md](THIRD_PARTY.md)。

## 快速开始

```bash
pip install -r pet/requirements.txt
python tools/build_codex_character.py   # 下载并构建默认形象（YunYueSama/codex-deepseek-pet，署名许可）
python tools/sync_stickers.py           # 可选：下载表情包（CC BY-NC-SA 4.0，非商业）
python tools/extract_assets.py          # 可选：下载预合成语音与旧版动画（非商业许可）
copy config.example.json pet\config.json
python pet/app.py
```

API Key 三种放法（优先级从高到低）：右键 → 设置；环境变量 `DFY_API_KEY`；文件 `pet/.secret_api_key`（已 gitignore）。

## 个性化

- `pet/persona.md`：人设，直接改；
- `pet/config.json`：`display_name`（显示名）、音色、个人库路径、Agent 允许访问的目录等；
- `pet/PARTNER.md`：她对你的了解（首次运行自动生成模板）。

这些都是你自己的数据，已在 `.gitignore` 里（`persona.md` 除外，改了不想公开就自己忽略）。

## 测试

```bash
python -m pytest -q pet/tests
python tools/smoke_app.py   # 隐藏窗口启动冒烟
```

## 目录

| 路径 | 内容 |
| --- | --- |
| `pet/` | 桌宠本体（tkinter）：动画、气泡、语音、大脑、记忆、心情、提醒、桌面生活、吃 token、Agent、QQ/公众号桥 |
| `body3d/` | 可选的 3D 身体（Electron + three-vrm），通过本机接口听大脑指挥；可用 `tools/image_to_3d.py` 从立绘生成整体模型 |
| `tools/` | 素材下载/构建、冒烟与自检、图生 3D、DSH 聊天接入（`dsh_connect.py`）、公开库导出 |
| `plugin/dsh-whale-bridge/` | DeepSeek Harness 只读状态桥插件（让桌宠看到 DSH 在忙什么） |

## 在 DeepSeek Harness 里和她聊

桌宠的共用大脑（默认 `127.0.0.1:8765`）同时提供 OpenAI chat-completions 和 Anthropic Messages 接口。
`python tools/dsh_connect.py --apply` 把 DSH 的「DeepSeek API Key」路由指到这里（备份、可 `--undo`），
重启 DSH、在设置里给这条路由填任意本机 key，新对话选她这个模型即可；聊的内容进她同一份记忆。
