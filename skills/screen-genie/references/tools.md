# MDI 工具参考

以下工具均在 MDI SDK 的虚拟显示屏（Task/OmniDisplay）上执行，属于兜底手段：能由手机原子能力完成的操作，先用原子能力，不要用截图加模拟点击。

参数名以调用方实际接受的为准，本文给出常用形式和取值范围。

## 任务与屏幕

| 工具 | 用途 | 常用参数 |
| --- | --- | --- |
| `mdi_task_create` | 创建自动化任务，自动创建并绑定虚拟显示屏，作为后续操作的执行环境 | `reuse`：同名 Task 已存在时复用或销毁重建 |
| `mdi_task_finish` | 结束任务，释放虚拟显示屏等资源 | `ignoreActions`：true 立即中断并清理，false 等当前操作完成再清理 |
| `mdi_open_app` | 在 Task 关联的虚拟屏上启动指定包名的应用 | `packageName` |
| `mdi_move_app` | 把应用从当前屏幕移到目标 Display，可选择置于栈顶 | `packageName`、`displayId`、是否置顶 |
| `mdi_top_app` | 获取指定 Display 上当前栈顶（前台）应用的包名；不指定时取系统默认屏幕 | `displayId`（可选） |
| `mdi_ime_enable` | 控制虚拟屏输入法（IME）所有权；开启后键盘显示在虚拟屏上，关闭后回到主屏。所有权独占 | 开关值 |

## 触控操作

| 工具 | 用途 | 常用参数 |
| --- | --- | --- |
| `mdi_click` | 点击。可按坐标点击，也可按 `contentDesc` 或 `text` 在 UI 树中定位后点击 | `x`、`y` 或 `contentDesc` / `text`；`durationMs` |
| `mdi_tap` | 与 `mdi_click` 完全一致，属于重复能力，优先使用 `mdi_click` | 同上 |
| `mdi_long_click` | 在指定坐标长按，默认 1000 毫秒 | `x`、`y`、`durationMs` |
| `mdi_swipe` | 从起点滑到终点，可控制持续时间和插值步数 | `startX`、`startY`、`endX`、`endY`、`durationMs`、步数 |
| `mdi_fling` | 带惯性的快速滑动（抛掷），适合翻页和列表滚动 | 起点、`velocityX`、`velocityY` |
| `mdi_scale` | 双指缩放，以中心点从起始距离缩放到结束距离；起始小于结束为放大，反之为缩小 | `centerX`、`centerY`、`startDistance`、`endDistance` |

## 文字与按键

| 工具 | 用途 | 常用参数 |
| --- | --- | --- |
| `mdi_set_text` | 在指定坐标的输入框（EditText）中设置文本 | `x`、`y`、`text` |
| `mdi_key_event` | 注入指定 KeyCode 的按键事件，支持 Android `KeyEvent.KEYCODE_*` | `keyCode` |
| `mdi_back` | 发送返回键 `KEYCODE_BACK`，用于返回上一页或关闭弹窗 | 无 |
| `mdi_home` | 发送主屏键 `KEYCODE_HOME`，回到桌面 | 无 |

## 等待与观察

| 工具 | 用途 | 常用参数 |
| --- | --- | --- |
| `mdi_wait` | 暂停指定毫秒数，默认 1000，最大 30000 | `milliseconds` |
| `mdi_capture` | 对指定包名的应用或栈顶应用截图，返回 PNG 格式的 base64 数据，用于预览和视觉确认；单帧快照，没有连续视频流 | `packageName`（可选） |
| `mdi_snapshot` | 获取指定包名或栈顶应用的 UI 视图树快照，返回扁平化文本树，含控件类型、坐标、文本 | `packageName`（可选） |

## 读 UI 树与取坐标

`mdi_snapshot` 返回的是扁平化文本树，每个节点带控件类型、文本和边界。按下面的方式把它变成可点的坐标：

- 边界通常是 `bounds`，形如 `[x1,y1][x2,y2]`，单位是虚拟屏像素。点击坐标取中心点：`x=(x1+x2)/2`，`y=(y1+y2)/2`。
- 优先按 `text` 或 `contentDesc` 定位；两者都没有时，才用中心点坐标。
- 节点标了可点击但 `bounds` 为空或宽高为 0 时，不要点它，改点它的父节点或改用截图目测。
- 文本可能被截断或包含省略号，`text` 要传完整值；不确定时用 `contentDesc`。
- 滚动、切换页面、弹窗出现后，旧坐标全部失效，必须重新 snapshot，不要复用。
- 截图返回的 PNG 可能被缩放，不要直接拿截图里的目测像素当虚拟屏坐标；需要精确值时以 `bounds` 为准。

## 用户说法对照

| 用户可能说 | 对应工具 |
| --- | --- |
| 创建任务、新建 Task、初始化任务环境、创建虚拟屏任务 | `mdi_task_create` |
| 结束任务、关闭 Task、停止自动化、释放虚拟屏 | `mdi_task_finish` |
| 打开应用、启动 App、在虚拟屏上打开某个应用 | `mdi_open_app` |
| 把应用切到主屏、移动到指定 display、跨屏移动 | `mdi_move_app` |
| 当前前台是哪个应用、查看栈顶 App | `mdi_top_app` |
| 点击、点一下、按文字找到元素并点击 | `mdi_click` |
| 长按、按住不放、长按图标 | `mdi_long_click` |
| 向上滑、向下滑、左滑、右滑 | `mdi_swipe` |
| 快速滑动、快速翻页、惯性滚动 | `mdi_fling` |
| 放大图片、双指缩放、捏合缩小 | `mdi_scale` |
| 输入文字、往搜索框打字、填写表单 | `mdi_set_text` |
| 模拟按键、发送回车键、按下音量键 | `mdi_key_event` |
| 返回、回到上一页、关掉弹窗 | `mdi_back` |
| 回到桌面、按 Home 键、回主页 | `mdi_home` |
| 等一下、等待页面加载、延时 | `mdi_wait` |
| 截图、截屏、获取当前画面 | `mdi_capture` |
| 获取 UI 树、抓取页面结构、查看控件信息 | `mdi_snapshot` |
| 启用输入法、开启虚拟屏键盘、切换输入法 | `mdi_ime_enable` |
