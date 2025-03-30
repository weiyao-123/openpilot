#!/bin/bash

echo "开始安装 CommaWebView 依赖..."

# 检查是否安装了pip
if ! command -v pip3 &> /dev/null; then
    echo "未找到pip3，请先安装Python3及pip"
    exit 1
fi

# 安装Python依赖
echo "安装Python依赖..."
pip3 install flask requests

echo "依赖安装完成!"
echo "现在您可以运行 python selfdrive/app/commawebview.py 来启动WebView客户端"
echo "然后在浏览器中访问 http://localhost:5000"