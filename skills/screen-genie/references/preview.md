# 查看虚拟屏画面

## 现有能力

`mdi_capture` 返回单帧 PNG 的 base64，可以看「现在是什么界面」，但拿不到连续画面。skill 里的预览就是这一种：用户说「给我看看」，截一张。

循环调用 `mdi_capture` 拼实时画面不现实：每次都要走一轮 MCP 往返，实际只能到 1～2 帧每秒；更关键的是服务端限流 **120 次/分钟（burst 20）**，5～10 fps 会在十几秒内吃光额度，任务本身就没调用预算了。所以循环截图只能应急看一两帧，不能当预览方案。

## 桌面端镜像（可选）

宿主在桌面、手机通过 ADB 连接时，可以用 scrcpy 直接镜像虚拟屏。先在桌面执行：

```bash
adb shell dumpsys display | grep mPrimaryDisplayDevice
scrcpy --list-displays
```

记下目标虚拟屏的 logical id，然后：

```bash
scrcpy --display-id=<id> --no-audio
```

实测注意：

- 用 `scrcpy` 自己创建的虚拟屏由 `com.android.shell` 持有，镜像无障碍。
- MDI 创建的虚拟屏属于应用 uid，是否能被 shell 镜像取决于 display 归属。任务运行中试一次：报权限错误或画面全黑，就说明不可镜像，退回单帧截图。
- `screenrecord --display-id` 在本机实测报 `Invalid physical display ID`，不要依赖。

## 抓单帧

```bash
adb shell screencap -p -d <physicalId> /sdcard/vd.png
adb pull /sdcard/vd.png
```

`-d` 要用物理 display id，不是 `--list-displays` 里的 logical id。两者不同，抓错会得到主屏或报错。

## 长期方案

要做到边操作边看，需要 MDI 侧提供帧源，例如：

```text
mdi_preview_start { mode: "stream" | "float", scale?: number, position?: "tl"|"tr"|"bl"|"br" }
mdi_preview_stop  {}
```

或者更轻量地返回一个可拉流地址 `mdi_stream_url`。宿主拿到地址后自己渲染悬浮窗或画中画。没有这一步，插件侧只能给单帧。
