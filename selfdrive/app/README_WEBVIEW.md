# Comma3 WebView 客户端使用说明

这个工具允许您在局域网中的电脑上查看 Comma3 设备的实时屏幕画面和车辆数据。

## 功能特点

- 接收 Comma3 发送的车辆数据广播
- 以网页形式展示车辆状态、设备信息、位置信息等
- 通过 WebRTC 技术显示 Comma3 的实时屏幕画面
- 不需要在 Comma3 上安装额外软件，只要 commassit.py 已启用 WebRTC 功能

## 系统要求

- Python 3.6+
- 网络浏览器 (推荐使用 Chrome 或 Firefox)
- 与 Comma3 设备处于同一局域网

## 安装指南

1. 确保您的电脑上已安装 Python 3.6 或更高版本
2. 安装必要的依赖：

```bash
pip install flask
```

## 使用方法

### 在 Comma3 上

1. 确保 commassit.py 服务正在运行，并且 WebRTC 功能已启用：

```bash
cd /data/openpilot
python selfdrive/app/commassit.py
```

2. 使用 `ifconfig` 命令查看 Comma3 的 IP 地址（通常是 wlan0 接口的地址）

### 在局域网电脑上

1. 将 commawebview.py 复制到您的电脑上
2. 运行 commawebview.py：

```bash
python commawebview.py -w 5000
```

3. 在浏览器中访问 http://localhost:5000

## 参数说明

commawebview.py 支持以下命令行参数：

- `-p, --port`: 监听 Comma3 UDP 广播的端口号，默认为 8088
- `-w, --web-port`: Web 服务器端口，默认为 5000
- `--host`: Web 服务器监听地址，默认为 0.0.0.0（所有网络接口）

示例：

```bash
# 使用不同的端口启动
python commawebview.py -p 8089 -w 8000

# 仅在本地接口启动
python commawebview.py --host 127.0.0.1
```

## 使用说明

1. 启动应用后，网页会自动搜索并连接到局域网中的 Comma3 设备
2. 连接成功后，页面顶部会显示设备的 IP 地址和最后更新时间
3. 如果 Comma3 启用了 WebRTC 功能，页面上会显示"开始屏幕共享"按钮
4. 点击该按钮开始接收 Comma3 的实时屏幕画面
5. 不同标签页提供了不同类型的信息：
   - 车辆信息：显示车速、方向盘角度、踏板状态等
   - 设备信息：显示 Comma3 的电池状态、系统资源等
   - 位置信息：显示 GPS 位置和导航信息
   - 原始数据：显示接收到的原始 JSON 数据

## 常见问题

### Q: 无法连接到 Comma3 设备

A: 请检查以下几点：
- 确认您的电脑与 Comma3 在同一个局域网内
- 确认 Comma3 上的 commassit.py 服务正在运行
- 检查防火墙设置，确保允许 UDP 广播和 WebRTC 连接
- 尝试使用与 Comma3 匹配的端口运行客户端

### Q: 能看到车辆数据但无法显示屏幕画面

A: 可能的原因：
- Comma3 上的 WebRTC 功能未启用
- WebRTC 依赖未正确安装在 Comma3 上
- 网络限制阻止了 WebRTC 连接
- Comma3 上的 commassit.py 配置不正确

### Q: 屏幕画面延迟很高

A: 尝试以下解决方案：
- 确保使用强信号的 WiFi 连接
- 尝试优化 WebRTC 配置（降低分辨率和帧率）
- 检查您的电脑性能是否足够

## 技术原理

- UDP 广播：Comma3 通过 UDP 广播在局域网中发送车辆数据
- WebRTC：使用 WebRTC 技术传输低延迟视频流
- Flask：提供网页界面和 API 访问

## 隐私和安全提示

- 此工具仅适用于局域网内使用，不建议在公共网络上暴露接口
- 默认配置不使用加密，请勿在不信任的网络中使用
- 驾驶时专注于道路，不要分心查看屏幕