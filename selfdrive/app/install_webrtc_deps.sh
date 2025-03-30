#!/bin/bash

echo "开始安装 WebRTC 依赖..."

# 确保使用 sudo 权限
if [ "$(id -u)" -ne 0 ]; then
    echo "请使用 sudo 运行此脚本"
    exit 1
fi

# 设置安装标志文件
FLAG_FILE="/data/openpilot/selfdrive/app/.webrtc_installed"

# 检查是否已经安装
if [ -f "$FLAG_FILE" ]; then
    echo "WebRTC 依赖已经安装，如需重新安装请删除 $FLAG_FILE"
    exit 0
fi

# 更新软件包列表
apt-get update

# 安装基本依赖
apt-get install -y python3-pip python3-dev build-essential libssl-dev libffi-dev python3-setuptools

# 安装视频处理依赖
apt-get install -y ffmpeg libavformat-dev libavcodec-dev libavdevice-dev libavutil-dev \
                   libswscale-dev libswresample-dev libavfilter-dev libopus-dev libvpx-dev

# 安装OpenCV依赖
apt-get install -y libsm6 libxext6 libxrender-dev

# 安装Python依赖
pip3 install --upgrade pip
pip3 install aiohttp aiortc av opencv-python numpy

# 创建标志文件
touch "$FLAG_FILE"

echo "WebRTC 依赖安装完成!"
echo "请重启 commassit 服务以启用屏幕共享功能"