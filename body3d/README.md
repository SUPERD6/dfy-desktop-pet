# body3d · 大肥鱼的 3D 身体（实验）

大脑不变，换身体：`pet/` 里的 Python 仍然负责记忆、心情、生活、说话；这里是一个 Electron 透明窗口，用 Three.js + [three-vrm](https://github.com/pixiv/three-vrm) 把 VRM 3D 模型画在桌面上。

## 运行

- 从桌宠右键菜单勾「🧊 3D 身体（实验）」：会启动本目录、收起 2D 窗口；3D 窗口右键「回到 2D」切回去。
- 单独运行：`start.bat`（优先用仓库里的便携 Node `.tools/node`，没有就用系统 node）。
- 首次需要依赖：`npm ci`（Electron 33.2.0、three 0.170.0、@pixiv/three-vrm 3.3.1，版本已锁定）。

## 模型

放在 `models/`（不进 Git），按顺序找第一个能用的：

1. `model.vrm`：带骨骼、表情、口型的正式形象（VRoid 捏的、或 B 站工房的 3D 大肥鱼——先确认作者授权，别放进公开仓库）；
2. `model.glb` / `mingyue_q.glb`：从 2D 立绘「图生 3D」得到的整体模型，没有骨骼，动作靠整体转向、呼吸、蹦跳、歪头、压扁回弹；
3. `sample.vrm`：three-vrm 仓库的示例模型，只用来验证链路。

整体模型旁边可放同名 `.json`：`rotation` 摆正角度（度）；`shading` 默认 `flat`（不打光，颜色取自原画）；`brightness` 默认 1.4（TripoSR 顶点色偏暗，按原画对照实测选定）。例如 `{"rotation": [-90, 0, 90], "brightness": 1.4}`。调色对照可用环境变量 `DFY_BG=#808080`、`DFY_SHADING=flat:1.4`。

### 从 2D 立绘生成 3D（本机显卡）

```bash
.tools/tripo-venv/Scripts/python.exe tools/image_to_3d.py pet/assets/characters/mingyue-q/anim/idle.webp body3d/models/mingyue_q.glb
```

用的是 TripoSR（MIT），首次会下载约 2GB 权重到 `.tools/hf-cache/`。单张正面图生成：背面是模型猜的、细节偏软；`--resolution 384` 稍清楚但面数翻倍，默认 256。

## 她会做什么

- 呼吸起伏、随机眨眼、轻微摇摆；头和眼睛跟着鼠标转；头发等物理随动。
- 跟着大脑的情绪换表情（开心/生气/难过/惊讶/放松），说话时嘴巴开合，头顶显示气泡。
- 空白处点击穿透；按住身体拖动；单击 = 摸头；双击 = 打开网页聊天；右键 = 菜单（摸头、投喂、聊天、回到 2D）。
- 空闲 30 帧，省显卡。

## 和大脑的接口（共用大脑服务，默认 `http://127.0.0.1:8765`，可用环境变量 `DFY_BRAIN` 改）

| 接口 | 用途 |
| --- | --- |
| `GET /v1/pet/state` | 每 200ms 取一次：`{ok, emotion, speaking, bubble, activity, mood, feeling, name}` |
| `POST /v1/pet/event` `{"kind": "pat" \| "open_chat" \| "back_2d" \| "feed:rice" …}` | 身体上的点击交给大脑 |

请求由 Electron 主进程发（不受网页跨域限制），所以大脑那边不开放跨域，普通网页读不到她的状态。

## 检查

- `start.bat --smoke`：隐藏窗口加载模型、渲染 60 帧，输出 `BODY3D SMOKE OK`（附镜头与骨骼取景数字）。
- `DFY_BODY_POS="x,y"`：指定窗口初始位置（截图检查用）。
- 在受限环境（如带沙箱的自动化终端）里 Chromium 沙箱起不来时，测试可加 `--no-sandbox`；正常从桌面启动不要加。

## 已知限制

- 还没接上 2D 版的「站在窗口顶上」物理：3D 窗口目前自己待着，可以拖动。
- 动作只有程序化待机；VRMA 动作文件（`models/test.vrma` 是示例）还没接。
