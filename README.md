# 屏幕精灵（screen-genie）

原子能力覆盖不到时，用 MDI 虚拟显示屏操作 Android 应用。

## 包含内容

- `skills/screen-genie/SKILL.md`：给模型读的 skill，说明何时该用虚拟屏、标准操作步骤、输入输出和边界。
- `skills/screen-genie/references/tools.md`：MDI 全部工具的用途、常用参数和用户说法对照。
- `skills/screen-genie/references/mcp-setup.md`：把手机内置 MCP 服务接入 Agent 宿主的完整步骤，含端口归属与 ADB 转发。
- `skills/screen-genie/references/preview.md`：查看虚拟屏画面的方式，单帧截图、scrcpy 镜像与限制。
- `skills/screen-genie/scripts/mcp_proxy_stdlib.py`：零依赖的 MCP 规范化代理，仅用 Python 标准库，适配 Termux。

## 安装与文件结构

保持下面的目录结构，不要只取 `SKILL.md` 单独改名：

```text
screen-genie-plugin/
├── plugin.json
├── README.md
└── skills/
    └── screen-genie/
        ├── SKILL.md
        ├── references/
        └── scripts/
```

`SKILL.md` 必须在 `skills/screen-genie/` 目录下，目录名与 frontmatter 的 `name` 一致。只把内容保存成无扩展名的 `new` 文件，宿主不会加载，`references/` 也会一起丢失。

## 能力范围

- 任务与屏幕：创建/结束自动化任务、启动应用、跨屏移动、查询前台应用、输入法归属切换。
- 触控：点击、长按、滑动、惯性滑动、双指缩放。
- 输入：设置输入框文本、注入按键、返回、回到桌面。
- 观察：等待、截图、获取 UI 视图树。

系统已有原子能力的操作不走这里；虚拟屏只用于能力缺失、能力失败但界面可操作、或跨应用界面流程。

## 使用

1. 优先直连手机内置服务：在宿主登记 `http://127.0.0.1:8765/mcp`，请求头带 `Authorization: Bearer <apiKey>`。
2. 宿主在桌面、手机通过 ADB 连接时，先在桌面执行 `adb forward tcp:8765 tcp:8765`。
3. 只有宿主不能自定义请求头时，才启动代理：`python3 skills/screen-genie/scripts/mcp_proxy_stdlib.py`，改用 `http://127.0.0.1:8766/mcp`，必要时配合 `MCP_API_KEY` 代填。
4. 重启会话，模型即可发现并调用 `mdi_*` 工具，按 skill 的步骤操作虚拟屏。

工具调用由 MDI SDK 提供，本插件不包含运行时依赖，也不保存任何凭据。端口归属、服务提供方、限流和排错见 `skills/screen-genie/references/mcp-setup.md`。

## 产物校验

完整包共 7 个文件：`plugin.json`、`README.md`、`skills/screen-genie/SKILL.md`、`references/` 下的 `tools.md`、`mcp-setup.md`、`preview.md`，以及 `scripts/mcp_proxy_stdlib.py`。

只拿到一个无扩展名文件，或没有 `references/` 目录，说明拿到的是残件，宿主不会加载，需要重新取完整包。
