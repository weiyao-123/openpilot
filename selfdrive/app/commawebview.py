#!/usr/bin/env python3
import json
import socket
import threading
import time
import traceback
from datetime import datetime
import argparse
from flask import Flask, render_template_string, jsonify, render_template

class CommaWebListener:
    def __init__(self, port=8088):
        """初始化接收器"""
        self.port = port
        self.data = {}
        self.last_update = 0
        self.running = True
        self.device_ip = None
        self.video_stream_url = None
        self.video_available = False

    def start_listening(self):
        """启动UDP监听"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('0.0.0.0', self.port))
            print(f"正在监听端口 {self.port} 的广播数据...")

            while self.running:
                try:
                    data, addr = sock.recvfrom(4096)
                    self.device_ip = addr[0]
                    try:
                        json_data = json.loads(data.decode('utf-8'))
                        self.data = json_data

                        # 获取视频流信息
                        if 'video_stream' in json_data:
                            self.video_available = json_data['video_stream'].get('available', False)
                            if self.video_available:
                                self.video_stream_url = json_data['video_stream'].get('url')
                                if not self.video_stream_url and 'port' in json_data['video_stream']:
                                    # 如果没有提供完整URL，则构建一个
                                    video_port = json_data['video_stream']['port']
                                    self.video_stream_url = f"http://{self.device_ip}:{video_port}/video"

                        self.last_update = time.time()
                    except json.JSONDecodeError:
                        print(f"接收到无效的JSON数据: {data[:100]}...")
                except Exception as e:
                    print(f"接收数据时出错: {e}")
        except Exception as e:
            print(f"无法绑定到端口 {self.port}: {e}")
        finally:
            sock.close()

# HTML模板
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CommaAssist 数据监视器</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 20px;
            background-color: #f5f5f5;
            color: #333;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        .header {
            text-align: center;
            margin-bottom: 20px;
        }
        .status {
            text-align: center;
            padding: 10px;
            margin-bottom: 20px;
            border-radius: 5px;
        }
        .waiting {
            background-color: #fff3cd;
            color: #856404;
        }
        .connected {
            background-color: #d4edda;
            color: #155724;
        }
        .expired {
            background-color: #f8d7da;
            color: #721c24;
        }
        .card {
            background-color: white;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
            margin-bottom: 20px;
        }
        .card-header {
            font-weight: bold;
            font-size: 18px;
            border-bottom: 1px solid #eee;
            padding: 15px;
            background-color: #f8f9fa;
        }
        .card-body {
            padding: 15px;
        }
        .row {
            margin-bottom: 10px;
        }
        .col-6 strong {
            color: #666;
        }
        .badge {
            font-size: 100%;
        }
        .bg-primary {
            background-color: #007bff;
        }
        .bg-success {
            background-color: #28a745;
        }
        .bg-danger {
            background-color: #dc3545;
        }
        .bg-warning {
            background-color: #ffc107;
            color: #212529;
        }
        .bg-info {
            background-color: #17a2b8;
        }
        .nav-tabs {
            margin-bottom: 20px;
        }
        .tab-content {
            padding-top: 20px;
        }
        .map-container {
            height: 300px;
            width: 100%;
            background-color: #eee;
            margin-top: 10px;
            border-radius: 5px;
        }
        .controls {
            margin-bottom: 20px;
        }
        .auto-refresh {
            display: inline-block;
            margin-right: 15px;
        }
        pre {
            background-color: #f5f5f5;
            padding: 15px;
            border-radius: 5px;
            white-space: pre-wrap;
            word-break: break-all;
        }
        .video-container {
            width: 100%;
            height: 0;
            padding-bottom: 56.25%; /* 16:9比例 */
            position: relative;
            background-color: #000;
            border-radius: 8px;
            overflow: hidden;
            margin-bottom: 20px;
        }
        .video-container iframe {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            border: none;
        }
        .video-placeholder {
            display: flex;
            justify-content: center;
            align-items: center;
            width: 100%;
            height: 100%;
            position: absolute;
            color: white;
            font-size: 18px;
            background-color: #333;
        }
        .video-controls {
            margin-top: 10px;
            margin-bottom: 20px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>CommaAssist 数据监视器</h1>
        </div>

        <div id="status-container" class="status waiting">
            等待来自comma3的数据...
        </div>

        <div class="controls">
            <label class="auto-refresh">
                <input type="checkbox" id="auto-refresh" checked> 自动刷新 (1秒)
            </label>
            <button class="btn btn-primary float-end" onclick="fetchData()">刷新数据</button>
            <div style="clear: both;"></div>
        </div>

        <ul class="nav nav-tabs" id="myTab" role="tablist">
            <li class="nav-item" role="presentation">
                <button class="nav-link active" id="car-tab" data-bs-toggle="tab" data-bs-target="#car-tab-pane" type="button" role="tab">
                    车辆信息
                </button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="device-tab" data-bs-toggle="tab" data-bs-target="#device-tab-pane" type="button" role="tab">
                    设备信息
                </button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="location-tab" data-bs-toggle="tab" data-bs-target="#location-tab-pane" type="button" role="tab">
                    位置信息
                </button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="video-tab" data-bs-toggle="tab" data-bs-target="#video-tab-pane" type="button" role="tab">
                    视频流
                </button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="json-tab" data-bs-toggle="tab" data-bs-target="#json-tab-pane" type="button" role="tab">
                    原始数据
                </button>
            </li>
        </ul>

        <div class="tab-content" id="myTabContent">
            <!-- 车辆信息标签页 -->
            <div class="tab-pane fade show active" id="car-tab-pane" role="tabpanel" aria-labelledby="car-tab" tabindex="0">
                <div class="row">
                    <div class="col-md-6">
                        <div class="card">
                            <div class="card-header">基本车辆信息</div>
                            <div class="card-body">
                                <div id="vehicle-status-container">
                                    <div class="alert alert-warning">等待车辆数据...</div>
                                </div>
                            </div>
                        </div>

                        <div class="card">
                            <div class="card-header">方向盘与控制系统</div>
                            <div class="card-body">
                                <div id="steering-system-container">
                                    <div class="alert alert-warning">等待车辆数据...</div>
                                </div>
                            </div>
                        </div>

                        <div class="card">
                            <div class="card-header">详细车辆信息</div>
                            <div class="card-body">
                                <div id="detailed-vehicle-info-container">
                                    <div class="alert alert-warning">等待车辆数据...</div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="col-md-6">
                        <div class="card">
                            <div class="card-header">踏板与制动系统</div>
                            <div class="card-body">
                                <div id="pedal-status-container">
                                    <div class="alert alert-warning">等待车辆数据...</div>
                                </div>
                            </div>
                        </div>

                        <div class="card">
                            <div class="card-header">车门与信号灯</div>
                            <div class="card-body">
                                <div id="door-lights-container">
                                    <div class="alert alert-warning">等待车辆数据...</div>
                                </div>
                            </div>
                        </div>

                        <div class="card">
                            <div class="card-header">巡航控制</div>
                            <div class="card-body">
                                <div id="cruise-info-container">
                                    <div class="alert alert-warning">等待车辆数据...</div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 设备信息标签页 -->
            <div class="tab-pane fade" id="device-tab-pane" role="tabpanel" aria-labelledby="device-tab" tabindex="0">
                <div class="card">
                    <div class="card-header">设备状态</div>
                    <div class="card-body">
                        <div id="device-info-container">
                            <div class="alert alert-warning">等待设备数据...</div>
                        </div>
                    </div>
                </div>

                <div class="card">
                    <div class="card-header">系统资源</div>
                    <div class="card-body">
                        <div id="system-resources-container">
                            <div class="alert alert-warning">等待设备数据...</div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 位置信息标签页 -->
            <div class="tab-pane fade" id="location-tab-pane" role="tabpanel" aria-labelledby="location-tab" tabindex="0">
                <div class="row">
                    <div class="col-md-6">
                        <div class="card">
                            <div class="card-header">GPS位置</div>
                            <div class="card-body">
                                <div id="gps-info-container">
                                    <div class="alert alert-warning">等待GPS数据...</div>
                                </div>
                            </div>
                        </div>

                        <div class="card">
                            <div class="card-header">导航信息</div>
                            <div class="card-body">
                                <div id="navigation-container">
                                    <div class="alert alert-warning">等待导航数据...</div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="col-md-6">
                        <div class="card">
                            <div class="card-header">地图</div>
                            <div class="card-body">
                                <div id="map" class="map-container"></div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 视频流标签页 -->
            <div class="tab-pane fade" id="video-tab-pane" role="tabpanel" aria-labelledby="video-tab" tabindex="0">
                <div class="card">
                    <div class="card-header">实时视频流</div>
                    <div class="card-body">
                        <div id="video-status" class="alert alert-info">正在检查视频流可用性...</div>
                        <div class="video-container" id="video-frame-container">
                            <div class="video-placeholder" id="video-placeholder">
                                等待视频流连接...
                            </div>
                            <iframe id="video-frame" style="display:none;" allowfullscreen></iframe>
                        </div>
                        <div class="video-controls">
                            <button class="btn btn-primary" id="refresh-video-btn">刷新视频流</button>
                            <button class="btn btn-warning" id="fullscreen-btn">全屏显示</button>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 原始数据标签页 -->
            <div class="tab-pane fade" id="json-tab-pane" role="tabpanel" aria-labelledby="json-tab" tabindex="0">
                <div class="card">
                    <div class="card-header">原始JSON数据</div>
                    <div class="card-body">
                        <pre id="raw-data">等待数据...</pre>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        let map, marker;
        let lastValidLatLng = null;
        let activeTab = 'car';  // 默认显示车辆信息标签
        let videoStreamUrl = null;

        // 根据车辆状态自动切换标签
        function autoSwitchTabs(isCarActive) {
            // 如果车辆启动，切换到车辆标签；否则切换到设备标签
            if (isCarActive && activeTab !== 'car') {
                document.getElementById('car-tab').click();
                activeTab = 'car';
            } else if (!isCarActive && activeTab === 'car') {
                document.getElementById('device-tab').click();
                activeTab = 'device';
            }
        }

        function initMap() {
            if (typeof google !== 'undefined') {
                const defaultPos = {lat: 39.9042, lng: 116.4074}; // 默认位置：北京
                map = new google.maps.Map(document.getElementById('map'), {
                    zoom: 16,
                    center: defaultPos,
                    mapTypeId: 'roadmap'
                });

                marker = new google.maps.Marker({
                    position: defaultPos,
                    map: map,
                    title: 'Comma3位置'
                });
            }
        }

        function updateMap(lat, lng, bearing) {
            if (typeof google !== 'undefined' && map && marker) {
                const latLng = new google.maps.LatLng(lat, lng);

                // 更新标记位置
                marker.setPosition(latLng);

                // 根据方向旋转标记
                if (bearing !== undefined) {
                    // 如果我们有自定义的带方向的标记图标，可以在这里设置
                }

                // 平滑移动地图中心
                map.panTo(latLng);

                lastValidLatLng = {lat, lng};
            }
        }

        function formatDataRow(label, value, badgeClass = null) {
            let valueHtml = value;
            if (badgeClass) {
                valueHtml = `<span class="badge ${badgeClass}">${value}</span>`;
            }

            return `
                <div class="row">
                    <div class="col-6">
                        <strong>${label}</strong>
                    </div>
                    <div class="col-6">
                        ${valueHtml}
                    </div>
                </div>
            `;
        }

        function updateVehicleStatus(car) {
            const speed = car.speed || 0;
            const isCarMoving = speed > 1.0;

            let html = '';

            html += formatDataRow('运行状态', isCarMoving ? '行驶中' : '静止', isCarMoving ? 'bg-success' : 'bg-secondary');
            html += formatDataRow('当前速度', `${speed.toFixed(1)} km/h`, 'bg-primary');
            html += formatDataRow('档位', car.gear_shifter || 'Unknown');

            document.getElementById('vehicle-status-container').innerHTML = html;

            return isCarMoving;
        }

        function updateSteeringSystem(car) {
            let html = '';

            html += formatDataRow('方向盘角度', `${(car.steering_angle || 0).toFixed(1)}°`);
            html += formatDataRow('转向力矩', `${(car.steering_torque || 0).toFixed(1)} Nm`);

            let blinkerStatus = '';
            if (car.left_blinker && car.right_blinker) blinkerStatus = '双闪';
            else if (car.left_blinker) blinkerStatus = '左转';
            else if (car.right_blinker) blinkerStatus = '右转';
            else blinkerStatus = '关闭';

            html += formatDataRow('转向灯', blinkerStatus);

            document.getElementById('steering-system-container').innerHTML = html;
        }

        function updatePedalStatus(car) {
            let html = '';

            const brakeStatus = car.brake_pressed ? '已踩下' : '释放';
            const gasStatus = car.gas_pressed ? '已踩下' : '释放';

            html += formatDataRow('制动踏板', brakeStatus, car.brake_pressed ? 'bg-danger' : 'bg-secondary');
            html += formatDataRow('油门踏板', gasStatus, car.gas_pressed ? 'bg-success' : 'bg-secondary');

            document.getElementById('pedal-status-container').innerHTML = html;
        }

        function updateDoorLights(car) {
            let html = '';

            const doorStatus = car.door_open ? '打开' : '关闭';
            html += formatDataRow('车门状态', doorStatus, car.door_open ? 'bg-danger' : 'bg-success');

            html += formatDataRow('左转向灯', car.left_blinker ? '开启' : '关闭', car.left_blinker ? 'bg-warning' : 'bg-secondary');
            html += formatDataRow('右转向灯', car.right_blinker ? '开启' : '关闭', car.right_blinker ? 'bg-warning' : 'bg-secondary');

            document.getElementById('door-lights-container').innerHTML = html;
        }

        function updateCruiseInfo(car) {
            let html = '';

            html += formatDataRow('巡航速度', `${(car.cruise_speed || 0).toFixed(1)} km/h`, 'bg-info');

            document.getElementById('cruise-info-container').innerHTML = html;
        }

        function updateDeviceInfo(device) {
            let html = '';

            html += formatDataRow('设备IP', device.ip || 'Unknown');

            const battery = device.battery || {};
            const batPercent = battery.percent || 0;
            let batClass = 'bg-danger';
            if (batPercent > 50) batClass = 'bg-success';
            else if (batPercent > 20) batClass = 'bg-warning';

            html += formatDataRow('电池电量', `${batPercent}%`, batClass);
            html += formatDataRow('电池电压', `${(battery.voltage || 0).toFixed(2)} V`);
            html += formatDataRow('电池电流', `${(battery.status || 0).toFixed(2)} A`);

            document.getElementById('device-info-container').innerHTML = html;
        }

        function updateSystemResources(device) {
            let html = '';

            let memClass = 'bg-success';
            if (device.mem_usage > 80) memClass = 'bg-danger';
            else if (device.mem_usage > 60) memClass = 'bg-warning';

            html += formatDataRow('内存使用', `${(device.mem_usage || 0).toFixed(1)}%`, memClass);
            html += formatDataRow('CPU温度', `${(device.cpu_temp || 0).toFixed(1)}°C`);
            html += formatDataRow('存储空间', `剩余 ${(device.free_space || 0).toFixed(1)}%`);

            document.getElementById('system-resources-container').innerHTML = html;
        }

        function updateGpsInfo(location) {
            if (!location.gps_valid) {
                document.getElementById('gps-info-container').innerHTML =
                    '<div class="alert alert-warning">GPS信号无效或未获取</div>';
                return;
            }

            let html = '';

            html += formatDataRow('纬度', location.latitude.toFixed(6));
            html += formatDataRow('经度', location.longitude.toFixed(6));
            html += formatDataRow('方向', `${location.bearing.toFixed(1)}°`);
            html += formatDataRow('海拔', `${location.altitude.toFixed(1)} m`);
            html += formatDataRow('GPS精度', `${location.accuracy.toFixed(1)} m`);
            html += formatDataRow('GPS速度', `${location.speed.toFixed(1)} km/h`, 'bg-primary');

            document.getElementById('gps-info-container').innerHTML = html;

            // 更新地图
            updateMap(location.latitude, location.longitude, location.bearing);
        }

        function updateNavigation(nav) {
            if (!nav || Object.keys(nav).length === 0) {
                document.getElementById('navigation-container').innerHTML =
                    '<div class="alert alert-info">没有活动的导航</div>';
                return;
            }

            let html = '';

            // 格式化距离显示
            const distRemaining = nav.distance_remaining || 0;
            let distText = '';
            if (distRemaining > 1000) {
                distText = `${(distRemaining / 1000).toFixed(1)} km`;
            } else {
                distText = `${Math.round(distRemaining)} m`;
            }

            // 格式化时间显示
            const timeRemaining = nav.time_remaining || 0;
            const minutes = Math.floor(timeRemaining / 60);
            const seconds = timeRemaining % 60;

            html += formatDataRow('剩余距离', distText, 'bg-info');
            html += formatDataRow('剩余时间', `${minutes}分${seconds}秒`, 'bg-info');
            html += formatDataRow('道路限速', `${(nav.speed_limit || 0).toFixed(1)} km/h`, 'bg-danger');

            if (nav.maneuver_distance > 0) {
                html += formatDataRow('下一动作', nav.maneuver_text, 'bg-warning');
                html += formatDataRow('动作距离', `${nav.maneuver_distance} m`);
            }

            document.getElementById('navigation-container').innerHTML = html;
        }

        function updateDetailedVehicleInfo(carInfo) {
            if (!carInfo || !carInfo.details) {
                document.getElementById('detailed-vehicle-info-container').innerHTML =
                    '<div class="alert alert-info">没有详细车辆信息</div>';
                return;
            }

            let html = '';

            // 基本信息
            if (carInfo.basic) {
                html += '<h5>基本信息</h5>';
                html += '<div class="mb-3">';
                html += formatDataRow('车型', carInfo.basic.car_model);
                html += formatDataRow('车辆指纹', carInfo.basic.fingerprint);
                html += formatDataRow('车重', carInfo.basic.weight);
                html += formatDataRow('轴距', carInfo.basic.wheelbase);
                html += formatDataRow('转向比', carInfo.basic.steering_ratio);
                html += '</div>';
            }

            // 巡航信息
            if (carInfo.details.cruise) {
                const cruise = carInfo.details.cruise;
                html += '<h5>巡航控制</h5>';
                html += '<div class="mb-3">';
                html += formatDataRow('巡航状态', cruise.enabled ? '开启' : '关闭', cruise.enabled ? 'bg-success' : 'bg-secondary');
                html += formatDataRow('自适应巡航', cruise.available ? '可用' : '不可用');
                html += formatDataRow('设定速度', `${(cruise.speed || 0).toFixed(1)} km/h`, 'bg-info');
                if (cruise.gap !== undefined) {
                    html += formatDataRow('跟车距离', cruise.gap);
                }
                html += '</div>';
            }

            // 车轮速度
            if (carInfo.details.wheel_speeds) {
                const ws = carInfo.details.wheel_speeds;
                html += '<h5>车轮速度</h5>';
                html += '<div class="mb-3">';
                html += formatDataRow('左前', `${(ws.fl || 0).toFixed(1)} km/h`);
                html += formatDataRow('右前', `${(ws.fr || 0).toFixed(1)} km/h`);
                html += formatDataRow('左后', `${(ws.rl || 0).toFixed(1)} km/h`);
                html += formatDataRow('右后', `${(ws.rr || 0).toFixed(1)} km/h`);
                html += '</div>';
            }

            // 方向盘信息
            if (carInfo.details.steering) {
                const steering = carInfo.details.steering;
                html += '<h5>方向盘系统</h5>';
                html += '<div class="mb-3">';
                html += formatDataRow('方向盘角度', `${(steering.angle || 0).toFixed(1)}°`);
                html += formatDataRow('方向盘力矩', `${(steering.torque || 0).toFixed(1)} Nm`);
                if (steering.rate !== undefined) {
                    html += formatDataRow('转向速率', `${steering.rate.toFixed(1)}°/s`);
                }
                html += '</div>';
            }

            // 安全系统
            if (carInfo.details.safety_systems && Object.keys(carInfo.details.safety_systems).length > 0) {
                const safety = carInfo.details.safety_systems;
                html += '<h5>安全系统</h5>';
                html += '<div class="mb-3">';
                if (safety.esp_disabled !== undefined) {
                    html += formatDataRow('ESP状态', safety.esp_disabled ? '禁用' : '正常', safety.esp_disabled ? 'bg-warning' : 'bg-success');
                }
                if (safety.abs_active !== undefined) {
                    html += formatDataRow('ABS状态', safety.abs_active ? '激活' : '正常', safety.abs_active ? 'bg-warning' : 'bg-success');
                }
                if (safety.tcs_active !== undefined) {
                    html += formatDataRow('牵引力控制', safety.tcs_active ? '激活' : '正常', safety.tcs_active ? 'bg-warning' : 'bg-success');
                }
                if (safety.collision_warning !== undefined) {
                    html += formatDataRow('碰撞警告', safety.collision_warning ? '警告' : '正常', safety.collision_warning ? 'bg-danger' : 'bg-success');
                }
                html += '</div>';
            }

            // 车门信息
            if (carInfo.details.doors && Object.keys(carInfo.details.doors).length > 0) {
                const doors = carInfo.details.doors;
                html += '<h5>车门状态</h5>';
                html += '<div class="mb-3">';
                html += formatDataRow('驾驶员门', doors.driver ? '打开' : '关闭', doors.driver ? 'bg-danger' : 'bg-success');
                if (doors.passenger !== undefined) {
                    html += formatDataRow('乘客门', doors.passenger ? '打开' : '关闭', doors.passenger ? 'bg-danger' : 'bg-success');
                }
                if (doors.trunk !== undefined) {
                    html += formatDataRow('行李箱', doors.trunk ? '打开' : '关闭', doors.trunk ? 'bg-danger' : 'bg-success');
                }
                if (doors.hood !== undefined) {
                    html += formatDataRow('引擎盖', doors.hood ? '打开' : '关闭', doors.hood ? 'bg-danger' : 'bg-success');
                }
                if (carInfo.status && carInfo.status.seatbelt_unlatched !== undefined) {
                    html += formatDataRow('安全带', carInfo.status.seatbelt_unlatched ? '未系' : '已系', carInfo.status.seatbelt_unlatched ? 'bg-danger' : 'bg-success');
                }
                html += '</div>';
            }

            // 灯光状态
            if (carInfo.details.lights && Object.keys(carInfo.details.lights).length > 0) {
                const lights = carInfo.details.lights;
                html += '<h5>灯光状态</h5>';
                html += '<div class="mb-3">';
                html += formatDataRow('左转向灯', lights.left_blinker ? '开启' : '关闭', lights.left_blinker ? 'bg-warning' : 'bg-secondary');
                html += formatDataRow('右转向灯', lights.right_blinker ? '开启' : '关闭', lights.right_blinker ? 'bg-warning' : 'bg-secondary');
                if (lights.high_beam !== undefined) {
                    html += formatDataRow('远光灯', lights.high_beam ? '开启' : '关闭', lights.high_beam ? 'bg-info' : 'bg-secondary');
                }
                if (lights.low_beam !== undefined) {
                    html += formatDataRow('近光灯', lights.low_beam ? '开启' : '关闭', lights.low_beam ? 'bg-info' : 'bg-secondary');
                }
                html += '</div>';
            }

            // 盲点监测
            if (carInfo.details.blind_spot && Object.keys(carInfo.details.blind_spot).length > 0) {
                const bs = carInfo.details.blind_spot;
                html += '<h5>盲点监测</h5>';
                html += '<div class="mb-3">';
                if (bs.left !== undefined) {
                    html += formatDataRow('左侧', bs.left ? '检测到车辆' : '无车辆', bs.left ? 'bg-warning' : 'bg-success');
                }
                if (bs.right !== undefined) {
                    html += formatDataRow('右侧', bs.right ? '检测到车辆' : '无车辆', bs.right ? 'bg-warning' : 'bg-success');
                }
                html += '</div>';
            }

            // 其他信息
            if (carInfo.details.other && Object.keys(carInfo.details.other).length > 0) {
                const other = carInfo.details.other;
                html += '<h5>其他信息</h5>';
                html += '<div class="mb-3">';
                if (other.outside_temp !== undefined) {
                    html += formatDataRow('车外温度', `${other.outside_temp.toFixed(1)}°C`);
                }
                if (other.fuel_range !== undefined) {
                    html += formatDataRow('续航里程', `${other.fuel_range.toFixed(1)} km`);
                }
                if (other.odometer !== undefined) {
                    html += formatDataRow('里程表', `${other.odometer.toFixed(1)} km`);
                }
                if (other.fuel_consumption !== undefined) {
                    html += formatDataRow('油耗', `${other.fuel_consumption.toFixed(1)} L/100km`);
                }
                html += '</div>';
            }

            // 如果没有任何详细信息
            if (html === '') {
                html = '<div class="alert alert-info">没有获取到详细车辆信息</div>';
            }

            document.getElementById('detailed-vehicle-info-container').innerHTML = html;
        }

        function updateVideo(data) {
            // 检查视频流信息
            const videoStatus = document.getElementById('video-status');
            const videoFrame = document.getElementById('video-frame');
            const videoPlaceholder = document.getElementById('video-placeholder');

            if (data.video_stream && data.video_stream.available) {
                videoStatus.className = 'alert alert-success';
                videoStatus.textContent = '视频流可用，正在加载...';

                // 获取视频流URL
                const newVideoUrl = data.video_stream.url;

                if (newVideoUrl && newVideoUrl !== videoStreamUrl) {
                    videoStreamUrl = newVideoUrl;
                    console.log('更新视频流URL:', videoStreamUrl);

                    // 加载视频流
                    videoFrame.src = videoStreamUrl;
                    videoFrame.style.display = 'block';
                    videoPlaceholder.style.display = 'none';

                    videoFrame.onload = function() {
                        videoStatus.textContent = '视频流已连接';
                    };

                    videoFrame.onerror = function() {
                        videoStatus.className = 'alert alert-danger';
                        videoStatus.textContent = '视频流连接失败';
                        videoFrame.style.display = 'none';
                        videoPlaceholder.style.display = 'flex';
                        videoPlaceholder.textContent = '视频流连接失败，请检查网络或刷新';
                    };
                }
            } else {
                videoStatus.className = 'alert alert-warning';
                videoStatus.textContent = '视频流不可用';
                videoFrame.style.display = 'none';
                videoPlaceholder.style.display = 'flex';
                videoPlaceholder.textContent = '设备未提供视频流';
                videoStreamUrl = null;
            }
        }

        // 刷新视频按钮
        document.getElementById('refresh-video-btn').addEventListener('click', function() {
            if (videoStreamUrl) {
                const videoFrame = document.getElementById('video-frame');
                videoFrame.src = videoStreamUrl + '?t=' + new Date().getTime();
            }
        });

        // 全屏按钮
        document.getElementById('fullscreen-btn').addEventListener('click', function() {
            const videoFrame = document.getElementById('video-frame');
            if (videoFrame.requestFullscreen) {
                videoFrame.requestFullscreen();
            } else if (videoFrame.mozRequestFullScreen) {
                videoFrame.mozRequestFullScreen();
            } else if (videoFrame.webkitRequestFullscreen) {
                videoFrame.webkitRequestFullscreen();
            } else if (videoFrame.msRequestFullscreen) {
                videoFrame.msRequestFullscreen();
            }
        });

        // 更新所有UI
        function updateUI(data) {
            // 更新状态信息
            updateStatusInfo(data);

            // 更新各部分内容
            if (data.car) {
                const isCarMoving = updateVehicleStatus(data.car);
                updateSteeringSystem(data.car);
                updatePedalStatus(data.car);
                updateDoorLights(data.car);
                updateCruiseInfo(data);
                updateCarDetails(data);

                // 自动切换标签
                autoSwitchTabs(isCarMoving);
            }

            if (data.device) {
                updateDeviceInfo(data.device);
                updateSystemResources(data.device);
            }

            if (data.location) {
                updateGPSInfo(data.location);
                if (data.location.gps_valid && data.location.latitude && data.location.longitude) {
                    updateMap(data.location.latitude, data.location.longitude, data.location.bearing);
                }
            }

            if (data.navigation) {
                updateNavigation(data.navigation);
            }

            // 更新视频流
            updateVideo(data);

            // 更新原始数据
            document.getElementById('raw-data').textContent = JSON.stringify(data, null, 2);
        }

        function fetchData() {
            fetch('/api/data')
                .then(response => response.json())
                .then(data => {
                    if (data.status === 'ok') {
                        updateUI(data.data);
                    } else {
                        console.error('API错误:', data.message);
                    }
                })
                .catch(error => {
                    console.error('获取数据错误:', error);
                });
        }

        // 初始加载
        document.addEventListener('DOMContentLoaded', function() {
            fetchData();

            // 定时刷新
            const autoRefreshCheckbox = document.getElementById('auto-refresh');
            let refreshInterval = null;

            function setAutoRefresh() {
                if (autoRefreshCheckbox.checked) {
                    refreshInterval = setInterval(fetchData, 1000);
                } else if (refreshInterval) {
                    clearInterval(refreshInterval);
                    refreshInterval = null;
                }
            }

            autoRefreshCheckbox.addEventListener('change', setAutoRefresh);
            setAutoRefresh();

            // 地图初始化
            if (typeof google !== 'undefined') {
                initMap();
            } else {
                console.log('Google Maps API未加载');
            }
        });
    </script>

    <!-- 加载Google地图API (需要替换为您自己的API密钥) -->
    <script async defer
        src="https://maps.googleapis.com/maps/api/js?key=YOUR_API_KEY&callback=initMap">
    </script>
</body>
</html>
"""

def create_app(listener):
    app = Flask(__name__)

    @app.route('/')
    def index():
        """主页"""
        return render_template_string(HTML_TEMPLATE)

    @app.route('/api/data')
    def get_data():
        """提供JSON数据API"""
        return jsonify({
            'data': listener.data,
            'last_update': listener.last_update,
            'device_ip': listener.device_ip
        })

    return app

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='CommaAssist Web监视器')
    parser.add_argument('-p', '--port', type=int, default=8088, help='监听comma设备的UDP端口(默认: 8088)')
    parser.add_argument('-w', '--web-port', type=int, default=5000, help='Web服务器端口(默认: 5000)')
    parser.add_argument('--host', default='0.0.0.0', help='Web服务器监听地址(默认: 0.0.0.0)')
    args = parser.parse_args()

    # 创建监听器
    listener = CommaWebListener(port=args.port)

    # 启动接收线程
    receiver_thread = threading.Thread(target=listener.start_listening)
    receiver_thread.daemon = True
    receiver_thread.start()

    # 创建Flask应用
    app = create_app(listener)

    # 启动Web服务器
    try:
        print(f"Web服务已启动，请访问 http://{args.host}:{args.web_port}/")
        app.run(host=args.host, port=args.web_port)
    except KeyboardInterrupt:
        print("\n退出...")
    finally:
        listener.running = False
        receiver_thread.join(timeout=1.0)

if __name__ == "__main__":
    main()