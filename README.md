# Oceaners Topside

Oceaners Topside 是 Oceaners 团队为参加 MATE ROV 竞赛开源的上位机控制台项目。它把水下机器人操作中常用的视频预览、拍照录像、手柄控制、TCP 通信和遥测数据显示整合到一个 Qt 桌面应用中，方便比赛现场调试、任务执行和后续复盘。

这个仓库面向开源协作，也面向后来继续参加 MATE ROV 的团队成员。我们希望它既能作为 Oceaners ROV 系统的一部分持续演进，也能给其他学生团队提供一个可阅读、可修改、可复用的 topside 参考实现。

## Features

- Qt 桌面端一体化控制台
- `1 个主画面 + 2 个副画面` 的视频监看布局
- 支持网络 RTP/H.264 视频流预览
- 支持本地摄像头调试模式
- 支持拍照、自动拍照、录像和保存目录选择
- 支持手柄输入读取和后台轮询
- 通过 TCP 向 ROV 下位机发送控制数据
- 显示连接状态、摇杆轴值、悬停状态、舵机角度和最近通信消息
- 自动解析下位机返回的 `key:value` 风格 telemetry 数据
- 在缺少 `pygame` 时自动降级，保留视频功能可用

## Project Structure

```text
.
├── main.py                 # Application entry point
├── app/
│   ├── main_window.py      # Main Qt window and UI interactions
│   ├── video_stream.py     # GStreamer preview, capture, and recording
│   ├── joystick_backend.py # Joystick polling, TCP communication, telemetry parsing
│   ├── joystick_bridge.py  # Joystick compatibility and bridge logic
│   ├── settings.py         # Persistent application settings
│   └── storage.py          # Storage path helpers
└── README.md
```

## Requirements

Recommended environment:

- Python 3.10+
- PySide6
- GStreamer runtime
- `gst-python` / `gi.repository`
- `pygame` for joystick support

On Windows, make sure the GStreamer runtime is installed and available to Python. The application includes some runtime path setup, but the actual GStreamer installation and plugins still need to exist on the machine.

## Quick Start

Create or activate your Python environment, then install the required packages according to your platform.

```bash
pip install PySide6 pygame
```

Run the application:

```bash
python main.py
```

If `pygame` is not installed, the application can still start, but joystick control will be unavailable.

## Configuration

The application persists common operator settings, including:

- Video source mode: network RTP or local camera
- Network video host and port
- Local camera index
- Joystick TCP host and port
- Auto-capture interval
- Media save directory

Current default values:

| Setting | Default |
| --- | --- |
| Video host | `0.0.0.0` |
| Video port | `5600` |
| Joystick host | `192.168.138.2` |
| Joystick port | `5000` |

## Video Console

The video area uses one large primary view and two smaller secondary views. In the current implementation, the app receives one video input and mirrors it across the three views. The secondary views can be clicked to swap with the primary view, so the UI is already prepared for future multi-camera expansion.

Supported source modes:

- `Network RTP`: receive RTP/H.264 video from the ROV or test transmitter
- `Local Camera`: use a local camera for development and debugging

## Joystick And Telemetry

The joystick module runs in the background instead of opening a separate `pygame` window. It reads controller axes, buttons, and hat state, then sends control packets to the ROV through TCP.

The status panel shows:

- TCP connection status
- Controller connection status and device name
- Hover mode state
- Left and right stick axis values
- Planar movement values
- Yaw rate and depth force
- Depth target
- Servo X/Y angles
- Last sent command
- Last received message
- Parsed telemetry values

Current button mappings:

| Button | Action |
| --- | --- |
| `button 7` | Toggle hover mode |
| `button 0` | Toggle yaw loop |
| `button 1` | Send servo start |
| `button 2` | Send servo stop |

## Development Notes

This project began as an integrated topside console combining camera and wired joystick control workflows into one desktop application. The code is intentionally kept small and direct so that team members can debug it under competition pressure.

Known limitations:

- Multi-view UI currently mirrors one real video source instead of receiving three independent streams.
- Servo angle values are UI-level readable estimates derived from control logic, not guaranteed physical feedback from the vehicle.
- Telemetry is shown as parsed latest values; a full real-time plotting dashboard has not been added yet.

Good next steps:

1. Add a reproducible environment file such as `requirements.txt` or `environment.yml`.
2. Validate joystick behavior with the competition controller.
3. Validate RTP preview, capture, and recording on the real topside computer.
4. Expand the video layer if the vehicle uses multiple independent cameras.
5. Add telemetry plotting and mission-specific operator widgets.

## About Oceaners

Oceaners is a student ROV team building underwater robotics systems for the MATE ROV competition. This repository contains our open-source topside software, shared in the spirit of learning, iteration, and collaboration across the student robotics community.

## License

No license has been added yet. Before reusing this project outside the team, please check the repository license status or contact the Oceaners team.
