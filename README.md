# ROV Integrated Console

这个目录是一个全新的整合版程序，用来把原来的 `camera_app` 和 `rov-v4-wired-joystick` 能力放进同一个 Qt 桌面应用里。

本目录是独立实现，不会修改原有项目代码。

## 当前功能

- 一个统一的 Qt 桌面程序
- 视频界面采用 `1 个主大屏 + 2 个副小屏`
- 当前只有 1 路视频流时，3 个视图显示同一画面
- 两个副小屏支持点击后切换为主屏显示
- 支持原 `camera_app` 的预览、拍照、自动拍照、录像、存储目录选择
- 内嵌 `joystick` 状态面板
- `joystick` 状态面板显示：
  - TCP 连接状态
  - 手柄连接状态和手柄名称
  - 悬停状态
  - 左右摇杆轴值
  - Planar x/y
  - Yaw rate / Depth force
  - Depth target
  - Servo x/y 角度
  - 最近发送命令
  - 最近接收消息
  - 自动解析的下位机数值 telemetry

## 目录结构

- [main.py](/D:/rov/v4/integrated_camera_joystick_app/main.py): 程序入口
- [app/main_window.py](/D:/rov/v4/integrated_camera_joystick_app/app/main_window.py): 主界面和交互逻辑
- [app/video_stream.py](/D:/rov/v4/integrated_camera_joystick_app/app/video_stream.py): GStreamer 预览和录制逻辑
- [app/joystick_backend.py](/D:/rov/v4/integrated_camera_joystick_app/app/joystick_backend.py): joystick 后台轮询、TCP 通信、数据解析
- [app/settings.py](/D:/rov/v4/integrated_camera_joystick_app/app/settings.py): 配置持久化
- [app/storage.py](/D:/rov/v4/integrated_camera_joystick_app/app/storage.py): 存储路径管理

## 与原项目的关系

这个整合版主要参考并复用了以下两个目录的设计和逻辑：

- [camera_app](/D:/rov/v4/camera_app)
- [rov-v4-wired-joystick](/D:/rov/v4/rov-v4-wired-joystick)

注意：

- 原来的两个项目没有被修改
- 如果你要回退或对比行为，可以直接分别运行原项目
- 整合版后续功能应继续优先改这个新目录，不要回头混改旧目录

## 运行环境

当前代码默认按你现有的 `rov_yolo` 环境来准备。

建议运行环境至少具备：

- Python 3.10+
- PySide6
- `gi.repository` / `gst-python`
- Windows 下可用的 GStreamer 运行时
- 如果要启用 joystick，还需要 `pygame`

## 推荐启动方式

```powershell
conda activate rov_yolo
cd D:\rov\v4\integrated_camera_joystick_app
python main.py
```

如果系统命令行里没有 `python`，也可以直接使用你当前环境里的解释器：

```powershell
D:\ProgramData\miniconda3\envs\rov_yolo\python.exe D:\rov\v4\integrated_camera_joystick_app\main.py
```

## 配置项说明

程序会持久化以下配置：

- 视频源模式：网络流 / 本地相机
- 视频流地址和端口
- 本地相机索引
- joystick TCP 主机和端口
- 自动拍照时间间隔
- 保存目录

默认值目前是：

- 视频流地址：`0.0.0.0`
- 视频流端口：`5600`
- joystick 主机：`192.168.138.2`
- joystick 端口：`5000`

## 视频功能说明

### 1. 布局

界面左侧是视频区：

- 上方 1 个主大屏
- 下方 2 个副小屏

### 2. 当前单路流行为

目前整合版只接了 1 路视频流输入。

因此现在的行为是：

- 主屏显示这一路流
- 两个副屏也显示同一画面

这是按后续扩展多路视频预留出来的 UI 结构，不是 bug。

### 3. 副屏切主屏

两个副小屏都支持鼠标点击。

点击后会发生：

- 被点击的副屏槽位与主屏槽位交换
- 当前只有 1 路流时，视觉上可能没有区别
- 后续如果扩展到多路流，这个交互会直接生效

### 4. 视频源模式

支持两种模式：

- `Network RTP`
- `Local Camera`

其中：

- `Network RTP` 用于接收网络 RTP/H264 视频
- `Local Camera` 用于 Windows 本地摄像头测试

## Joystick 功能说明

### 1. 当前整合方式

原来的 joystick 是 `pygame` 独立窗口程序。

在整合版里：

- 不再弹出原来的 `pygame` 控制窗口
- 改成后台线程轮询手柄
- 界面中只显示监控面板和状态

### 2. 保留的核心逻辑

整合版保留了原 joystick 项目里的这些关键行为：

- 手柄轴值读取
- Hat 状态读取
- 按键触发控制命令
- TCP 连接到下位机
- 二进制控制包发送
- 文本消息接收
- 文本 telemetry 自动解析

### 3. 当前按键行为

当前后台逻辑延续原代码的主要按钮映射：

- `button 7`: 切换悬停模式
- `button 0`: 切换 yaw loop
- `button 1`: 发送 servo start
- `button 2`: 发送 servo stop

### 4. 注意事项

- 只有安装了 `pygame` 才能真的启用 joystick
- 如果 `pygame` 未安装，程序仍可启动，但 joystick 区域会显示不可用
- 这属于降级设计，不会影响相机功能

## 已知运行注意事项

### 1. `pygame` 当前可能未安装

我在当前环境检查时发现：

- `rov_yolo` 环境里当前没有 `pygame`

这会导致：

- 整合版程序可以启动
- 但 joystick 控制不会工作

如果要启用 joystick，请在对应环境里安装 `pygame`。

例如：

```powershell
conda activate rov_yolo
pip install pygame
```

如果你更倾向于 `conda` 方式，也可以按你现有环境规范处理。

### 2. GStreamer 运行时依赖要完整

视频部分依赖 GStreamer 和 Python GI 绑定。

当前代码已经在 [main.py](/D:/rov/v4/integrated_camera_joystick_app/main.py) 里做了 Windows 运行时路径补充，但仍要注意：

- GStreamer 安装路径必须真实存在
- `gi.repository` 相关 typelib 需要可用
- 某些机器上可能还需要补 `GST_PLUGIN_SCANNER`
- 如果某些插件找不到，预览或录像会失败

### 3. 当前只是真正单路视频输入

虽然界面做成了 `1 主 + 2 副`，但现在并没有实现 3 路独立视频采集。

也就是说：

- 现在是单路输入，多视图复用显示
- 不是已经支持 3 路摄像头同时接入

如果以后要扩展：

- 需要为每个槽位设计独立视频源
- 需要扩展 `video_stream.py` 为多实例或多管线模式

### 4. joystick 角度显示只是界面可视化

当前界面里的 servo 角度显示来自原工程里的 PWM 到角度换算逻辑，主要用于显示和调试。

这意味着：

- 它是 UI 层面的可读值
- 不等同于设备端一定已经执行到对应物理角度

### 5. 当前没有把旧版 `pc_client_plot.py` 的曲线面板完整嵌入

目前整合版已经支持：

- 解析接收文本中的 `key:value`
- 在界面中显示最近的 telemetry 数值

但还没有做：

- 完整实时曲线图小窗

如果后续需要，可以继续在这个整合版上追加。

## 已做验证

我已经做过以下验证：

- 新目录代码可以编译通过
- 新入口和主窗口可以按真实启动路径完成导入
- 在缺少 `pygame` 的情况下，程序会自动降级，不会直接崩溃

注意：

- 这不等于我已经在你机器上完整跑通了 GUI 交互
- 也不等于当前环境里的 GStreamer 插件全部齐全
- 真实预览、录像、手柄输入，还要看你本机环境依赖是否完整

## 后续建议

如果你接下来继续完善这个整合版，建议优先做这几项：

1. 给整合版补一个启动脚本，例如 `run_integrated_app.ps1`
2. 在环境里安装 `pygame`，然后验证 joystick 真机输入
3. 实机验证 RTP 预览、拍照、录像
4. 如果确实有多路视频需求，再把单路复用升级成多路真实输入
5. 如果你希望界面更像原 joystick，可继续把实时曲线图嵌进去

## 使用边界

为了避免后续维护混乱，请遵守下面这条原则：

- 整合需求继续改 [integrated_camera_joystick_app](/D:/rov/v4/integrated_camera_joystick_app)
- 原来的 [camera_app](/D:/rov/v4/camera_app) 和 [rov-v4-wired-joystick](/D:/rov/v4/rov-v4-wired-joystick) 保持为参考版本
