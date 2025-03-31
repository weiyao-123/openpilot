# Comma3 视频流与远程监控系统

这个模块提供了两个主要功能：
1. Comma3设备数据实时广播（车辆状态、GPS位置等）
2. 实时屏幕画面流传输到局域网设备

## 功能特点

- 通过UDP广播传输车辆和设备状态数据
- 使用WebSocket实时流传输屏幕画面
- 提供网页界面查看所有信息
- 低延迟、无需外部依赖
- 使用Qt原生功能，不依赖OpenCV等重量级库

## 安装步骤

1. 安装依赖包：
```bash
cd /data/openpilot/selfdrive/app
pip install -r requirements.txt
```

2. 确保网络连接正常，并且comma3设备与接收设备在同一局域网中

## 使用方法

### 在Comma3上启动服务：

```bash
cd /data/openpilot
python selfdrive/app/commassit.py
```

启动后，服务将：
- 通过UDP广播设备数据（端口8088）
- 启动视频流服务器（端口8089）

### 在其他设备上查看数据和视频流：

```bash
cd /path/to/openpilot
python selfdrive/app/commawebview.py
```

然后在浏览器中访问：http://localhost:5000

## 技术说明

- commassit.py：在Comma3上运行，负责采集和广播数据与视频
- commawebview.py：在任何电脑上运行，接收并显示数据和视频流

视频流实现使用：
- Qt的QWidget.grab()捕获屏幕
- 通过WebSocket传输JPEG格式图像
- 前端使用base64解码并显示图像

## 常见问题

1. 如果视频流不工作，请检查：
   - 确保设备在同一局域网
   - 检查防火墙是否允许8089端口
   - 尝试直接访问 http://[COMMA3_IP]:8089/video

2. 如果看不到设备数据：
   - 确保UDP广播在您的网络中允许通过（端口8088）
   - 检查路由器是否允许广播包
