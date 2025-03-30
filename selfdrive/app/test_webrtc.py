#!/usr/bin/env python3
import argparse
import requests
import json
import sys
import time

def test_webrtc_connection(url):
    """测试WebRTC服务是否可访问"""
    print(f"正在测试WebRTC服务连接: {url}")

    try:
        # 尝试访问WebRTC服务根页面
        start_time = time.time()
        response = requests.get(url, timeout=5)
        elapsed = time.time() - start_time

        if response.status_code == 200:
            print(f"✅ 已成功连接到WebRTC服务 ({elapsed:.2f}秒)")
            print(f"页面大小: {len(response.content)} 字节")
            return True
        else:
            print(f"❌ 服务器返回错误: {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("❌ 无法连接到服务器，请检查URL是否正确")
        return False
    except requests.exceptions.Timeout:
        print("❌ 连接超时，服务器响应时间过长")
        return False
    except Exception as e:
        print(f"❌ 连接测试出错: {e}")
        return False

def test_udp_broadcast(ip, port=8088):
    """测试UDP广播是否可接收"""
    import socket

    print(f"正在测试UDP广播接收 (监听 {ip}:{port})...")

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(10)  # 设置10秒超时
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((ip, port))

        print(f"等待来自Comma3的UDP广播 (10秒超时)...")

        try:
            data, addr = sock.recvfrom(4096)
            print(f"✅ 成功接收来自 {addr[0]} 的UDP广播")

            try:
                # 尝试解析JSON
                msg = json.loads(data.decode('utf-8'))
                print(f"✅ 成功解析JSON数据")

                # 检查是否包含WebRTC信息
                if 'webrtc' in msg and msg['webrtc'].get('enabled', False):
                    webrtc_url = msg['webrtc'].get('url', '')
                    print(f"✅ 检测到WebRTC信息: {webrtc_url}")
                    return webrtc_url
                else:
                    print("❌ 未检测到WebRTC信息，请确认Comma3已启用WebRTC功能")
            except json.JSONDecodeError:
                print("❌ 接收到无效的JSON数据")

            return None
        except socket.timeout:
            print("❌ 等待超时，未收到UDP广播")
            return None
    except Exception as e:
        print(f"❌ UDP监听出错: {e}")
        return None
    finally:
        sock.close()

def main():
    parser = argparse.ArgumentParser(description='测试WebRTC连接和UDP广播')
    parser.add_argument('-u', '--url', help='WebRTC服务URL，例如 http://192.168.1.10:8089')
    parser.add_argument('-p', '--port', type=int, default=8088, help='UDP广播监听端口 (默认: 8088)')
    parser.add_argument('-i', '--ip', default='0.0.0.0', help='UDP广播监听地址 (默认: 0.0.0.0)')

    args = parser.parse_args()

    # 如果提供了URL，直接测试WebRTC连接
    if args.url:
        if test_webrtc_connection(args.url):
            print("\n诊断: WebRTC服务可以访问")
            print("您可以在commawebview.py中使用此URL进行连接")
        else:
            print("\n诊断: WebRTC服务无法访问，请检查:")
            print("1. URL是否正确")
            print("2. Comma3是否已启动WebRTC服务")
            print("3. 网络连接是否正常")
    # 否则，先测试UDP广播
    else:
        print("未提供WebRTC URL，将先尝试接收UDP广播...")
        webrtc_url = test_udp_broadcast(args.ip, args.port)

        if webrtc_url:
            print("\n自动检测到WebRTC URL，正在测试连接...")
            if test_webrtc_connection(webrtc_url):
                print("\n诊断: 一切正常! UDP广播和WebRTC服务都可以访问")
                print(f"您可以通过 {webrtc_url} 访问Comma3的WebRTC服务")
            else:
                print("\n诊断: UDP广播正常，但WebRTC服务无法访问")
                print("请检查WebRTC服务是否正确启动")
        else:
            print("\n诊断: 未能接收到有效的UDP广播")
            print("请检查:")
            print("1. Comma3和电脑是否在同一个局域网内")
            print("2. commassit.py是否正在运行")
            print("3. 防火墙是否允许UDP广播通过")

    return 0

if __name__ == "__main__":
    sys.exit(main())