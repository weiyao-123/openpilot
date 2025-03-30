#!/usr/bin/env python3
import fcntl
import json
import math
import os
import socket
import struct
import threading
import time
import traceback
import zmq
from datetime import datetime
import cv2
import numpy as np
import asyncio
import logging

import cereal.messaging as messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.common.params import Params
from openpilot.system.hardware import PC, TICI

# WebRTC 相关库
try:
    import aiohttp
    from aiohttp import web
    from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
    from aiortc.contrib.media import MediaPlayer, MediaRelay
    import av
    HAS_WEBRTC = True
except ImportError:
    print("WebRTC 相关库未安装，屏幕共享功能将不可用")
    HAS_WEBRTC = False

try:
  from selfdrive.car.car_helpers import interfaces
  HAS_CAR_INTERFACES = True
except ImportError:
  HAS_CAR_INTERFACES = False
  interfaces = None

# 设置日志级别
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CommaAssist")

# 加载WebRTC配置
def load_webrtc_config():
    """加载WebRTC配置文件，如果不存在则使用默认值"""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webrtc_config.json")
    default_config = {
        "enabled": True,
        "port": 8089,
        "fps": 10,
        "width": 640,
        "height": 360,
        "use_https": False,
        "stun_servers": ["stun:stun.l.google.com:19302"]
    }

    try:
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                config = json.load(f)
                logger.info(f"已加载WebRTC配置: {config_path}")
                # 合并默认配置，确保所有必要的字段都存在
                for key, value in default_config.items():
                    if key not in config:
                        config[key] = value
                return config
    except Exception as e:
        logger.error(f"加载WebRTC配置失败: {e}")

    logger.info("使用WebRTC默认配置")
    return default_config

# 全局WebRTC配置
WEBRTC_CONFIG = load_webrtc_config()

# WebRTC 视频流相关类
class CommaVideoStreamTrack(VideoStreamTrack):
    """
    用于捕获并发送设备屏幕的视频流
    """
    def __init__(self, fps=10, width=640, height=360):
        super().__init__()
        self.fps = fps
        self.width = width
        self.height = height
        self.counter = 0
        self.last_frame_time = time.time()

        # 在 comma3 上，使用截屏命令获取屏幕内容
        self.is_comma3 = TICI

        # 初始化计数器和帧率控制
        self.frame_count = 0
        self.start_time = time.time()

    async def next_timestamp(self):
        """控制帧率"""
        self.counter += 1
        wait_time = 1 / self.fps
        current_time = time.time()
        elapsed = current_time - self.last_frame_time

        if elapsed < wait_time:
            await asyncio.sleep(wait_time - elapsed)

        self.last_frame_time = time.time()
        return self.counter

    async def recv(self):
        """获取并返回下一帧视频"""
        pts, time_base = await self.next_timestamp(), av.VideoDupedFrameSource.time_base

        try:
            # 在 comma3 上使用截屏命令
            if self.is_comma3:
                # 使用 screenshot 命令抓取屏幕
                os.system("screenshot /tmp/comma_screen.png")
                img = cv2.imread("/tmp/comma_screen.png")
                if img is None:
                    # 如果截图失败，生成一个黑色画面
                    img = np.zeros((self.height, self.width, 3), dtype=np.uint8)
            else:
                # 在其他设备上，可能需要其他方式获取屏幕内容
                # 这里暂时生成一个测试画面
                img = np.zeros((self.height, self.width, 3), dtype=np.uint8)
                cv2.putText(img, f"CommaAssist Screen {self.counter}", (50, self.height // 2),
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)

            # 调整图像大小以降低带宽需求
            img = cv2.resize(img, (self.width, self.height))

            # 转换为 aiortc 可用的帧格式
            frame = av.VideoFrame.from_ndarray(img, format="bgr24")
            frame.pts = pts
            frame.time_base = time_base

            # 计算实际帧率
            self.frame_count += 1
            elapsed = time.time() - self.start_time
            if elapsed > 5:  # 每5秒记录一次实际帧率
                logger.info(f"实际视频帧率: {self.frame_count / elapsed:.2f} fps")
                self.frame_count = 0
                self.start_time = time.time()

            return frame
        except Exception as e:
            logger.error(f"获取视频帧时出错: {e}")
            # 出错时返回黑色画面
            img = np.zeros((self.height, self.width, 3), dtype=np.uint8)
            cv2.putText(img, "Error", (self.width // 2 - 40, self.height // 2),
                       cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            frame = av.VideoFrame.from_ndarray(img, format="bgr24")
            frame.pts = pts
            frame.time_base = time_base
            return frame

# WebRTC 服务类
class WebRTCServer:
    def __init__(self, host='0.0.0.0', port=8089, fps=10, width=640, height=360, stun_servers=None):
        self.host = host
        self.port = port
        self.pcs = set()
        self.video_track = None
        self.fps = fps
        self.width = width
        self.height = height
        self.relay = MediaRelay()
        self.app = None
        self.runner = None
        self.site = None
        self.is_running = False
        self.stun_servers = stun_servers or ["stun:stun.l.google.com:19302"]

    async def offer(self, request):
        """处理客户端的 WebRTC offer 请求"""
        params = await request.json()
        offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

        pc = RTCPeerConnection(configuration={"iceServers": [{"urls": self.stun_servers}]})
        self.pcs.add(pc)

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            logger.info(f"连接状态变更: {pc.connectionState}")
            if pc.connectionState == "failed":
                await pc.close()
                self.pcs.discard(pc)

        # 创建视频流
        if self.video_track is None:
            self.video_track = CommaVideoStreamTrack(fps=self.fps, width=self.width, height=self.height)

        # 将视频流添加到对等连接
        pc.addTrack(self.relay.subscribe(self.video_track))

        # 设置本地描述
        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        return web.json_response({
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type
        })

    async def on_shutdown(self, app):
        """关闭所有对等连接"""
        coros = [pc.close() for pc in self.pcs]
        await asyncio.gather(*coros)
        self.pcs.clear()

    async def index(self, request):
        """返回WebRTC客户端页面"""
        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Comma3 屏幕共享</title>
            <style>
                body { margin: 0; padding: 20px; font-family: Arial, sans-serif; text-align: center; background-color: #f0f0f0; }
                video { max-width: 100%; background-color: #000; border-radius: 8px; }
                .container { max-width: 800px; margin: 0 auto; }
                button { background-color: #4CAF50; border: none; color: white; padding: 10px 20px;
                        text-align: center; text-decoration: none; display: inline-block; font-size: 16px;
                        margin: 10px 2px; cursor: pointer; border-radius: 4px; }
                button:disabled { background-color: #cccccc; }
                .status { margin: 10px 0; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Comma3 屏幕共享</h1>
                <div>
                    <button id="start">开始</button>
                    <button id="stop" disabled>停止</button>
                </div>
                <div class="status" id="status">未连接</div>
                <video id="video" autoplay playsinline muted></video>
            </div>

            <script>
                const videoElement = document.getElementById('video');
                const startButton = document.getElementById('start');
                const stopButton = document.getElementById('stop');
                const statusElement = document.getElementById('status');
                let pc = null;

                startButton.addEventListener('click', start);
                stopButton.addEventListener('click', stop);

                async function start() {
                    if (pc) {
                        return;
                    }

                    try {
                        startButton.disabled = true;
                        stopButton.disabled = false;
                        statusElement.textContent = '正在连接...';

                        pc = new RTCPeerConnection({
                            iceServers: [
                                { urls: 'stun:stun.l.google.com:19302' },
                                { urls: 'stun:stun1.l.google.com:19302' }
                            ]
                        });

                        pc.addEventListener('track', function(evt) {
                            if (evt.track.kind == 'video') {
                                videoElement.srcObject = evt.streams[0];
                                statusElement.textContent = '已连接';
                            }
                        });

                        pc.addEventListener('connectionstatechange', function() {
                            if (pc.connectionState === 'failed' || pc.connectionState === 'disconnected') {
                                stop();
                            }
                        });

                        // 创建offer
                        const offer = await pc.createOffer({
                            offerToReceiveVideo: true
                        });
                        await pc.setLocalDescription(offer);

                        // 发送offer到服务器
                        const response = await fetch('/offer', {
                            body: JSON.stringify({
                                sdp: pc.localDescription.sdp,
                                type: pc.localDescription.type
                            }),
                            headers: {
                                'Content-Type': 'application/json'
                            },
                            method: 'POST'
                        });

                        // 处理服务器返回的answer
                        const answer = await response.json();
                        await pc.setRemoteDescription(answer);
                    } catch (e) {
                        console.error('连接失败:', e);
                        stop();
                    }
                }

                function stop() {
                    if (pc) {
                        pc.close();
                        pc = null;
                    }

                    videoElement.srcObject = null;
                    startButton.disabled = false;
                    stopButton.disabled = true;
                    statusElement.textContent = '已断开';
                }

                // 页面关闭时断开连接
                window.addEventListener('beforeunload', function() {
                    if (pc) {
                        pc.close();
                        pc = null;
                    }
                });
            </script>
        </body>
        </html>
        """
        return web.Response(content_type="text/html", text=html_content)

    async def start_server(self):
        """启动WebRTC服务器"""
        if not HAS_WEBRTC:
            logger.error("未安装WebRTC相关库，无法启动WebRTC服务")
            return

        try:
            self.is_running = True
            self.app = web.Application()
            self.app.on_shutdown.append(self.on_shutdown)
            self.app.router.add_get("/", self.index)
            self.app.router.add_post("/offer", self.offer)

            self.runner = web.AppRunner(self.app)
            await self.runner.setup()
            self.site = web.TCPSite(self.runner, self.host, self.port)

            logger.info(f"启动WebRTC服务器 {self.host}:{self.port}")
            await self.site.start()
        except Exception as e:
            logger.error(f"启动WebRTC服务器失败: {e}")
            self.is_running = False

    async def stop_server(self):
        """停止WebRTC服务器"""
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        self.is_running = False
        logger.info("WebRTC服务器已停止")

class CommaAssist:
  def __init__(self):
    print("初始化 CommaAssist 服务...")
    # 初始化参数
    self.params = Params()

    # 订阅需要的数据源
    self.sm = messaging.SubMaster(['deviceState', 'carState', 'controlsState',
                                   'longitudinalPlan', 'liveLocationKalman',
                                   'navInstruction', 'modelV2'])

    # 网络相关配置
    self.broadcast_ip = self.get_broadcast_address()
    if self.broadcast_ip is None:
      self.broadcast_ip = "255.255.255.255"  # 使用通用广播地址作为备选
    self.broadcast_port = 8088
    self.ip_address = "0.0.0.0"
    self.is_running = True

    # 获取车辆信息
    self.car_info = {}
    self.load_car_info()

    # 启动广播线程
    threading.Thread(target=self.broadcast_data).start()

    # 初始化并启动WebRTC服务
    if WEBRTC_CONFIG["enabled"] and HAS_WEBRTC:
      self.webrtc_server = WebRTCServer(
          host=self.get_local_ip(),
          port=WEBRTC_CONFIG["port"],
          fps=WEBRTC_CONFIG["fps"],
          width=WEBRTC_CONFIG["width"],
          height=WEBRTC_CONFIG["height"],
          stun_servers=WEBRTC_CONFIG["stun_servers"]
      )
      self.start_webrtc_server()
    else:
      self.webrtc_server = None
      print("WebRTC功能已禁用或相关库未安装")

  def start_webrtc_server(self):
    """启动WebRTC服务器"""
    if HAS_WEBRTC and self.webrtc_server:
      # 在单独的线程中启动异步WebRTC服务
      def run_webrtc_server():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.webrtc_server.start_server())
        loop.run_forever()

      threading.Thread(target=run_webrtc_server, daemon=True).start()
      print(f"WebRTC服务器已在后台启动 (http://{self.get_local_ip()}:{WEBRTC_CONFIG['port']}/)")
    else:
      print("未安装WebRTC相关库，屏幕共享功能不可用")

  def load_car_info(self):
    """加载车辆基本信息"""
    try:
      # 获取车辆型号信息
      try:
        car_model = self.params.get("CarModel", encoding='utf8')
        self.car_info["car_name"] = car_model if car_model else "Unknown"
      except Exception as e:
        print(f"无法获取CarModel参数: {e}")
        try:
          # 尝试获取其他可能的车辆参数
          car_params = self.params.get("CarParamsCache", encoding='utf8')
          self.car_info["car_name"] = "通过CarParamsCache获取" if car_params else "Unknown"
        except Exception:
          self.car_info["car_name"] = "Unknown Model"
          car_model = None

      # 检查车辆接口是否可用
      if not HAS_CAR_INTERFACES:
        print("车辆接口模块不可用")
      elif not car_model:
        print("无有效的车型信息")
      elif not isinstance(interfaces, list):
        print("车辆接口不是列表类型，尝试转换...")
        # 尝试获取车辆接口的具体实现
        if hasattr(interfaces, '__call__'):
          # 如果interfaces是一个函数，尝试直接获取车辆指纹
          try:
            self.car_info["car_fingerprint"] = f"直接从车型{car_model}获取"
            print(f"直接从车型识别: {car_model}")
          except Exception as e:
            print(f"无法从车型直接获取指纹: {e}")
      else:
        # 正常遍历接口列表
        print("尝试从车辆接口中获取指纹信息...")
        for interface in interfaces:
          if not hasattr(interface, 'CHECKSUM'):
            continue

          try:
            if isinstance(interface.CHECKSUM, dict) and 'pt' in interface.CHECKSUM:
              if car_model in interface.CHECKSUM["pt"]:
                platform = interface
                self.car_info["car_fingerprint"] = platform.config.platform_str

                # 获取车辆规格参数
                specs = platform.config.specs
                if specs:
                  if hasattr(specs, 'mass'):
                    self.car_info["mass"] = specs.mass
                  if hasattr(specs, 'wheelbase'):
                    self.car_info["wheelbase"] = specs.wheelbase
                  if hasattr(specs, 'steerRatio'):
                    self.car_info["steerRatio"] = specs.steerRatio
                break
          except Exception as e:
            print(f"处理特定车辆接口异常: {e}")

    except Exception as e:
      print(f"加载车辆信息失败: {e}")
      traceback.print_exc()

    # 确保基本字段存在，避免后续访问出错
    if "car_name" not in self.car_info:
      self.car_info["car_name"] = "Unknown Model"
    if "car_fingerprint" not in self.car_info:
      self.car_info["car_fingerprint"] = "Unknown Fingerprint"

    print(f"车辆信息加载完成: {self.car_info}")

  def get_broadcast_address(self):
    """获取广播地址"""
    try:
      if PC:
        iface = b'br0'
      else:
        iface = b'wlan0'

      with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        ip = fcntl.ioctl(
          s.fileno(),
          0x8919,  # SIOCGIFADDR
          struct.pack('256s', iface)
        )[20:24]
        ip_str = socket.inet_ntoa(ip)
        print(f"获取到IP地址: {ip_str}")
        # 从IP地址构造广播地址
        ip_parts = ip_str.split('.')
        return f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}.255"
    except (OSError, Exception) as e:
      print(f"获取广播地址失败: {e}")
      return None

  def get_local_ip(self):
    """获取本地IP地址"""
    try:
      with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception as e:
      print(f"获取本地IP失败: {e}")
      return "127.0.0.1"

  def make_data_message(self):
    """构建广播消息内容"""
    # 基本消息结构
    message = {
      "timestamp": int(time.time()),
      "device": {
        "ip": self.ip_address,
        "battery": {},
        "mem_usage": 0,
        "cpu_temp": 0,
        "free_space": 0
      },
      "car": {
        "speed": 0,
        "cruise_speed": 0,
        "gear_shifter": "unknown",
        "steering_angle": 0,
        "steering_torque": 0,
        "brake_pressed": False,
        "gas_pressed": False,
        "door_open": False,
        "left_blinker": False,
        "right_blinker": False
      },
      "location": {
        "latitude": 0,
        "longitude": 0,
        "bearing": 0,
        "speed": 0,
        "altitude": 0,
        "accuracy": 0,
        "gps_valid": False
      },
      "car_info": {
        "basic": {
          "car_model": self.car_info.get("car_name", "Unknown"),
          "fingerprint": self.car_info.get("car_fingerprint", "Unknown"),
          "weight": f"{self.car_info.get('mass', 0):.0f} kg" if 'mass' in self.car_info else "Unknown",
          "wheelbase": f"{self.car_info.get('wheelbase', 0):.3f} m" if 'wheelbase' in self.car_info else "Unknown",
          "steering_ratio": f"{self.car_info.get('steerRatio', 0):.1f}" if 'steerRatio' in self.car_info else "Unknown"
        }
      },
      "webrtc": {
        "enabled": HAS_WEBRTC and WEBRTC_CONFIG["enabled"],
        "url": f"http://{self.ip_address}:{WEBRTC_CONFIG['port']}" if HAS_WEBRTC and WEBRTC_CONFIG["enabled"] else ""
      }
    }

    # 安全地获取设备信息
    try:
      if self.sm.updated['deviceState'] and self.sm.valid['deviceState']:
        device_state = self.sm['deviceState']

        # 获取设备信息 - 使用getattr安全地访问属性
        message["device"]["mem_usage"] = getattr(device_state, 'memoryUsagePercent', 0)
        message["device"]["free_space"] = getattr(device_state, 'freeSpacePercent', 0)

        # CPU温度
        cpu_temps = getattr(device_state, 'cpuTempC', [])
        if isinstance(cpu_temps, list) and len(cpu_temps) > 0:
          message["device"]["cpu_temp"] = cpu_temps[0]

        # 电池信息
        try:  # 额外的错误处理，因为这是常见错误点
          message["device"]["battery"]["percent"] = getattr(device_state, 'batteryPercent', 0)
          message["device"]["battery"]["status"] = getattr(device_state, 'batteryCurrent', 0)
          message["device"]["battery"]["voltage"] = getattr(device_state, 'batteryVoltage', 0)
          message["device"]["battery"]["charging"] = getattr(device_state, 'chargingError', False)
        except Exception as e:
          print(f"获取电池信息失败: {e}")
    except Exception as e:
      print(f"获取设备状态出错: {e}")

    # 安全地获取车辆信息
    try:
      if self.sm.updated['carState'] and self.sm.valid['carState']:
        CS = self.sm['carState']

        # 基本车辆信息
        message["car"]["speed"] = getattr(CS, 'vEgo', 0) * 3.6  # m/s转km/h
        message["car"]["gear_shifter"] = str(getattr(CS, 'gearShifter', "unknown"))
        message["car"]["steering_angle"] = getattr(CS, 'steeringAngleDeg', 0)
        message["car"]["steering_torque"] = getattr(CS, 'steeringTorque', 0)
        message["car"]["brake_pressed"] = getattr(CS, 'brakePressed', False)
        message["car"]["gas_pressed"] = getattr(CS, 'gasPressed', False)
        message["car"]["door_open"] = getattr(CS, 'doorOpen', False)
        message["car"]["left_blinker"] = getattr(CS, 'leftBlinker', False)
        message["car"]["right_blinker"] = getattr(CS, 'rightBlinker', False)

        # 扩展的车辆状态信息
        is_car_started = getattr(CS, 'vEgo', 0) > 0.1
        is_car_engaged = False

        # 详细车辆信息
        car_details = {}

        # 车辆状态
        status = {
          "running_status": "Moving" if is_car_started else "Stopped",
          "door_open": getattr(CS, 'doorOpen', False),
          "seatbelt_unlatched": getattr(CS, 'seatbeltUnlatched', False),
        }

        # 引擎信息
        engine_info = {}
        if hasattr(CS, 'engineRpm') and CS.engineRpm > 0:
          engine_info["rpm"] = f"{CS.engineRpm:.0f}"
        car_details["engine"] = engine_info

        # 巡航控制
        cruise_info = {}
        if hasattr(CS, 'cruiseState'):
          is_car_engaged = getattr(CS.cruiseState, 'enabled', False)
          cruise_info["enabled"] = getattr(CS.cruiseState, 'enabled', False)
          cruise_info["available"] = getattr(CS.cruiseState, 'available', False)
          cruise_info["speed"] = getattr(CS.cruiseState, 'speed', 0) * 3.6

          if hasattr(CS, 'pcmCruiseGap'):
            cruise_info["gap"] = CS.pcmCruiseGap

        status["cruise_engaged"] = is_car_engaged
        car_details["cruise"] = cruise_info

        # 车轮速度
        wheel_speeds = {}
        if hasattr(CS, 'wheelSpeeds'):
          ws = CS.wheelSpeeds
          wheel_speeds["fl"] = getattr(ws, 'fl', 0) * 3.6
          wheel_speeds["fr"] = getattr(ws, 'fr', 0) * 3.6
          wheel_speeds["rl"] = getattr(ws, 'rl', 0) * 3.6
          wheel_speeds["rr"] = getattr(ws, 'rr', 0) * 3.6
        car_details["wheel_speeds"] = wheel_speeds

        # 方向盘信息
        steering = {
          "angle": getattr(CS, 'steeringAngleDeg', 0),
          "torque": getattr(CS, 'steeringTorque', 0),
        }
        if hasattr(CS, 'steeringRateDeg'):
          steering["rate"] = CS.steeringRateDeg
        car_details["steering"] = steering

        # 踏板状态
        pedals = {
          "gas_pressed": getattr(CS, 'gasPressed', False),
          "brake_pressed": getattr(CS, 'brakePressed', False),
        }
        if hasattr(CS, 'gas'):
          pedals["throttle_position"] = CS.gas * 100
        if hasattr(CS, 'brake'):
          pedals["brake_pressure"] = CS.brake * 100
        car_details["pedals"] = pedals

        # 安全系统
        safety_systems = {}
        if hasattr(CS, 'espDisabled'):
          safety_systems["esp_disabled"] = CS.espDisabled
        if hasattr(CS, 'absActive'):
          safety_systems["abs_active"] = CS.absActive
        if hasattr(CS, 'tcsActive'):
          safety_systems["tcs_active"] = CS.tcsActive
        if hasattr(CS, 'collisionWarning'):
          safety_systems["collision_warning"] = CS.collisionWarning
        car_details["safety_systems"] = safety_systems

        # 车门状态
        doors = {
          "driver": getattr(CS, 'doorOpen', False)
        }
        if hasattr(CS, 'passengerDoorOpen'):
          doors["passenger"] = CS.passengerDoorOpen
        if hasattr(CS, 'trunkOpen'):
          doors["trunk"] = CS.trunkOpen
        if hasattr(CS, 'hoodOpen'):
          doors["hood"] = CS.hoodOpen
        car_details["doors"] = doors

        # 灯光状态
        lights = {
          "left_blinker": getattr(CS, 'leftBlinker', False),
          "right_blinker": getattr(CS, 'rightBlinker', False),
        }
        if hasattr(CS, 'genericToggle'):
          lights["high_beam"] = CS.genericToggle
        if hasattr(CS, 'lowBeamOn'):
          lights["low_beam"] = CS.lowBeamOn
        car_details["lights"] = lights

        # 盲点监测
        blind_spot = {}
        if hasattr(CS, 'leftBlindspot'):
          blind_spot["left"] = CS.leftBlindspot
        if hasattr(CS, 'rightBlindspot'):
          blind_spot["right"] = CS.rightBlindspot
        if blind_spot:
          car_details["blind_spot"] = blind_spot

        # 其他可选信息
        other_info = {}
        if hasattr(CS, 'outsideTemp'):
          other_info["outside_temp"] = CS.outsideTemp
        if hasattr(CS, 'fuelGauge'):
          other_info["fuel_range"] = CS.fuelGauge
        if hasattr(CS, 'odometer'):
          other_info["odometer"] = CS.odometer
        if hasattr(CS, 'instantFuelConsumption'):
          other_info["fuel_consumption"] = CS.instantFuelConsumption
        if other_info:
          car_details["other"] = other_info

        # 更新状态和详细信息
        message["car_info"]["status"] = status
        message["car_info"]["details"] = car_details

      if self.sm.updated['controlsState'] and self.sm.valid['controlsState']:
        controls_state = self.sm['controlsState']
        message["car"]["cruise_speed"] = getattr(controls_state, 'vCruise', 0)

        # 额外的控制状态信息
        controls_info = {}
        if hasattr(controls_state, 'enabled'):
          controls_info["enabled"] = controls_state.enabled
        if hasattr(controls_state, 'active'):
          controls_info["active"] = controls_state.active
        if hasattr(controls_state, 'alertText1'):
          controls_info["alert_text"] = controls_state.alertText1
        if controls_info:
          message["car_info"]["controls"] = controls_info

    except Exception as e:
      print(f"获取车辆信息出错: {e}")
      traceback.print_exc()

    # 安全地获取GPS位置信息
    try:
      if self.sm.updated['liveLocationKalman'] and self.sm.valid['liveLocationKalman']:
        location = self.sm['liveLocationKalman']

        # 检查GPS是否有效
        location_status = getattr(location, 'status', -1)
        position_valid = False
        if hasattr(location, 'positionGeodetic'):
          position_valid = getattr(location.positionGeodetic, 'valid', False)

        gps_valid = (location_status == 0) and position_valid
        message["location"]["gps_valid"] = gps_valid

        if gps_valid and hasattr(location, 'positionGeodetic') and hasattr(location.positionGeodetic, 'value'):
          # 获取位置信息
          pos_value = location.positionGeodetic.value
          if len(pos_value) >= 3:
            message["location"]["latitude"] = pos_value[0]
            message["location"]["longitude"] = pos_value[1]
            message["location"]["altitude"] = pos_value[2]

          # 获取精度信息
          if hasattr(location, 'positionGeodeticStd') and hasattr(location.positionGeodeticStd, 'value'):
            std_value = location.positionGeodeticStd.value
            if len(std_value) > 0:
              message["location"]["accuracy"] = std_value[0]

          # 获取方向信息
          if hasattr(location, 'calibratedOrientationNED') and hasattr(location.calibratedOrientationNED, 'value'):
            orientation = location.calibratedOrientationNED.value
            if len(orientation) > 2:
              message["location"]["bearing"] = math.degrees(orientation[2])

          # 设置速度信息
          car_state = self.sm['carState'] if self.sm.valid['carState'] else None
          if car_state and hasattr(car_state, 'vEgo'):
            message["location"]["speed"] = car_state.vEgo * 3.6
    except Exception as e:
      print(f"获取位置信息出错: {e}")

    # 如果有导航指令，添加导航信息
    try:
      if self.sm.valid['navInstruction']:
        nav_instruction = self.sm['navInstruction']

        nav_info = {}
        nav_info["distance_remaining"] = getattr(nav_instruction, 'distanceRemaining', 0)
        nav_info["time_remaining"] = getattr(nav_instruction, 'timeRemaining', 0)
        nav_info["speed_limit"] = getattr(nav_instruction, 'speedLimit', 0) * 3.6
        nav_info["maneuver_distance"] = getattr(nav_instruction, 'maneuverDistance', 0)
        nav_info["maneuver_type"] = getattr(nav_instruction, 'maneuverType', "")
        nav_info["maneuver_modifier"] = getattr(nav_instruction, 'maneuverModifier', "")
        nav_info["maneuver_text"] = getattr(nav_instruction, 'maneuverPrimaryText', "")

        message["navigation"] = nav_info
    except Exception as e:
      print(f"获取导航信息出错: {e}")

    try:
      return json.dumps(message)
    except Exception as e:
      print(f"序列化消息出错: {e}")
      return "{}"

  def broadcast_data(self):
    """定期发送数据到广播地址"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    rk = Ratekeeper(10, print_delay_threshold=None)  # 10Hz广播频率

    print(f"开始广播数据到 {self.broadcast_ip}:{self.broadcast_port}")

    while self.is_running:
      try:
        # 更新数据
        self.sm.update(0)

        # 更新IP地址
        ip_address = self.get_local_ip()
        if ip_address != self.ip_address:
          self.ip_address = ip_address
          print(f"IP地址已更新: {ip_address}")

        # 构建并发送消息
        msg = self.make_data_message()
        dat = msg.encode('utf-8')
        sock.sendto(dat, (self.broadcast_ip, self.broadcast_port))

        # 减少日志输出频率
        if rk.frame % 50 == 0:  # 每5秒打印一次日志
          print(f"广播数据: {self.broadcast_ip}:{self.broadcast_port}")

        rk.keep_time()
      except Exception as e:
        print(f"广播数据错误: {e}")
        traceback.print_exc()
        time.sleep(1)

def main(gctx=None):
  """主函数
  支持作为独立程序运行或由process_config启动
  gctx参数用于与openpilot进程管理系统兼容
  """
  comma_assist = CommaAssist()

  # 保持主线程运行
  try:
    while True:
      time.sleep(10)  # 主线程休眠
  except KeyboardInterrupt:
    comma_assist.is_running = False
    # 停止WebRTC服务
    if HAS_WEBRTC and comma_assist.webrtc_server and comma_assist.webrtc_server.is_running:
      loop = asyncio.new_event_loop()
      asyncio.set_event_loop(loop)
      loop.run_until_complete(comma_assist.webrtc_server.stop_server())
    print("CommaAssist服务已停止")

if __name__ == "__main__":
  main()