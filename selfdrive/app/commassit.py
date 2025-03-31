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
import base64
import io
from datetime import datetime
from flask import Flask, Response, request
from flask_socketio import SocketIO
import eventlet

from PyQt5.QtCore import Qt, QBuffer, QByteArray, QIODevice
from PyQt5.QtGui import QImage

import cereal.messaging as messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.common.params import Params
from openpilot.system.hardware import PC, TICI
try:
  from selfdrive.car.car_helpers import interfaces
  HAS_CAR_INTERFACES = True
except ImportError:
  HAS_CAR_INTERFACES = False
  interfaces = None

# 全局变量
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

class ScreenCapture:
  """屏幕捕获类，用于获取屏幕画面并转换为可传输的格式"""
  def __init__(self, width=1280, height=720, fps=20, quality=70):
    self.capture_width = width
    self.capture_height = height
    self.fps = fps
    self.quality = quality
    self.running = False

  def capture_screen(self, root_widget):
    """捕获屏幕画面并返回QImage"""
    if root_widget is None:
      return None
    try:
      # 使用QWidget的grab方法捕获屏幕
      pixmap = root_widget.grab()
      return pixmap.toImage()
    except Exception as e:
      print(f"屏幕捕获失败: {e}")
      return None

  def process_image(self, qimage):
    """处理QImage图像"""
    if qimage is None:
      return None

    # 调整图像大小
    scaled_image = qimage.scaled(
      self.capture_width,
      self.capture_height,
      Qt.KeepAspectRatio,
      Qt.SmoothTransformation
    )

    return scaled_image

  def get_jpeg_data(self, qimage):
    """将QImage转换为JPEG数据"""
    if qimage is None:
      return None

    # 创建字节数组缓冲区
    byte_array = QByteArray()
    buffer = QBuffer(byte_array)
    buffer.open(QIODevice.WriteOnly)

    # 保存为JPEG格式
    qimage.save(buffer, "JPEG", self.quality)

    return byte_array.data()

  def get_base64_jpeg(self, qimage):
    """将QImage转换为base64编码的JPEG"""
    jpeg_data = self.get_jpeg_data(qimage)
    if jpeg_data is None:
      return None

    return base64.b64encode(jpeg_data).decode('utf-8')

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

    # 视频流配置
    self.video_port = 8089
    self.screen_capture = ScreenCapture(width=1280, height=720, fps=15, quality=80)
    self.video_streaming = False
    self.root_widget = None
    self.video_clients = set()

    # Flask服务
    self.flask_thread = None

    # 获取车辆信息
    self.car_info = {}
    self.load_car_info()

    # 启动广播线程
    threading.Thread(target=self.broadcast_data).start()

    # 启动视频流线程
    threading.Thread(target=self.start_video_server).start()
    threading.Thread(target=self.video_capture_loop).start()

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
      "video_stream": {
        "available": True,
        "port": self.video_port,
        "url": f"http://{self.ip_address}:{self.video_port}/video"
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

  def start_video_server(self):
    """启动视频流服务器"""
    print(f"启动视频流服务器，端口: {self.video_port}")
    try:
      socketio.run(app, host='0.0.0.0', port=self.video_port)
    except Exception as e:
      print(f"视频流服务器启动失败: {e}")
      traceback.print_exc()

  def video_capture_loop(self):
    """视频捕获循环"""
    rk = Ratekeeper(self.screen_capture.fps, print_delay_threshold=None)
    print("开始视频捕获循环")

    while self.is_running:
      try:
        if len(self.video_clients) > 0 and self.root_widget is not None:
          # 捕获屏幕
          qimage = self.screen_capture.capture_screen(self.root_widget)
          if qimage is not None:
            # 处理图像
            processed_image = self.screen_capture.process_image(qimage)
            if processed_image is not None:
              # 转换为base64编码的JPEG
              base64_img = self.screen_capture.get_base64_jpeg(processed_image)
              if base64_img is not None:
                # 通过WebSocket发送
                socketio.emit('video_frame', {'frame': base64_img})

        rk.keep_time()
      except Exception as e:
        print(f"视频捕获错误: {e}")
        traceback.print_exc()
        time.sleep(1)

  def update_screen(self):
    """获取当前UI界面的根窗口"""
    if self.root_widget is None:
      try:
        # 尝试获取当前窗口 (使用PyQt5直接方式)
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
          for widget in app.topLevelWidgets():
            if widget.isVisible():
              self.root_widget = widget
              print("成功获取UI根窗口")
              break
      except Exception as e:
        print(f"获取UI窗口失败: {e}")

# 添加Socket.IO事件处理
@socketio.on('connect')
def handle_connect():
    """处理客户端连接"""
    comma_assist.video_clients.add(request.sid)
    print(f"客户端连接: {request.sid}, 当前客户端数: {len(comma_assist.video_clients)}")

@socketio.on('disconnect')
def handle_disconnect():
    """处理客户端断开连接"""
    try:
        comma_assist.video_clients.remove(request.sid)
    except KeyError:
        pass
    print(f"客户端断开: {request.sid}, 当前客户端数: {len(comma_assist.video_clients)}")

# 视频流路由
@app.route('/video')
def video_feed():
    """视频流页面"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Comma3 实时视频流</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
        <style>
            body { margin: 0; padding: 0; font-family: Arial, sans-serif; }
            #video-container { width: 100%; max-width: 1280px; margin: 0 auto; }
            #video-stream { width: 100%; height: auto; }
            h1 { text-align: center; }
        </style>
    </head>
    <body>
        <h1>Comma3 实时视频流</h1>
        <div id="video-container">
            <img id="video-stream" src="" alt="等待视频流...">
        </div>
        <script>
            const socket = io.connect(window.location.origin);
            const img = document.getElementById('video-stream');

            socket.on('video_frame', function(data) {
                img.src = 'data:image/jpeg;base64,' + data.frame;
            });

            socket.on('connect', function() {
                console.log('已连接到视频服务器');
            });

            socket.on('disconnect', function() {
                console.log('与视频服务器断开连接');
                img.src = '';
            });
        </script>
    </body>
    </html>
    """

def main(gctx=None):
  """主函数
  支持作为独立程序运行或由process_config启动
  gctx参数用于与openpilot进程管理系统兼容
  """
  global comma_assist
  comma_assist = CommaAssist()

  # 保持主线程运行
  try:
    while True:
      time.sleep(1)
      comma_assist.update_screen()  # 定期更新屏幕对象
  except KeyboardInterrupt:
    comma_assist.is_running = False
    print("CommaAssist服务已停止")

if __name__ == "__main__":
  main()