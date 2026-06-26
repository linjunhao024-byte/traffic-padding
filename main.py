#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Traffic Padding Micro-Service v1.0.0
# 流量伪装微服务：全天候随机微量碎片填充，使上下行流量比例自然化

__version__ = "1.1.0"

import calendar
import hashlib
import hmac
import base64
import json
import os
import random
import signal
import struct
import sys
import syslog
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
import ssl
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

CONFIG_FILE = "/etc/traffic-padding/config.json"
USAGE_FILE = "/etc/traffic-padding/usage.json"
STATS_FILE = "/etc/traffic-padding/stats.json"
TRAFFIC_HISTORY_FILE = "/etc/traffic-padding/traffic_history.json"
QOS_STATS_FILE = "/etc/traffic-padding/qos_stats.json"
URL_POOL_REFRESH_INTERVAL = 86400
HTTP_TIMEOUT = 15
SLIDING_WINDOW_SIZE = 5
CONFIG_RELOAD_INTERVAL = 300
TG_CHECK_INTERVAL = 3600
TRAFFIC_RECORD_INTERVAL = 300  # 流量记录间隔（秒）

# URL 中文名称映射
URL_NAME_MAP = {
    "dldir1.qq.com": "腾讯",
    "dl.360safe.com": "360",
    "cdn.aliyundcdntest.com": "阿里云",
    "huaweicloud.obs": "华为云",
    "dldir1.163.com": "网易",
    "lf1-cdn-tos.bytegoofy.com": "字节跳动",
    "img.alicdn.com": "淘宝",
    "qiniu-web-assets.dcloud.net.cn": "七牛云",
    "cloud.tencent.com": "腾讯云",
    "cn.bing.com": "必应",
    "bing.com": "必应",
    "en.wikipedia.org": "维基百科",
    "wikipedia.org": "维基百科",
    "speed.cloudflare.com": "Cloudflare",
    "dl.google.com": "Google",
    "download.jetbrains.com": "JetBrains",
    "speed.hetzner.de": "Hetzner",
    "proof.ovh.net": "OVH",
    "speedtest.tele2.net": "Tele2",
}

COUNTER_MAX_32BIT = 0xFFFFFFFF
COUNTER_MAX_64BIT = 0xFFFFFFFFFFFFFFFF

USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
]

# 不验证 SSL 证书 — 仅用于流量填充下载（目的就是浪费带宽，验证身份无意义）
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# 安全 SSL 上下文 — 用于 QoS 探测、通知推送、URL 池 API 等需要验证身份的场景
SSL_CONTEXT_SAFE = ssl.create_default_context()

try:
    syslog.openlog("traffic-padding", syslog.LOG_PID, syslog.LOG_DAEMON)
    SYSLOG_AVAILABLE = True
except Exception:
    SYSLOG_AVAILABLE = False

_last_log_times: Dict[str, float] = {}
LOG_THROTTLE_SECONDS = 5


def log_message(level: str, message: str, throttle_key: str = None):
    if throttle_key:
        now = time.time()
        if now - _last_log_times.get(throttle_key, 0) < LOG_THROTTLE_SECONDS:
            return
        _last_log_times[throttle_key] = now
    print(f"[{level}] {message}")
    if SYSLOG_AVAILABLE:
        priority = {"INFO": syslog.LOG_INFO, "WARN": syslog.LOG_WARNING, "ERROR": syslog.LOG_ERR}.get(level, syslog.LOG_INFO)
        syslog.syslog(priority, message)


def atomic_write_json(filepath: str, data) -> bool:
    """原子写入 JSON 文件（先写临时文件再 rename，防止崩溃导致文件损坏）"""
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        tmp_path = filepath + ".tmp"
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, filepath)  # 原子操作
        return True
    except (IOError, OSError) as e:
        log_message("ERROR", f"写入 {filepath} 失败: {e}")
        # 清理可能残留的临时文件
        try:
            os.remove(filepath + ".tmp")
        except OSError:
            pass
        return False


def detect_system_counter_bits() -> int:
    return struct.calcsize("P") * 8


def read_net_dev(interface: str) -> Tuple[Optional[int], Optional[int]]:
    """读取 /proc/net/dev 中指定网卡的 RX/TX 字节数，失败返回 (None, None)"""
    try:
        with open('/proc/net/dev', 'r') as f:
            for line in f:
                if ':' not in line:
                    continue
                name, data = line.split(':', 1)
                if name.strip() == interface:
                    fields = data.split()
                    return int(fields[0]), int(fields[8])
    except (IOError, ValueError, IndexError):
        pass
    return None, None


def calculate_counter_delta(prev: int, curr: int, max_val: int) -> int:
    if curr >= prev:
        return curr - prev
    # 计数器溢出处理
    overflow_delta = (max_val - prev) + curr + 1
    log_message("WARN", f"计数器溢出: prev={prev}, curr={curr}, delta={overflow_delta}")
    return overflow_delta


# ============================================================================
# 带宽监控（独立线程，1秒采样，1分钟写CSV，实时告警）
# ============================================================================

class BandwidthMonitor:
    """后台线程：每秒采样网卡带宽，每分钟写CSV，触发告警"""

    def __init__(self, interface: str, config, notifier_callback=None):
        self.interface = interface
        self.config = config
        self.notifier_callback = notifier_callback  # (title, msg) → 发送通知
        self.running = False
        self.thread = None

        # 采样状态
        self.prev_rx = 0
        self.prev_tx = 0
        self.prev_ts = 0.0

        # 分钟累积
        self.min_rx_peak = 0.0
        self.min_tx_peak = 0.0
        self.min_rx_sum = 0.0
        self.min_tx_sum = 0.0
        self.sample_count = 0
        self.current_minute = ""

        # 线程安全的缓存（供其他组件读取）
        self._lock = threading.Lock()
        self._latest_rx_speed = 0.0
        self._latest_tx_speed = 0.0
        self._latest_total_speed = 0.0

        # 今日统计
        self._today_rx_peak = 0.0
        self._today_tx_peak = 0.0
        self._today_total_peak = 0.0
        self._today_rx_peak_time = ""
        self._today_tx_peak_time = ""
        self._today_total_peak_time = ""
        self._today_rx_sum = 0.0
        self._today_tx_sum = 0.0
        self._today_total_sum = 0.0
        self._today_samples = 0
        self._today_date = ""
        self._today_alert_count = 0

        # 告警状态
        self._alert_state = "normal"  # normal / alert
        self._alert_last_ts = 0
        self._alert_start_ts = 0  # 告警开始时间
        self._alert_peak_mbps = 0  # 告警期间峰值
        self._alert_history: List[Dict] = []  # 告警历史 [{start, end, duration_sec, peak_mbps}]

        # 流量累计（字节，用于报告）
        self._today_rx_bytes = 0
        self._today_tx_bytes = 0

    def start(self):
        """启动后台采样线程"""
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True, name="BandwidthMonitor")
        self.thread.start()
        log_message("INFO", f"带宽监控启动: 接口={self.interface}")

    def stop(self):
        """停止采样线程"""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)

    def _run(self):
        """线程主循环"""
        # 初始化基准
        rx, tx = read_net_dev(self.interface)
        if rx is None:
            log_message("WARN", f"带宽监控: 网卡 {self.interface} 不存在，停止监控")
            self.running = False
            return
        self.prev_rx = rx
        self.prev_tx = tx
        self.prev_ts = time.time()
        self.current_minute = datetime.now().strftime("%Y%m%d%H%M")
        self._today_date = datetime.now().strftime("%Y-%m-%d")

        while self.running:
            time.sleep(1)
            if not self.running:
                break
            self._sample()

    def _sample(self):
        """单次采样"""
        rx, tx = read_net_dev(self.interface)
        if rx is None:
            return
        now = time.time()
        time_delta = max(0.1, now - self.prev_ts)

        # 计算 Mbps（处理计数器溢出）
        counter_max = COUNTER_MAX_64BIT if detect_system_counter_bits() == 64 else COUNTER_MAX_32BIT
        rx_delta = calculate_counter_delta(self.prev_rx, rx, counter_max)
        tx_delta = calculate_counter_delta(self.prev_tx, tx, counter_max)
        rx_speed = max(0.0, rx_delta * 8 / 1_000_000 / time_delta)
        tx_speed = max(0.0, tx_delta * 8 / 1_000_000 / time_delta)
        total_speed = rx_speed + tx_speed

        self.prev_rx = rx
        self.prev_tx = tx
        self.prev_ts = now

        # 所有共享状态修改都在锁内
        csv_to_write = None
        csv_data = None
        with self._lock:
            self._latest_rx_speed = rx_speed
            self._latest_tx_speed = tx_speed
            self._latest_total_speed = total_speed

            # 累计流量字节
            self._today_rx_bytes += rx_delta
            self._today_tx_bytes += tx_delta

            # 分钟累积
            if rx_speed > self.min_rx_peak:
                self.min_rx_peak = rx_speed
            if tx_speed > self.min_tx_peak:
                self.min_tx_peak = tx_speed
            self.min_rx_sum += rx_speed
            self.min_tx_sum += tx_speed
            self.sample_count += 1

            # 今日统计
            now_str = datetime.now().strftime("%H:%M")
            if rx_speed > self._today_rx_peak:
                self._today_rx_peak = rx_speed
                self._today_rx_peak_time = now_str
            if tx_speed > self._today_tx_peak:
                self._today_tx_peak = tx_speed
                self._today_tx_peak_time = now_str
            if total_speed > self._today_total_peak:
                self._today_total_peak = total_speed
                self._today_total_peak_time = now_str
            self._today_rx_sum += rx_speed
            self._today_tx_sum += tx_speed
            self._today_total_sum += total_speed
            self._today_samples += 1

            # 日期切换（在锁内完成，先写旧日期CSV再重置）
            today = datetime.now().strftime("%Y-%m-%d")
            if today != self._today_date:
                # 准备写旧日期的最后一分钟CSV
                if self.sample_count > 0:
                    csv_to_write = self._today_date
                self._today_date = today
                self._today_rx_peak = 0.0
                self._today_tx_peak = 0.0
                self._today_total_peak = 0.0
                self._today_rx_peak_time = ""
                self._today_tx_peak_time = ""
                self._today_total_peak_time = ""
                self._today_rx_sum = 0.0
                self._today_tx_sum = 0.0
                self._today_total_sum = 0.0
                self._today_samples = 0
                self._today_alert_count = 0
                self._alert_history = []
                self._today_rx_bytes = 0
                self._today_tx_bytes = 0

            # 分钟切换 → 快照数据并在锁内重置
            now_minute = datetime.now().strftime("%Y%m%d%H%M")
            if now_minute != self.current_minute and self.sample_count > 0:
                if csv_to_write is None:
                    csv_to_write = self._today_date
                self.current_minute = now_minute
                # 快照并重置分钟累积（在锁内完成）
                csv_snapshot = {
                    'rx_peak': self.min_rx_peak,
                    'tx_peak': self.min_tx_peak,
                    'rx_sum': self.min_rx_sum,
                    'tx_sum': self.min_tx_sum,
                    'count': self.sample_count,
                }
                self.min_rx_peak = 0.0
                self.min_tx_peak = 0.0
                self.min_rx_sum = 0.0
                self.min_tx_sum = 0.0
                self.sample_count = 0
                csv_data = csv_snapshot

        # 锁外执行CSV写入（IO操作）
        if csv_to_write and csv_data:
            self._write_csv(csv_to_write, csv_data)

        # 告警检查
        self._check_alert(rx_speed, tx_speed, total_speed)

    def _write_csv(self, date_str: str = None, snapshot: Dict = None):
        """写入一分钟的CSV记录（使用锁内快照的数据）"""
        try:
            data = snapshot or {}
            count = data.get('count', 0)
            if count == 0:
                return

            csv_dir = self.config.get('csv_log_dir', '/etc/traffic-padding/logs')
            os.makedirs(csv_dir, exist_ok=True)
            date_tag = (date_str or self._today_date).replace('-', '')
            csv_file = os.path.join(csv_dir, f"bandwidth_{date_tag}.csv")

            if not os.path.exists(csv_file):
                with open(csv_file, 'w') as f:
                    f.write("timestamp,rx_peak_mbps,tx_peak_mbps,total_peak_mbps,rx_avg_mbps,tx_avg_mbps,total_avg_mbps,samples\n")

            rx_avg = data['rx_sum'] / count
            tx_avg = data['tx_sum'] / count
            total_peak = data['rx_peak'] + data['tx_peak']
            total_avg = rx_avg + tx_avg
            ts = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")

            with open(csv_file, 'a') as f:
                f.write(f"{ts},{data['rx_peak']:.4f},{data['tx_peak']:.4f},{total_peak:.4f},"
                        f"{rx_avg:.4f},{tx_avg:.4f},{total_avg:.4f},{count}\n")
        except (IOError, OSError) as e:
            log_message("ERROR", f"写入 CSV 失败: {e}")

    def get_latest_stats(self) -> Dict:
        """获取最新带宽数据（线程安全）"""
        with self._lock:
            return {
                'rx_speed': self._latest_rx_speed,
                'tx_speed': self._latest_tx_speed,
                'total_speed': self._latest_total_speed,
            }

    def get_today_stats(self) -> Dict:
        """获取今日带宽统计（线程安全快照）"""
        with self._lock:
            samples = max(1, self._today_samples)
            return {
                'date': self._today_date,
                'rx_peak': self._today_rx_peak,
                'rx_peak_time': self._today_rx_peak_time,
                'tx_peak': self._today_tx_peak,
                'tx_peak_time': self._today_tx_peak_time,
                'total_peak': self._today_total_peak,
                'total_peak_time': self._today_total_peak_time,
                'rx_avg': self._today_rx_sum / samples,
                'tx_avg': self._today_tx_sum / samples,
                'total_avg': self._today_total_sum / samples,
                'samples': self._today_samples,
                'alert_count': self._today_alert_count,
                'alert_history': list(self._alert_history),
                'rx_bytes': self._today_rx_bytes,
                'tx_bytes': self._today_tx_bytes,
            }

    def _check_alert(self, rx_speed: float, tx_speed: float, total_speed: float):
        """带宽告警检查"""
        if not self.config.get('alert_enabled', False):
            return

        threshold = self.config.get('alert_threshold_mbps', 50)
        cooldown = self.config.get('alert_cooldown', 180)
        recovery = self.config.get('alert_recovery', True)
        now = time.time()

        if total_speed > threshold:
            if self._alert_state == "alert" and (now - self._alert_last_ts) < cooldown:
                return
            self._alert_state = "alert"
            self._alert_last_ts = now
            if self._alert_start_ts == 0:
                self._alert_start_ts = now
                self._alert_peak_mbps = total_speed
            elif total_speed > self._alert_peak_mbps:
                self._alert_peak_mbps = total_speed
            with self._lock:
                self._today_alert_count += 1

            over_pct = ((total_speed - threshold) / threshold * 100) if threshold > 0 else 0
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_message("WARN", f"带宽告警: {total_speed:.1f}Mbps > {threshold}Mbps (超 {over_pct:.0f}%)")

            if self.notifier_callback:
                msg = (f"⚠️ 带宽告警通知\n\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"当前带宽: {total_speed:.1f} Mbps\n"
                       f"告警阈值: {threshold} Mbps\n"
                       f"超限幅度: {over_pct:.0f}%\n\n"
                       f"入站: {rx_speed:.1f} Mbps\n"
                       f"出站: {tx_speed:.1f} Mbps\n\n"
                       f"时间: {ts}")
                self.notifier_callback("⚠️ 带宽告警", msg, "bandwidth_alert")

        elif self._alert_state == "alert" and recovery:
            duration = int(now - self._alert_start_ts) if self._alert_start_ts > 0 else int(now - self._alert_last_ts)
            # 记录告警事件
            self._alert_history.append({
                'start': datetime.fromtimestamp(self._alert_start_ts).strftime('%H:%M') if self._alert_start_ts else '?',
                'end': datetime.now().strftime('%H:%M'),
                'duration_sec': duration,
                'peak_mbps': getattr(self, '_alert_peak_mbps', total_speed),
            })
            self._alert_state = "normal"
            self._alert_start_ts = 0
            self._alert_peak_mbps = 0
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            dur_min = duration // 60
            dur_sec = duration % 60
            log_message("INFO", f"带宽恢复: {total_speed:.1f}Mbps, 告警持续 {dur_min}分{dur_sec}秒")

            if self.notifier_callback:
                msg = (f"✅ 带宽恢复正常\n\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"当前带宽: {total_speed:.1f} Mbps\n"
                       f"告警阈值: {threshold} Mbps\n"
                       f"本次带宽超限时间: {dur_min}分{dur_sec}秒\n\n"
                       f"时间: {ts}")
                self.notifier_callback("✅ 带宽恢复", msg, "bandwidth_alert_recovery")

    def load_today_csv_stats(self) -> Optional[Dict]:
        """从今日CSV文件加载统计（用于报告，避免重复计算）"""
        csv_dir = self.config.get('csv_log_dir', '/etc/traffic-padding/logs')
        csv_file = os.path.join(csv_dir, f"bandwidth_{self._today_date.replace('-', '')}.csv")
        if not os.path.exists(csv_file):
            return None

        try:
            with open(csv_file, 'r') as f:
                lines = f.readlines()
            if len(lines) <= 1:
                return None

            rx_peaks = []
            tx_peaks = []
            rx_avgs = []
            tx_avgs = []
            for line in lines[1:]:
                parts = line.strip().split(',')
                if len(parts) >= 7:
                    rx_peaks.append(float(parts[1]))
                    tx_peaks.append(float(parts[2]))
                    rx_avgs.append(float(parts[4]))
                    tx_avgs.append(float(parts[5]))

            if not rx_peaks:
                return None

            return {
                'rx_peak': max(rx_peaks),
                'tx_peak': max(tx_peaks),
                'total_peak': max(p1 + p2 for p1, p2 in zip(rx_peaks, tx_peaks)),
                'rx_avg': sum(rx_avgs) / len(rx_avgs),
                'tx_avg': sum(tx_avgs) / len(tx_avgs),
                'total_avg': sum(r + t for r, t in zip(rx_avgs, tx_avgs)) / len(rx_avgs),
                'minutes': len(rx_peaks),
            }
        except (IOError, ValueError):
            return None

    def load_range_csv_stats(self, start_date: str, end_date: str) -> Optional[Dict]:
        """从CSV文件加载日期范围的统计"""
        csv_dir = self.config.get('csv_log_dir', '/etc/traffic-padding/logs')
        all_rx_peaks = []
        all_tx_peaks = []
        all_rx_avgs = []
        all_tx_avgs = []
        total_minutes = 0

        current = start_date
        while current <= end_date:
            csv_file = os.path.join(csv_dir, f"bandwidth_{current}.csv")
            if os.path.exists(csv_file):
                try:
                    with open(csv_file, 'r') as f:
                        for line in f.readlines()[1:]:
                            parts = line.strip().split(',')
                            if len(parts) >= 7:
                                all_rx_peaks.append(float(parts[1]))
                                all_tx_peaks.append(float(parts[2]))
                                all_rx_avgs.append(float(parts[4]))
                                all_tx_avgs.append(float(parts[5]))
                                total_minutes += 1
                except (IOError, ValueError):
                    pass
            # 日期 +1
            try:
                dt = datetime.strptime(current, "%Y%m%d") + timedelta(days=1)
                current = dt.strftime("%Y%m%d")
            except ValueError:
                break

        if not all_rx_peaks:
            return None

        return {
            'rx_peak': max(all_rx_peaks),
            'tx_peak': max(all_tx_peaks),
            'total_peak': max(p1 + p2 for p1, p2 in zip(all_rx_peaks, all_tx_peaks)),
            'rx_avg': sum(all_rx_avgs) / len(all_rx_avgs),
            'tx_avg': sum(all_tx_avgs) / len(all_tx_avgs),
            'total_avg': sum(r + t for r, t in zip(all_rx_avgs, all_tx_avgs)) / len(all_rx_avgs),
            'minutes': total_minutes,
        }

    def cleanup_csv(self, start_date: str, end_date: str):
        """清理日期范围内的CSV文件"""
        csv_dir = self.config.get('csv_log_dir', '/etc/traffic-padding/logs')
        deleted = 0
        current = start_date
        while current <= end_date:
            csv_file = os.path.join(csv_dir, f"bandwidth_{current}.csv")
            if os.path.exists(csv_file):
                try:
                    os.remove(csv_file)
                    deleted += 1
                except OSError:
                    pass
            try:
                dt = datetime.strptime(current, "%Y%m%d") + timedelta(days=1)
                current = dt.strftime("%Y%m%d")
            except ValueError:
                break
        if deleted > 0:
            log_message("INFO", f"清理带宽日志: 删除 {deleted} 个文件")



# ============================================================================
# AI 分析器（定期调用模型分析数据，缓存结果）
# ============================================================================

class AIAnalyzer:
    """后台线程：每小时调用 AI 模型分析带宽和填充数据"""

    API_PATH = "/chat/completions"

    def __init__(self, config, service_ref=None):
        self.config = config
        self.service_ref = service_ref  # 延迟绑定 TrafficPaddingService
        self.running = False
        self.thread = None
        self._lock = threading.Lock()
        self._last_analysis = ""  # 最近一次分析结果
        self._last_analysis_time = 0
        self._analysis_file = "/etc/traffic-padding/ai_analysis.json"
        self._analyzing = False
        self._load_cached()

    def _load_cached(self):
        """加载缓存的分析结果"""
        try:
            if os.path.exists(self._analysis_file):
                with open(self._analysis_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._last_analysis = data.get('analysis', '')
                    self._last_analysis_time = data.get('timestamp', 0)
        except (json.JSONDecodeError, IOError):
            pass

    def _save_cached(self):
        """保存分析结果"""
        atomic_write_json(self._analysis_file, {
            'analysis': self._last_analysis,
            'timestamp': self._last_analysis_time,
        })

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True, name="AIAnalyzer")
        self.thread.start()
        log_message("INFO", "AI 分析器启动")

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=95)

    def _run(self):
        """线程主循环：每 45-55 分钟随机调用一次"""
        # 启动后先等 3 分钟，让系统收集一些数据
        for _ in range(180):
            if not self.running:
                return
            time.sleep(1)

        while self.running:
            self._do_analysis()
            if not self.running:
                return
            # 随机等待 45-55 分钟（抖动，确保不会超过 1 小时限额）
            wait_seconds = random.randint(2700, 3300)
            for _ in range(wait_seconds):
                if not self.running:
                    return
                time.sleep(1)

    def _do_analysis(self):
        """执行一次分析（带日志）"""
        if not self.config.get('ai_enabled', True):
            return
        data = self._prepare_data()
        if not data:
            return
        log_message("INFO", "执行 AI 分析...")
        result = self._call_api(data)
        if result:
            with self._lock:
                self._last_analysis = result
                self._last_analysis_time = time.time()
            self._save_cached()
            log_message("INFO", f"AI 分析完成 ({len(result)} 字)")
        else:
            log_message("WARN", "AI 分析未返回结果")

    def _prepare_data(self) -> str:
        """准备发送给 AI 的数据摘要"""
        lines = []
        now = datetime.now()

        # 带宽监控数据
        if self.service_ref and self.service_ref.bandwidth_monitor:
            bw = self.service_ref.bandwidth_monitor.get_today_stats()
            lines.append(f"=== 带宽监控 ({now.strftime('%Y-%m-%d %H:%M')}) ===")
            lines.append(f"入站峰值: {bw['rx_peak']:.1f} Mbps ({bw['rx_peak_time']})")
            lines.append(f"出站峰值: {bw['tx_peak']:.1f} Mbps ({bw['tx_peak_time']})")
            lines.append(f"入站均值: {bw['rx_avg']:.1f} Mbps")
            lines.append(f"出站均值: {bw['tx_avg']:.1f} Mbps")
            lines.append(f"总流量: RX {bw['rx_bytes'] / (1024**3):.2f} GB / TX {bw['tx_bytes'] / (1024**3):.2f} GB")
            lines.append(f"告警次数: {bw['alert_count']}")

            # 最近 1 小时的 CSV 数据
            csv_dir = self.config.get('csv_log_dir', '/etc/traffic-padding/logs')
            csv_file = os.path.join(csv_dir, f"bandwidth_{now.strftime('%Y%m%d')}.csv")
            if os.path.exists(csv_file):
                try:
                    with open(csv_file, 'r') as f:
                        all_lines = f.readlines()
                    # 取最近 60 行数据（跳过表头）
                    data_lines = all_lines[1:]  # 跳过表头
                    recent = data_lines[-60:] if len(data_lines) > 60 else data_lines
                    if recent:
                        rx_vals = [float(l.split(',')[4]) for l in recent if ',' in l]
                        tx_vals = [float(l.split(',')[5]) for l in recent if ',' in l]
                        if rx_vals:
                            lines.append(f"近1小时入站: 均值{sum(rx_vals)/len(rx_vals):.1f}Mbps, 最大{max(rx_vals):.1f}Mbps, 最小{min(rx_vals):.1f}Mbps")
                            lines.append(f"近1小时出站: 均值{sum(tx_vals)/len(tx_vals):.1f}Mbps, 最大{max(tx_vals):.1f}Mbps, 最小{min(tx_vals):.1f}Mbps")
                except (IOError, ValueError):
                    pass

        # 流量填充数据
        if self.service_ref:
            stats = self.service_ref.downloader.get_stats()
            period = self.service_ref.get_period_stats('daily')
            lines.append(f"\n=== 流量填充 ===")
            lines.append(f"今日填充: {period['gb']:.3f} GB")
            lines.append(f"累计总量: {self.service_ref.total_downloaded_all_time / (1024**3):.3f} GB")
            lines.append(f"任务数: {stats['task_count']}, 成功: {stats['success_count']}, 失败: {stats['fail_count']}")
            avg_speed = self.service_ref.downloader.get_avg_speed()
            lines.append(f"平均下载速度: {avg_speed:.2f} MB/s")
            lines.append(f"配额使用: {self.service_ref.scheduler.daily_quota_used / (1024**3):.3f} / {self.config.get('max_daily_extra_gb', 10)} GB")

            # QoS 状态
            if self.service_ref.qos_probe.enabled:
                lines.append(f"QoS: {self.service_ref.qos_probe.get_status_str()}")

        return "\n".join(lines)

    def _call_api(self, data_summary: str) -> str:
        """调用 DeepSeek API"""
        api_key = self.config.get('ai_api_key', '')
        base_url = self.config.get('ai_base_url', '')
        model = self.config.get('ai_model', 'DeepSeek-R1-Distill-Llama-8B-F16')

        if not api_key or not base_url:
            return ""

        url = base_url.rstrip('/') + self.API_PATH

        prompt = (
            "你是服务器流量分析助手。根据以下数据：\n"
            "1.判断流量是否正常(正常/异常) 2.指出具体问题和可能原因 3.给出可操作建议\n"
            "用中文回答，150字以内，不要复述原始数据。\n\n"
            f"{data_summary}"
        )

        payload = json.dumps({
            "model": model,
            "temperature": 0.6,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "max_tokens": 400,
        }).encode('utf-8')

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        try:
            req = urllib.request.Request(url, data=payload, headers=headers)
            with urllib.request.urlopen(req, timeout=90, context=SSL_CONTEXT_SAFE) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                content = result['choices'][0]['message']['content']
                # 清理可能附带的 reasoning_content
                if 'reasoning_content' in str(result):
                    pass  # 非流式模式下 reasoning_content 不会出现在 message 里
                return content.strip()
        except Exception as e:
            log_message("WARN", f"AI 分析调用失败: {e}", throttle_key="ai_fail")
            return ""

    def get_latest(self) -> str:
        """获取最近一次分析结果"""
        with self._lock:
            return self._last_analysis

    def get_latest_time(self) -> float:
        with self._lock:
            return self._last_analysis_time

    def trigger_now(self):
        """手动触发一次分析（防止并发）"""
        if self._analyzing:
            log_message("INFO", "AI 分析正在进行，跳过重复触发")
            return
        self._analyzing = True
        def _run():
            try:
                if not self.config.get('ai_enabled', True):
                    log_message("WARN", "AI 分析已关闭，请先开启")
                    return
                data = self._prepare_data()
                if not data:
                    log_message("WARN", "无数据可分析")
                    return
                log_message("INFO", "手动触发 AI 分析...")
                result = self._call_api(data)
                if result:
                    with self._lock:
                        self._last_analysis = result
                        self._last_analysis_time = time.time()
                    self._save_cached()
                    log_message("INFO", f"手动 AI 分析完成 ({len(result)} 字)")
                else:
                    log_message("WARN", "手动 AI 分析失败")
            finally:
                self._analyzing = False
        threading.Thread(target=_run, daemon=True, name="AI-Manual").start()


# ============================================================================
# 配置管理（支持热重载）
# ============================================================================

class Config:
    def __init__(self, config_path: str = CONFIG_FILE):
        self.config_path = config_path
        self.data = self._load_config()
        self.last_mtime = self._get_file_mtime()
        self.last_check_time = time.time()

    def _get_file_mtime(self) -> float:
        try:
            return os.path.getmtime(self.config_path)
        except OSError:
            return 0

    def _load_config(self) -> Dict:
        default = {
            "interface": "eth0",
            "target_ratio": 3.0,
            "max_daily_extra_gb": 10.0,
            "min_task_bytes": 2097152,
            "max_task_bytes": 15728640,
            "jitter_base": 5,
            "jitter_range": 25,
            "enable_night_mode": True,
            "night_start_hour": 2,
            "night_end_hour": 5,
            "night_multiplier": 5.0,
            "peak_hours": [19, 20, 21, 22],
            "peak_multiplier": 0.6,
        }
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    default.update(json.load(f))
                    log_message("INFO", f"已加载配置: {self.config_path}")
        except (json.JSONDecodeError, IOError) as e:
            log_message("ERROR", f"配置读取失败: {e}")
        return default

    def check_and_reload(self):
        now = time.time()
        if now - self.last_check_time < CONFIG_RELOAD_INTERVAL:
            return
        self.last_check_time = now
        current_mtime = self._get_file_mtime()
        if current_mtime > self.last_mtime:
            log_message("INFO", "配置文件变更，重新加载...")
            self.data = self._load_config()
            self.last_mtime = current_mtime

    def get(self, key: str, default=None):
        return self.data.get(key, default)


# ============================================================================
# 网卡流量监控（溢出检测 + 滑动窗口）
# ============================================================================

class TrafficMonitor:
    def __init__(self, interface: str, bandwidth_monitor=None):
        self.interface = interface
        self.prev_rx_bytes = 0
        self.prev_tx_bytes = 0
        self.prev_time = 0
        self.baseline_valid = False
        self.system_bits = detect_system_counter_bits()
        self.counter_max = COUNTER_MAX_32BIT if self.system_bits == 32 else COUNTER_MAX_64BIT
        self.rx_window = deque(maxlen=SLIDING_WINDOW_SIZE)
        self.tx_window = deque(maxlen=SLIDING_WINDOW_SIZE)
        self._init_baseline()

    def _init_baseline(self):
        rx, tx = self._read_proc_net_dev()
        if rx is not None and tx is not None:
            self.prev_rx_bytes = rx
            self.prev_tx_bytes = tx
            self.prev_time = time.time()
            self.baseline_valid = True
            log_message("INFO", f"网卡 {self.interface} 基准: RX={rx}, TX={tx}")
        else:
            self.baseline_valid = False

    def _read_proc_net_dev(self) -> Tuple[Optional[int], Optional[int]]:
        return read_net_dev(self.interface)

    def get_traffic_stats(self) -> Dict:
        if not self.baseline_valid:
            self._init_baseline()
            if not self.baseline_valid:
                return {'rx_delta': 0, 'tx_delta': 0, 'ratio': 1.0, 'rx_rate': 0, 'tx_rate': 0,
                        'need_padding': False, 'avg_rx_delta': 0, 'avg_tx_delta': 0}

        current_rx, current_tx = self._read_proc_net_dev()
        current_time = time.time()

        if current_rx is None or current_tx is None:
            return {'rx_delta': 0, 'tx_delta': 0, 'ratio': 1.0, 'rx_rate': 0, 'tx_rate': 0,
                    'need_padding': False, 'avg_rx_delta': 0, 'avg_tx_delta': 0}

        time_delta = max(1, current_time - self.prev_time)
        rx_delta = calculate_counter_delta(self.prev_rx_bytes, current_rx, self.counter_max)
        tx_delta = calculate_counter_delta(self.prev_tx_bytes, current_tx, self.counter_max)

        self.prev_rx_bytes = current_rx
        self.prev_tx_bytes = current_tx
        self.prev_time = current_time

        self.rx_window.append(rx_delta)
        self.tx_window.append(tx_delta)

        avg_rx = sum(self.rx_window) / len(self.rx_window) if self.rx_window else 0
        avg_tx = sum(self.tx_window) / len(self.tx_window) if self.tx_window else 0
        ratio = avg_rx / avg_tx if avg_tx > 0 else (float('inf') if avg_rx > 0 else 1.0)

        return {
            'rx_delta': rx_delta, 'tx_delta': tx_delta,
            'avg_rx_delta': avg_rx, 'avg_tx_delta': avg_tx,
            'ratio': ratio,
            'rx_rate': rx_delta / time_delta, 'tx_rate': tx_delta / time_delta,
            'need_padding': False
        }


# ============================================================================
# QoS 探测（TCP ping 方式，国内外分离统计）
# ============================================================================

class QoSProbe:
    """跨境网络 QoS 探测器（TCP ping + 国内外分离）"""

    # 探测目标：(host, port, category)
    # category: 'domestic'=国内, 'international'=跨境
    DEFAULT_TARGETS = [
        ('www.baidu.com', 443, 'domestic'),
        ('cn.bing.com', 443, 'domestic'),
        ('www.qq.com', 443, 'domestic'),
        ('www.aliyun.com', 443, 'domestic'),
        ('dl.google.com', 443, 'international'),
        ('www.apple.com', 443, 'international'),
        ('www.cloudflare.com', 443, 'international'),
    ]

    # 阈值：国内和跨境分开
    THRESHOLDS = {
        'domestic': {'latency_warn': 80, 'latency_bad': 200, 'jitter_warn': 40, 'loss_warn': 10},
        'international': {'latency_warn': 150, 'latency_bad': 400, 'jitter_warn': 60, 'loss_warn': 15},
    }

    def __init__(self, config: Config):
        self.config = config
        self.enabled = config.get('qos_probe_enabled', True)
        self.history_file = QOS_STATS_FILE
        self.history: List[Dict] = []
        self._load_history()
        self.last_error = ""

    def _load_history(self):
        """加载历史探测数据"""
        try:
            if os.path.exists(self.history_file):
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    self.history = json.load(f)
                    # 只保留最近 7 天的数据
                    cutoff = time.time() - 7 * 86400
                    self.history = [h for h in self.history if h.get('timestamp', 0) > cutoff]
        except (json.JSONDecodeError, IOError):
            self.history = []

    def _save_history(self):
        """保存探测历史"""
        # 只保留最近 1000 条记录
        if len(self.history) > 1000:
            self.history = self.history[-1000:]
        atomic_write_json(self.history_file, self.history)

    def _tcp_ping(self, host: str, port: int, timeout: float = 5.0) -> Tuple[bool, float]:
        """TCP ping：返回 (成功, 延迟ms)"""
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        start = time.time()
        try:
            result = sock.connect_ex((host, port))
            latency = (time.time() - start) * 1000  # ms
            if result == 0:
                return True, latency
            else:
                return False, latency
        except (socket.timeout, socket.error):
            return False, (time.time() - start) * 1000
        finally:
            sock.close()

    def _probe_target(self, host: str, port: int, count: int = 3) -> Tuple[List[float], int]:
        """对单个目标进行多次 TCP ping，返回 (成功延迟列表, 总次数)"""
        latencies = []
        for i in range(count):
            ok, latency = self._tcp_ping(host, port)
            if ok:
                latencies.append(latency)
            if i < count - 1:
                time.sleep(random.uniform(0.3, 0.8))
        return latencies, count

    def _calc_stats(self, latencies: List[float], total: int) -> Dict:
        """计算统计指标"""
        if not latencies:
            return {'avg': 0, 'min': 0, 'max': 0, 'jitter': 0, 'loss': 100, 'count': 0}

        avg = sum(latencies) / len(latencies)
        jitter = (sum((l - avg) ** 2 for l in latencies) / len(latencies)) ** 0.5 if len(latencies) > 1 else 0
        loss = ((total - len(latencies)) / total) * 100 if total > 0 else 0

        return {
            'avg': avg,
            'min': min(latencies),
            'max': max(latencies),
            'jitter': jitter,
            'loss': loss,
            'count': len(latencies),
        }

    def _judge(self, stats: Dict, category: str) -> str:
        """根据阈值判断 QoS 等级"""
        th = self.THRESHOLDS.get(category, self.THRESHOLDS['domestic'])
        if stats['loss'] > th['loss_warn'] * 2 or stats['avg'] > th['latency_bad']:
            return 'bad'
        if stats['avg'] > th['latency_warn'] or stats['jitter'] > th['jitter_warn'] or stats['loss'] > th['loss_warn']:
            return 'warning'
        return 'good'

    def probe_all(self) -> Dict:
        """TCP ping 探测，国内外分离统计"""
        if not self.enabled:
            return {'enabled': False}

        targets = self.DEFAULT_TARGETS.copy()
        random.shuffle(targets)

        # 各选 2 个国内 + 2 个跨境
        domestic_targets = [(h, p) for h, p, c in targets if c == 'domestic'][:2]
        international_targets = [(h, p) for h, p, c in targets if c == 'international'][:2]

        domestic_latencies = []
        domestic_total = 0
        international_latencies = []
        international_total = 0
        failed_targets = []

        for host, port in domestic_targets:
            time.sleep(random.uniform(0.5, 1.0))
            latencies, total = self._probe_target(host, port, count=3)
            domestic_latencies.extend(latencies)
            domestic_total += total
            if not latencies:
                failed_targets.append(f"{host}:{port} (国内)")

        for host, port in international_targets:
            time.sleep(random.uniform(0.5, 1.0))
            latencies, total = self._probe_target(host, port, count=3)
            international_latencies.extend(latencies)
            international_total += total
            if not latencies:
                failed_targets.append(f"{host}:{port} (跨境)")

        # 分别计算统计
        domestic_stats = self._calc_stats(domestic_latencies, domestic_total)
        international_stats = self._calc_stats(international_latencies, international_total)

        # 分别判断等级
        domestic_level = self._judge(domestic_stats, 'domestic')
        international_level = self._judge(international_stats, 'international')

        # 综合等级：取最差的
        level_order = {'good': 0, 'warning': 1, 'bad': 2, 'error': 3}
        overall_level = domestic_level if level_order.get(domestic_level, 0) >= level_order.get(international_level, 0) else international_level

        # 综合原因
        qos_reasons = []
        if domestic_level != 'good':
            qos_reasons.append(f"国内: {domestic_stats['avg']:.0f}ms / 抖动{domestic_stats['jitter']:.0f}ms / 丢包{domestic_stats['loss']:.0f}%")
        if international_level != 'good':
            qos_reasons.append(f"跨境: {international_stats['avg']:.0f}ms / 抖动{international_stats['jitter']:.0f}ms / 丢包{international_stats['loss']:.0f}%")
        if not qos_reasons:
            qos_reasons.append("网络正常")

        # 全部失败
        if not domestic_latencies and not international_latencies:
            error_detail = "; ".join(failed_targets[:3]) if failed_targets else "无目标"
            self.last_error = f"所有目标不可达: {error_detail}"
            log_message("WARN", f"QoS 探测全部失败 — {self.last_error}")
            overall_level = 'error'

        # 记录日志
        log_message("INFO", f"QoS 探测: 国内={domestic_stats['avg']:.0f}ms 跨境={international_stats['avg']:.0f}ms 等级={overall_level}")

        # 保存结果
        result_data = {
            'timestamp': time.time(),
            'domestic': domestic_stats,
            'international': international_stats,
            'domestic_level': domestic_level,
            'international_level': international_level,
            'qos_level': overall_level,
            'qos_reasons': qos_reasons,
            'latency_avg': (domestic_stats['avg'] + international_stats['avg']) / 2 if domestic_stats['count'] and international_stats['count'] else max(domestic_stats['avg'], international_stats['avg']),
            'jitter': max(domestic_stats['jitter'], international_stats['jitter']),
            'loss': max(domestic_stats['loss'], international_stats['loss']),
        }

        self.history.append(result_data)
        self._save_history()

        return result_data

    def get_status_str(self) -> str:
        """获取 QoS 状态字符串（显示国内+跨境）"""
        if not self.enabled:
            return "未启用"

        if not self.history:
            return "未探测"

        latest = self.history[-1]
        level = latest.get('qos_level', 'unknown')

        if level == 'error':
            if self.last_error:
                short_err = self.last_error.split(";")[0] if ";" in self.last_error else self.last_error
                if len(short_err) > 40:
                    short_err = short_err[:37] + "..."
                return f"✗ 探测失败 ({short_err})"
            return "✗ 所有目标不可达"

        # 国内+跨境分开显示
        d = latest.get('domestic', {})
        i = latest.get('international', {})
        d_ms = d.get('avg', 0)
        i_ms = i.get('avg', 0)

        level_str = {'good': '✓ 正常', 'warning': '⚠ 轻度拥堵', 'bad': '✗ 严重拥堵'}.get(level, level)
        return f"{level_str} (国内{d_ms:.0f}ms / 跨境{i_ms:.0f}ms)"

    def get_trend(self) -> str:
        """获取趋势（最近 1 小时 vs 之前）"""
        if len(self.history) < 5:
            return "数据不足"

        now = time.time()
        hour_ago = now - 3600

        recent = [h for h in self.history if h.get('timestamp', 0) > hour_ago]
        older = [h for h in self.history if h.get('timestamp', 0) <= hour_ago]

        if not recent or not older:
            return "数据不足"

        recent_avg = sum(h.get('latency_avg', 0) for h in recent) / len(recent)
        older_avg = sum(h.get('latency_avg', 0) for h in older[-10:]) / min(len(older), 10)

        if recent_avg < older_avg * 0.9:
            return "↓ 改善中"
        elif recent_avg > older_avg * 1.1:
            return "↑ 恶化中"
        else:
            return "→ 稳定"


# ============================================================================
# URL 池管理（健康检查 + 国内优先）
# ============================================================================

class URLPool:
    def __init__(self):
        self.urls: List[str] = []
        self.last_refresh = 0
        self.refresh_interval = URL_POOL_REFRESH_INTERVAL
        self.url_health: Dict[str, Dict] = {}
        self.health_file = "/etc/traffic-padding/url_health.json"
        self._load_health_data()

    def _load_health_data(self):
        try:
            if os.path.exists(self.health_file):
                with open(self.health_file, 'r', encoding='utf-8') as f:
                    self.url_health = json.load(f)
                self._cleanup_old_health_data()
        except (json.JSONDecodeError, IOError):
            self.url_health = {}

    def _save_health_data(self):
        atomic_write_json(self.health_file, self.url_health)

    def _cleanup_old_health_data(self):
        week_ago = time.time() - 7 * 86400
        to_remove = [url for url, h in self.url_health.items()
                     if max(h.get("last_fail", 0), h.get("last_success", 0)) < week_ago
                     and max(h.get("last_fail", 0), h.get("last_success", 0)) > 0]
        for url in to_remove:
            del self.url_health[url]

    def record_url_success(self, url: str):
        if url not in self.url_health:
            self.url_health[url] = {"success": 0, "fail": 0, "last_fail": 0, "last_success": 0}
        self.url_health[url]["success"] += 1
        self.url_health[url]["last_success"] = time.time()
        if self.url_health[url]["success"] % 10 == 0:
            self._save_health_data()

    def record_url_failure(self, url: str):
        if url not in self.url_health:
            self.url_health[url] = {"success": 0, "fail": 0, "last_fail": 0, "last_success": 0}
        self.url_health[url]["fail"] += 1
        self.url_health[url]["last_fail"] = time.time()
        if self.url_health[url]["fail"] % 5 == 0:
            self._save_health_data()

    def _get_url_score(self, url: str) -> float:
        """健康分数 0.0-1.0，1 小时内失败过的减半"""
        h = self.url_health.get(url)
        if not h or (h["success"] + h["fail"]) == 0:
            return 0.5
        rate = h["success"] / (h["success"] + h["fail"])
        if h.get("last_fail", 0) > 0 and (time.time() - h["last_fail"]) < 3600:
            rate *= 0.5
        return rate

    def _get_request_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
        }

    # 国内镜像源（服务器常用）
    def _fetch_domestic_big_files(self) -> List[str]:
        urls = [
            # 阿里云镜像（Ubuntu/CentOS/Python）
            "https://mirrors.aliyun.com/ubuntu/ls-lR.gz",
            "https://mirrors.aliyun.com/centos/7/isos/x86_64/CentOS-7-x86_64-Minimal-2009.iso",
            "https://mirrors.aliyun.com/pypi/packages/source/p/pip/pip-23.0.tar.gz",
            # 腾讯云镜像
            "https://mirrors.tencent.com/ubuntu/ls-lR.gz",
            "https://mirrors.tencent.com/centos/7/isos/x86_64/CentOS-7-x86_64-Minimal-2009.iso",
            # 华为云镜像
            "https://mirrors.huaweicloud.com/ubuntu/ls-lR.gz",
            "https://mirrors.huaweicloud.com/centos/7/isos/x86_64/CentOS-7-x86_64-Minimal-2009.iso",
            # 网易镜像
            "https://mirrors.163.com/ubuntu/ls-lR.gz",
            "https://mirrors.163.com/centos/7/isos/x86_64/CentOS-7-x86_64-Minimal-2009.iso",
            # 清华镜像
            "https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ls-lR.gz",
            "https://mirrors.tuna.tsinghua.edu.cn/anaconda/archive/Anaconda3-2023.09-0-Linux-x86_64.sh",
            # 中科大镜像
            "https://mirrors.ustc.edu.cn/ubuntu/ls-lR.gz",
            "https://mirrors.ustc.edu.cn/centos/7/isos/x86_64/CentOS-7-x86_64-Minimal-2009.iso",
            # 阿里云 OSS 测试文件
            "https://cdn.aliyundcdntest.com/test_100m",
            # 华为云 OBS 测试文件
            "https://huaweicloud.obs.cn-north-1.myhuaweicloud.com/obs_test_10m",
        ]
        random.shuffle(urls)
        return urls[:5]

    # 必应中国每日图片
    def _fetch_bing_china(self) -> List[str]:
        urls = []
        try:
            req = urllib.request.Request(
                "https://cn.bing.com/HPImageArchive.aspx?format=js&idx=0&n=5",
                headers=self._get_request_headers()
            )
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=SSL_CONTEXT_SAFE) as resp:
                for img in json.loads(resp.read().decode('utf-8')).get('images', []):
                    if img.get('url'):
                        urls.append(f"https://cn.bing.com{img['url']}")
        except Exception as e:
            log_message("WARN", f"必应 API 失败: {e}", throttle_key="bing_fail")
        return urls

    # 国内备用源（服务器全栈资源）
    def _fetch_looking_glass(self) -> List[str]:
        urls = [
            # ── 语言运行时 ──
            "https://www.python.org/ftp/python/3.11.5/Python-3.11.5.tgz",
            "https://nodejs.org/dist/v20.5.1/node-v20.5.1-linux-x64.tar.gz",
            "https://go.dev/dl/go1.21.1.linux-amd64.tar.gz",
            "https://github.com/rust-lang/rustup/releases/download/1.26.0/rustup-init",
            "https://www.php.net/distributions/php-8.2.10.tar.gz",
            "https://download.java.net/java/GA/jdk17.0.2/dfd4a8d0985749f896bed50d7138ee7f/8/GPL/openjdk-17.0.2_linux-x64_bin.tar.gz",

            # ── 包管理器/依赖 ──
            "https://github.com/npm/cli/archive/refs/tags/v10.1.0.tar.gz",
            "https://github.com/pypa/pip/archive/refs/tags/23.2.1.tar.gz",
            "https://github.com/composer/composer/releases/download/2.6.2/composer.phar",
            "https://rubygems.org/rubygems/rubygems-3.4.20.tgz",
            "https://github.com/yarnpkg/yarn/releases/download/v1.22.19/yarn-v1.22.19.tar.gz",

            # ── 容器/编排 ──
            "https://download.docker.com/linux/ubuntu/dists/jammy/pool/stable/amd64/containerd.io_1.6.22-1_amd64.deb",
            "https://storage.googleapis.com/kubernetes-release/release/v1.28.2/bin/linux/amd64/kubectl",
            "https://github.com/docker/compose/releases/download/v2.21.0/docker-compose-linux-x86_64",
            "https://get.helm.sh/helm-v3.13.0-linux-amd64.tar.gz",
            "https://github.com/containers/podman/releases/download/v4.7.0/podman-remote-static-linux_amd64.tar.gz",

            # ── Web 服务器/代理 ──
            "https://nginx.org/download/nginx-1.25.2.tar.gz",
            "https://archive.apache.org/dist/httpd/httpd-2.4.58.tar.gz",
            "https://github.com/caddyserver/caddy/releases/download/v2.7.4/caddy_2.7.4_linux_amd64.tar.gz",
            "https://github.com/traefik/traefik/releases/download/v2.10.4/traefik_v2.10.4_linux_amd64.tar.gz",
            "https://github.com/golang/go/archive/refs/tags/go1.21.1.tar.gz",

            # ── 数据库/缓存 ──
            "https://download.redis.io/releases/redis-7.2.1.tar.gz",
            "https://dev.mysql.com/get/Downloads/MySQL-8.0/mysql-community-client-8.0.34-1.el7.x86_64.rpm",
            "https://ftp.postgresql.org/pub/source/v16.0/postgresql-16.0.tar.gz",
            "https://github.com/etcd-io/etcd/releases/download/v3.5.9/etcd-v3.5.9-linux-amd64.tar.gz",
            "https://github.com/valkey-io/valkey/archive/refs/tags/7.2.5.tar.gz",

            # ── 监控/日志 ──
            "https://github.com/prometheus/prometheus/releases/download/v2.47.0/prometheus-2.47.0.linux-amd64.tar.gz",
            "https://github.com/grafana/grafana/releases/download/v10.1.1/grafana-10.1.1.linux-amd64.tar.gz",
            "https://github.com/elastic/elasticsearch/archive/refs/tags/v8.10.2.tar.gz",
            "https://github.com/fluent/fluentd/archive/refs/tags/v1.16.2.tar.gz",
            "https://github.com/influxdata/influxdb/releases/download/v2.7.3/influxdb2-2.7.3-linux-amd64.tar.gz",

            # ── 开发工具 ──
            "https://github.com/cli/cli/releases/download/v2.34.0/gh_2.34.0_linux_amd64.tar.gz",
            "https://github.com/git/git/archive/refs/tags/v2.42.0.tar.gz",
            "https://github.com/jesseduffield/lazygit/releases/download/v0.40.2/lazygit_0.40.2_Linux_x86_64.tar.gz",
            "https://github.com/dandavison/delta/releases/download/0.16.5/delta-0.16.5-x86_64-unknown-linux-musl.tar.gz",
            "https://github.com/sharkdp/fd/releases/download/v9.0.0/fd-v9.0.0-x86_64-unknown-linux-musl.tar.gz",
            "https://github.com/BurntSushi/ripgrep/releases/download/13.0.0/ripgrep-13.0.0-x86_64-unknown-linux-musl.tar.gz",

            # ── 系统工具 ──
            "https://github.com/htop-dev/htop/archive/refs/tags/3.2.2.tar.gz",
            "https://github.com/tmux/tmux/releases/download/3.3a/tmux-3.3a.tar.gz",
            "https://github.com/vim/vim/archive/refs/tags/v9.0.1882.tar.gz",
            "https://github.com/neovim/neovim/releases/download/v0.9.4/nvim-linux64.tar.gz",
            "https://github.com/aria2/aria2/releases/download/release-1.36.0/aria2-1.36.0.tar.gz",

            # ── 编译工具链 ──
            "https://ftp.gnu.org/gnu/gcc/gcc-13.2.0/gcc-13.2.0.tar.gz",
            "https://ftp.gnu.org/gnu/make/make-4.4.1.tar.gz",
            "https://ftp.gnu.org/gnu/cmake/cmake-3.27.5.tar.gz",
            "https://github.com/ninja-build/ninja/archive/refs/tags/v1.11.1.tar.gz",
            "https://github.com/llvm/llvm-project/releases/download/llvmorg-17.0.2/llvm-17.0.2.src.tar.xz",

            # ── 网络工具 ──
            "https://github.com/curl/curl/releases/download/curl-8_3_0/curl-8.3.0.tar.gz",
            "https://github.com/wg/wrk/archive/refs/tags/4.2.0.tar.gz",
            "https://github.com/echo-bot/httpbin/archive/refs/heads/master.tar.gz",
            "https://github.com/ipfs/kubo/releases/download/v0.23.0/kubo_v0.23.0_linux-amd64.tar.gz",

            # ── 安全/证书 ──
            "https://github.com/openssl/openssl/archive/refs/tags/openssl-3.1.3.tar.gz",
            "https://github.com/certbot/certbot/archive/refs/tags/v2.7.0.tar.gz",
            "https://github.com/letsencrypt/boulder/archive/refs/tags/release-2023-09-18.tar.gz",

            # ── Shell/终端 ──
            "https://github.com/ohmyzsh/ohmyzsh/archive/refs/heads/master.tar.gz",
            "https://github.com/starship/starship/releases/download/v1.16.0/starship-x86_64-unknown-linux-musl.tar.gz",
            "https://github.com/ajeetdsouza/zoxide/releases/download/v0.9.2/zoxide-0.9.2-x86_64-unknown-linux-musl.tar.gz",
            "https://github.com/junegunn/fzf/releases/download/v0.43.0/fzf-0.43.0-linux_amd64.tar.gz",
        ]
        random.shuffle(urls)
        return urls[:5]

    def refresh_pool(self) -> bool:
        log_message("INFO", "刷新 URL 池...")
        new_urls = []
        sources = [
            ("国内CDN", self._fetch_domestic_big_files),
            ("必应中国", self._fetch_bing_china),
            ("国内备用", self._fetch_looking_glass),
        ]
        for name, fetcher in sources:
            try:
                fetched = fetcher()
                if fetched:
                    new_urls.extend(fetched)
                    log_message("INFO", f"  {name}: {len(fetched)} 个 URL")
            except Exception as e:
                log_message("WARN", f"  {name} 失败: {e}")

        seen = set()
        valid = [u for u in new_urls if u and u.startswith(('http://', 'https://')) and u not in seen and not seen.add(u)]

        if valid:
            self.urls = valid
            self.last_refresh = time.time()
            domestic = sum(1 for u in valid if any(d in u for d in ['qq.com', '360safe.com', 'bing.com', 'aliyun.com', 'huaweicloud.com']))
            log_message("INFO", f"URL 池: {len(valid)} 个 (国内 {domestic}, 国际 {len(valid) - domestic})")
            return True

        log_message("WARN", "未获取到 URL，下次重试")
        return False

    def get_random_url(self) -> Optional[str]:
        """加权随机选择，健康分数高的优先"""
        if not self.urls or (time.time() - self.last_refresh) > self.refresh_interval:
            self.refresh_pool()
        if not self.urls:
            return None
        if len(self.urls) > 1 and self.url_health:
            weights = [max(0.1, self._get_url_score(u)) for u in self.urls]
            r = random.uniform(0, sum(weights))
            cum = 0
            for i, w in enumerate(weights):
                cum += w
                if r <= cum:
                    return self.urls[i]
        return random.choice(self.urls)

    def get_url_count(self) -> int:
        return len(self.urls)

    @staticmethod
    def get_url_name(url: str) -> str:
        """获取 URL 的中文名称"""
        for domain, name in URL_NAME_MAP.items():
            if domain in url:
                return name
        # 尝试提取域名
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            return parsed.netloc.split('.')[0]
        except Exception:
            return "未知来源"


# ============================================================================
# 微任务下载器（支持短时/长时/长短结合模式）
# ============================================================================

class MicroTaskDownloader:
    def __init__(self, url_pool: URLPool, config: Config = None):
        self.total_downloaded = 0
        self.task_count = 0
        self.success_count = 0
        self.fail_count = 0
        self.url_pool = url_pool
        self.config = config
        self.download_mode = config.get('download_mode', 'short') if config else 'short'
        self.speed_history: List[float] = []  # 下载速度历史 (MB/s)
        self.error_stats: Dict[str, int] = {}  # 错误类型统计
        self.url_speed: Dict[str, List[float]] = {}  # 各 URL 速度

    def execute_task(self, url: str, target_bytes: int) -> Dict:
        """根据下载模式执行任务"""
        # 重新读取配置（支持热切换）
        if self.config:
            self.download_mode = self.config.get('download_mode', 'short')

        if self.download_mode == 'long':
            return self.execute_long_task(url)
        elif self.download_mode == 'mixed':
            # 随机选择模式
            if random.random() < 0.3:  # 30% 概率使用长时模式
                return self.execute_long_task(url)
            else:
                return self.execute_micro_task(url, target_bytes)
        else:  # short
            return self.execute_micro_task(url, target_bytes)

    def execute_micro_task(self, url: str, target_bytes: int) -> Dict:
        """短时下载：使用 Range 请求下载部分数据"""
        start_time = time.time()
        result = {'success': False, 'bytes_downloaded': 0, 'duration': 0, 'error': None, 'mode': 'short'}

        try:
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Range": f"bytes=0-{target_bytes - 1}",
                "Connection": "keep-alive",
            }
            req = urllib.request.Request(url, headers=headers)

            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=SSL_CONTEXT) as resp:
                if resp.status not in (200, 206):
                    result['error'] = f"HTTP {resp.status}"
                    self.url_pool.record_url_failure(url)
                    self._record_error(f"HTTP {resp.status}")
                    raise Exception(f"HTTP {resp.status}")

                bytes_read = 0
                while bytes_read < target_bytes:
                    chunk = resp.read(min(8192, target_bytes - bytes_read))
                    if not chunk:
                        break
                    bytes_read += len(chunk)

                result['success'] = True
                result['bytes_downloaded'] = bytes_read
                self.total_downloaded += bytes_read
                self.url_pool.record_url_success(url)

        except urllib.error.HTTPError as e:
            result['error'] = f"HTTP {e.code}: {e.reason}"
            self.url_pool.record_url_failure(url)
            self._record_error(f"HTTP {e.code}")
        except urllib.error.URLError as e:
            result['error'] = f"URL Error: {e.reason}"
            self.url_pool.record_url_failure(url)
            self._record_error("连接失败")
        except TimeoutError:
            result['error'] = "超时"
            self.url_pool.record_url_failure(url)
            self._record_error("超时")
        except Exception as e:
            if not result['error']:
                result['error'] = str(e)
            self.url_pool.record_url_failure(url)
            self._record_error("其他错误")

        self.task_count += 1
        if result['success']:
            self.success_count += 1
        else:
            self.fail_count += 1

        result['duration'] = time.time() - start_time
        self._record_speed(url, result)
        return result

    def execute_long_task(self, url: str) -> Dict:
        """长时下载：下载完整文件，持续 1-5 分钟"""
        start_time = time.time()
        result = {'success': False, 'bytes_downloaded': 0, 'duration': 0, 'error': None, 'mode': 'long'}

        # 长时下载的目标时间（60-300秒）
        target_duration = random.randint(60, 300)

        try:
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Connection": "keep-alive",
            }
            req = urllib.request.Request(url, headers=headers)

            with urllib.request.urlopen(req, timeout=target_duration + 30, context=SSL_CONTEXT) as resp:
                if resp.status not in (200, 206):
                    result['error'] = f"HTTP {resp.status}"
                    self.url_pool.record_url_failure(url)
                    self._record_error(f"HTTP {resp.status}")
                    raise Exception(f"HTTP {resp.status}")

                bytes_read = 0
                while True:
                    elapsed = time.time() - start_time
                    if elapsed >= target_duration:
                        break
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    bytes_read += len(chunk)

                result['success'] = True
                result['bytes_downloaded'] = bytes_read
                self.total_downloaded += bytes_read
                self.url_pool.record_url_success(url)

        except urllib.error.HTTPError as e:
            result['error'] = f"HTTP {e.code}: {e.reason}"
            self.url_pool.record_url_failure(url)
            self._record_error(f"HTTP {e.code}")
        except urllib.error.URLError as e:
            result['error'] = f"URL Error: {e.reason}"
            self.url_pool.record_url_failure(url)
            self._record_error("连接失败")
        except TimeoutError:
            result['error'] = "超时"
            self.url_pool.record_url_failure(url)
            self._record_error("超时")
        except Exception as e:
            if not result['error']:
                result['error'] = str(e)
            self.url_pool.record_url_failure(url)
            self._record_error("其他错误")

        self.task_count += 1
        if result['success']:
            self.success_count += 1
        else:
            self.fail_count += 1

        result['duration'] = time.time() - start_time
        self._record_speed(url, result)
        return result

    def _record_speed(self, url: str, result: Dict):
        """记录下载速度"""
        if result['success'] and result['duration'] > 0:
            speed = (result['bytes_downloaded'] / (1024 * 1024)) / result['duration']
            self.speed_history.append(speed)
            if len(self.speed_history) > 100:
                self.speed_history = self.speed_history[-100:]

            # 记录各 URL 速度
            url_name = self.url_pool.get_url_name(url)
            if url_name not in self.url_speed:
                self.url_speed[url_name] = []
            self.url_speed[url_name].append(speed)
            if len(self.url_speed[url_name]) > 20:
                self.url_speed[url_name] = self.url_speed[url_name][-20:]

    def _record_error(self, error_type: str):
        """记录错误类型"""
        self.error_stats[error_type] = self.error_stats.get(error_type, 0) + 1

    def get_stats(self) -> Dict:
        return {
            'total_downloaded': self.total_downloaded,
            'task_count': self.task_count,
            'success_count': self.success_count,
            'fail_count': self.fail_count,
            'total_downloaded_mb': self.total_downloaded / (1024 * 1024)
        }

    def get_avg_speed(self) -> float:
        """获取平均下载速度 (MB/s)"""
        if not self.speed_history:
            return 0
        return sum(self.speed_history) / len(self.speed_history)

    def get_fastest_url(self) -> Tuple[str, float]:
        """获取最快的 URL"""
        if not self.url_speed:
            return ("无", 0)
        best_name = max(self.url_speed, key=lambda k: sum(self.url_speed[k]) / len(self.url_speed[k]))
        best_speed = sum(self.url_speed[best_name]) / len(self.url_speed[best_name])
        return (best_name, best_speed)

    def get_slowest_url(self) -> Tuple[str, float]:
        """获取最慢的 URL"""
        if not self.url_speed:
            return ("无", 0)
        worst_name = min(self.url_speed, key=lambda k: sum(self.url_speed[k]) / len(self.url_speed[k]))
        worst_speed = sum(self.url_speed[worst_name]) / len(self.url_speed[worst_name])
        return (worst_name, worst_speed)

    def get_error_stats(self) -> Dict[str, int]:
        """获取错误统计"""
        return self.error_stats


# ============================================================================
# 任务调度器（配额持久化 + 时间权重）
# ============================================================================

class Scheduler:
    def __init__(self, config: Config):
        self.config = config
        self.daily_quota_used = 0
        self.daily_quota_limit = config.get('max_daily_extra_gb', 10) * 1024 * 1024 * 1024
        self.usage_file = USAGE_FILE
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        self._load_usage_from_disk()

    def _load_usage_from_disk(self):
        try:
            if os.path.exists(self.usage_file):
                with open(self.usage_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if data.get('date') == self.current_date:
                        self.daily_quota_used = data.get('used_bytes', 0)
                        log_message("INFO", f"恢复今日配额: {self.daily_quota_used / (1024**3):.3f} GB")
                    else:
                        self._save_usage_to_disk()
        except (json.JSONDecodeError, IOError):
            self.daily_quota_used = 0

    def _save_usage_to_disk(self):
        atomic_write_json(self.usage_file, {"date": self.current_date, "used_bytes": self.daily_quota_used})

    def _reset_daily_quota_if_needed(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self.current_date:
            self.daily_quota_used = 0
            self.current_date = today
            self._save_usage_to_disk()
            log_message("INFO", "配额已重置")

    def get_time_weight(self) -> float:
        """凌晨降频，晚高峰加速"""
        hour = datetime.now().hour
        if self.config.get('enable_night_mode') and self.config.get('night_start_hour', 2) <= hour < self.config.get('night_end_hour', 5):
            return self.config.get('night_multiplier', 5.0)
        if hour in self.config.get('peak_hours', []):
            return self.config.get('peak_multiplier', 0.6)
        return 1.0

    def calculate_jitter_sleep(self) -> float:
        base = self.config.get('jitter_base', 5)
        jitter = self.config.get('jitter_range', 25)
        return max(2.0, (base + random.uniform(0, jitter)) * self.get_time_weight())

    def calculate_task_size(self) -> int:
        min_b = self.config.get('min_task_bytes', 2 * 1024 * 1024)
        max_b = self.config.get('max_task_bytes', 15 * 1024 * 1024)
        return max(min_b, min(max_b, int(random.gauss((min_b + max_b) / 2, (max_b - min_b) / 4))))

    def should_execute_task(self, traffic_stats: Dict) -> Tuple[bool, int]:
        self._reset_daily_quota_if_needed()
        self.daily_quota_limit = self.config.get('max_daily_extra_gb', 10) * 1024 ** 3

        if self.daily_quota_used >= self.daily_quota_limit:
            return False, 0

        avg_rx = traffic_stats.get('avg_rx_delta', 0)
        avg_tx = traffic_stats.get('avg_tx_delta', 0)

        if avg_tx == 0:
            return False, 0

        gap = avg_tx * self.config.get('target_ratio', 3.0) - avg_rx
        if gap <= 0:
            return False, 0

        remaining = self.daily_quota_limit - self.daily_quota_used
        task_size = min(self.calculate_task_size(), int(remaining), int(gap))

        return (True, task_size) if task_size >= 1048576 else (False, 0)

    def record_usage(self, bytes_used: int):
        self.daily_quota_used += bytes_used
        self._save_usage_to_disk()


# ============================================================================
# 通知器基类（共享逻辑）
# ============================================================================

class BaseNotifier:
    """通知器基类，包含共享的报告判断和格式化逻辑"""

    def __init__(self):
        self.report_freq = 'daily'
        self.report_hour = 23  # 推送时间（24小时制）
        self.report_align = 'natural'  # 统计周期对齐方式: natural=自然日/周/月, push_time=按推送时间
        self.monthly_reset_day = 1
        self.monthly_quota_gb = 0
        self.last_report_date = ""

    def _should_report(self) -> bool:
        """判断是否应该发送报告"""
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        current_hour = now.hour

        # 只在推送时间的整点触发
        if current_hour != self.report_hour:
            return False

        if today == self.last_report_date:
            return False
        if self.report_freq == "daily":
            return True
        elif self.report_freq == "weekly":
            return now.weekday() == 0
        elif self.report_freq == "monthly":
            reset_day = self.monthly_reset_day  # 月额度重置日（如每月 1 号）
            current_day = now.day
            # 重置日前一天发送月报（提前通知）
            # 例：reset_day=5 → 4 号发送
            if current_day == reset_day - 1:
                return True
            # 特殊处理：reset_day=1 时，前一天是上月最后一天
            # 无法用 reset_day-1 表示，改为在本月最后一天发送
            if reset_day == 1:
                _, last_day = calendar.monthrange(now.year, now.month)
                if current_day == last_day:
                    return True
            return False
        return False

    def _format_uptime(self, service: 'TrafficPaddingService') -> str:
        """格式化运行时长"""
        seconds = int(time.time() - service.start_time)
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if hours > 24:
            return f"{hours // 24}天{hours % 24}小时"
        return f"{hours}小时{minutes}分钟"

    def _collect_report_data(self, service: 'TrafficPaddingService') -> Dict:
        """采集报告数据（TG/钉钉共用）"""
        stats = service.downloader.get_stats()
        period = service.get_period_stats(self.report_freq)

        # 月流量字符串
        monthly_str = ""
        if self.monthly_quota_gb != 0:
            period = service.get_period_stats('monthly')
            monthly_used_gb = period['gb']
            if self.monthly_quota_gb == -1:
                monthly_str = f"\n- 月总额度: 无限\n- 已消耗: {monthly_used_gb:.3f} GB"
            elif self.monthly_quota_gb > 0:
                monthly_pct = (monthly_used_gb / self.monthly_quota_gb) * 100
                monthly_str = f"\n- 月总额度: {self.monthly_quota_gb:.1f} GB\n- 已消耗: {monthly_used_gb:.3f} GB\n- 占比: {monthly_pct:.2f}%"

        # 下载速度
        avg_speed = service.downloader.get_avg_speed()
        fastest = service.downloader.get_fastest_url()
        slowest = service.downloader.get_slowest_url()

        # 错误统计
        error_stats = service.downloader.get_error_stats()
        error_lines = []
        if error_stats:
            for err, count in list(error_stats.items())[:3]:
                error_lines.append(f"{err}: {count} 次")
            if len(error_stats) > 3:
                error_lines.append(f"...共 {len(error_stats)} 种错误")

        # 网卡流量对比
        net_stats = service.get_network_stats()
        fill_ratio = (stats['total_downloaded_mb'] / net_stats['rx_mb'] * 100) if net_stats['rx_mb'] > 0 else 0

        # URL 健康
        healthy_count = 0
        if service.url_pool.url_health and service.url_pool.urls:
            pool_set = set(service.url_pool.urls)
            healthy_count = sum(1 for url, h in service.url_pool.url_health.items()
                               if url in pool_set and h['success'] / max(1, h['success'] + h['fail']) > 0.8)

        # QoS 状态（优先用缓存，没有则从 history 取最近一条）
        qos_result = service._cached_qos_result
        if not qos_result and service.qos_probe.history:
            qos_result = service.qos_probe.history[-1]
        qos_result = qos_result or {}

        # 带宽监控数据
        bw_stats = None
        bw_today = None
        if service.bandwidth_monitor:
            bw_today = service.bandwidth_monitor.get_today_stats()
            bw_stats = service.bandwidth_monitor.load_today_csv_stats()

        return {
            'now': datetime.now(),
            'stats': stats,
            'period': period,
            'daily_quota_gb': service.config.get('max_daily_extra_gb', 10),
            'quota_used': service.scheduler.daily_quota_used,
            'total_gb': service.total_downloaded_all_time / (1024 ** 3),
            'monthly_str': monthly_str,
            'avg_speed': avg_speed,
            'fastest': fastest,
            'slowest': slowest,
            'error_lines': error_lines,
            'net_stats': net_stats,
            'fill_ratio': fill_ratio,
            'url_count': service.url_pool.get_url_count(),
            'healthy_count': healthy_count,
            'qos_enabled': service.qos_probe.enabled,
            'qos_status': service.qos_probe.get_status_str(),
            'qos_trend': service.qos_probe.get_trend(),
            'qos_result': qos_result,
            'cycle_count': service.cycle_count,
            'uptime': self._format_uptime(service),
            'interface': service.config.get('interface'),
            'target_ratio': service.config.get('target_ratio'),
            'time_weight': service.scheduler.get_time_weight(),
            'server_name': service.server_name,
            'freq_label': self._get_freq_label(),
            'bw_today': bw_today,
            'bw_stats': bw_stats,
            'ai_analysis': service.ai_analyzer.get_latest() if service.ai_analyzer else '',
        }

    def _get_freq_label(self) -> str:
        """获取频率标签"""
        return {"daily": "日报", "weekly": "周报", "monthly": "月报"}.get(self.report_freq, "报告")

    def _should_notify(self, notify_type: str, service: 'TrafficPaddingService' = None) -> bool:
        """检查某类通知是否启用"""
        if service is None:
            return True
        return service.notify_settings.get(notify_type, True)

    def check_and_send(self, service: 'TrafficPaddingService'):
        """检查是否该发送报告并发送"""
        if not self.enabled:
            return
        # 根据报告频率确定通知类型
        freq_type_map = {"daily": "report_daily", "weekly": "report_weekly", "monthly": "report_monthly"}
        notify_type = freq_type_map.get(self.report_freq, "report_daily")
        if not self._should_notify(notify_type, service):
            return
        if self._should_report():
            report = self.build_report(service)
            if self.send_message(report):
                self.last_report_date = datetime.now().strftime("%Y-%m-%d")
                log_message("INFO", f"{self._get_freq_label()} 报告已发送")
                # 周报发送成功后清理CSV日志
                if self.report_freq == "weekly" and service.bandwidth_monitor:
                    now = datetime.now()
                    end_date = now.strftime("%Y%m%d")
                    start_date = (now - timedelta(days=6)).strftime("%Y%m%d")
                    service.bandwidth_monitor.cleanup_csv(start_date, end_date)
            else:
                log_message("WARN", f"{self._get_freq_label()} 报告发送失败")

    def build_report(self, service: 'TrafficPaddingService') -> str:
        """生成报告（子类必须实现）"""
        raise NotImplementedError


# ============================================================================
# Telegram 推送（日报/周报/月报）
# ============================================================================

class TelegramNotifier(BaseNotifier):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.enabled = config.get('tg_enabled', False)
        self.bot_token = config.get('tg_bot_token', '')
        self.chat_id = config.get('tg_chat_id', '')
        self.report_freq = config.get('tg_report_freq', 'daily')
        self.report_hour = config.get('tg_report_hour', 23)
        self.report_align = config.get('tg_report_align', 'natural')
        self.monthly_reset_day = config.get('tg_monthly_reset_day', 1)
        self.monthly_quota_gb = config.get('monthly_quota_gb', 0)

    def send_message(self, text: str) -> bool:
        if not self.enabled or not self.bot_token or not self.chat_id:
            return False
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            data = json.dumps({"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}).encode('utf-8')
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15, context=SSL_CONTEXT_SAFE) as resp:
                return resp.status == 200
        except Exception as e:
            log_message("WARN", f"TG 推送失败: {e}", throttle_key="tg_fail")
            return False

    def build_report(self, service: 'TrafficPaddingService') -> str:
        d = self._collect_report_data(service)
        period_str = f"{d['period']['label']}: {d['period']['gb']:.3f} GB"

        # 错误统计格式化
        error_str = ""
        if d['error_lines']:
            lines = [f"├ {line}" for line in d['error_lines']]
            error_str = "\n" + "\n".join(lines)
            idx = error_str.rfind("├")
            if idx >= 0:
                error_str = error_str[:idx] + "└" + error_str[idx + len("├"):]

        # URL 健康
        url_health_str = f"\n├ 健康: {d['healthy_count']}/{d['url_count']}" if d['healthy_count'] else ""

        # QoS 状态
        qos_str = ""
        if d['qos_enabled']:
            qos_str = f"""

🌐 QoS 探测
├ 状态: {d['qos_status']}
├ 趋势: {d['qos_trend']}
├ 延迟: {d['qos_result'].get('latency_avg', 0):.0f}ms
├ 抖动: {d['qos_result'].get('jitter', 0):.0f}ms
└ 丢包: {d['qos_result'].get('loss', 0):.0f}%"""

        # 带宽监控段
        bw_str = ""
        if d['bw_today']:
            bt = d['bw_today']
            # 智能单位：大于1GB用GB，否则用MB
            def fmt_bytes(b):
                if b >= 1024**3:
                    return f"{b / (1024**3):.2f} GB"
                elif b >= 1024**2:
                    return f"{b / (1024**2):.1f} MB"
                else:
                    return f"{b / 1024:.0f} KB"
            # 告警详情
            alert_detail = ""
            if bt.get('alert_history'):
                for ah in bt['alert_history']:
                    dur_min = ah['duration_sec'] // 60
                    dur_sec = ah['duration_sec'] % 60
                    alert_detail += f"\n│   {ah['start']}-{ah['end']} 持续{dur_min}分{dur_sec}秒 峰值{ah['peak_mbps']:.0f}Mbps"

            bw_str = f"""
📊 带宽监控
├ 入站峰值: {bt['rx_peak']:.1f} Mbps ({bt['rx_peak_time']})
├ 出站峰值: {bt['tx_peak']:.1f} Mbps ({bt['tx_peak_time']})
├ 入站平均: {bt['rx_avg']:.1f} Mbps
├ 出站平均: {bt['tx_avg']:.1f} Mbps
├ 总流量: RX {fmt_bytes(bt['rx_bytes'])} / TX {fmt_bytes(bt['tx_bytes'])}
└ 告警: {bt['alert_count']} 次{alert_detail}"""

        # AI 分析段
        ai_str = ""
        if d['ai_analysis']:
            ai_str = f"\n\n🤖 AI 分析\n{d['ai_analysis']}"

        return f"""📋 <b>Traffic Padding {d['freq_label']}</b>
━━━━━━━━━━━━━━━━━━━━

🖥️ <b>{d['server_name']}</b>

🕐 {d['now'].strftime("%Y-%m-%d %H:%M")}{bw_str}

📦 流量填充
├ {period_str}
├ 累计总量: {d['total_gb']:.3f} GB
├ 今日配额: {d['quota_used'] / (1024**3):.3f} / {d['daily_quota_gb']:.1f} GB{d['monthly_str']}

📈 下载性能
├ 平均速度: {d['avg_speed']:.2f} MB/s
├ 最快来源: {d['fastest'][0]} ({d['fastest'][1]:.1f} MB/s)
└ 最慢来源: {d['slowest'][0]} ({d['slowest'][1]:.1f} MB/s)

📊 流量对比
├ 实际 RX: {d['net_stats']['rx_mb']:.1f} MB
├ 实际 TX: {d['net_stats']['tx_mb']:.1f} MB
├ 填充下载: {d['stats']['total_downloaded_mb']:.1f} MB
└ 填充占比: {d['fill_ratio']:.1f}%

🔗 URL 状态
├ 总数: {d['url_count']} 个{url_health_str}
├ 成功: {d['stats']['success_count']} 次
└ 失败: {d['stats']['fail_count']} 次{error_str}{qos_str}

📈 运行状态
├ 周期: {d['cycle_count']}
├ 任务: {d['stats']['task_count']}
└ 时长: {d['uptime']}

⚙️ 配置
├ 网卡: {d['interface']}
├ 比例: 1:{d['target_ratio']}
└ 权重: {d['time_weight']:.2f}x{ai_str}"""


# ============================================================================
# 钉钉机器人推送（日报/周报/月报）
# ============================================================================

class DingTalkNotifier(BaseNotifier):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.enabled = config.get('dingtalk_enabled', False)
        self.webhook_url = config.get('dingtalk_webhook', '')
        self.secret = config.get('dingtalk_secret', '')
        self.report_freq = config.get('dingtalk_report_freq', 'daily')
        self.report_hour = config.get('dingtalk_report_hour', 23)
        self.report_align = config.get('dingtalk_report_align', 'natural')
        self.monthly_reset_day = config.get('dingtalk_monthly_reset_day', 1)
        self.monthly_quota_gb = config.get('monthly_quota_gb', 0)

    def _get_sign_url(self) -> str:
        """生成加签 URL（如果配置了 secret）"""
        if not self.secret:
            return self.webhook_url
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{self.secret}"
        hmac_code = hmac.new(
            self.secret.encode('utf-8'),
            string_to_sign.encode('utf-8'),
            digestmod=hashlib.sha256
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        return f"{self.webhook_url}&timestamp={timestamp}&sign={sign}"

    def send_message(self, text: str) -> bool:
        if not self.enabled or not self.webhook_url:
            return False
        try:
            url = self._get_sign_url()
            data = json.dumps({
                "msgtype": "markdown",
                "markdown": {"title": "Traffic Padding 报告", "text": text}
            }).encode('utf-8')
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15, context=SSL_CONTEXT_SAFE) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                return result.get('errcode') == 0
        except Exception as e:
            log_message("WARN", f"钉钉推送失败: {e}", throttle_key="dingtalk_fail")
            return False

    def _draw_traffic_chart(self, service: 'TrafficPaddingService') -> str:
        """生成横向流量柱状图（用带宽监控 CSV 数据）"""
        bw_today = None
        if service.bandwidth_monitor:
            bw_today = service.bandwidth_monitor.get_today_stats()

        stats = service.downloader.get_stats()
        download_mb = stats['total_downloaded_mb']

        rx_mb = bw_today['rx_bytes'] / (1024 * 1024) if bw_today else 0
        tx_mb = bw_today['tx_bytes'] / (1024 * 1024) if bw_today else 0

        if rx_mb < 0.01 and tx_mb < 0.01 and download_mb < 0.01:
            return ""

        max_val = max(rx_mb, tx_mb, download_mb, 0.01)
        bar_width = 30

        def fmt_val(v):
            return f"{v:.1f}MB" if v >= 1 else f"{v*1024:.0f}KB"

        def draw_bar(label, value):
            filled = int((value / max_val) * bar_width) if max_val > 0 else 0
            bar = '█' * filled + '░' * (bar_width - filled)
            return f"  {label:8s} {bar} {fmt_val(value):>9s}"

        lines = [
            "### 📊 流量柱状图",
            "",
            "```javascript",
            "┌─────────────────────────────────────────────────────┐",
            "│                                                     │",
            f"{draw_bar('上行 TX', tx_mb)}   │",
            "│                                                     │",
            f"{draw_bar('下行 RX', rx_mb)}   │",
            "│                                                     │",
            f"{draw_bar('填充', download_mb)}   │",
            "│                                                     │",
            "└─────────────────────────────────────────────────────┘",
            "```",
        ]
        return "\n".join(lines)

    def build_report(self, service: 'TrafficPaddingService') -> str:
        d = self._collect_report_data(service)
        period_str = f"{d['period']['label']}: {d['period']['gb']:.3f} GB"

        # 错误统计格式化
        error_str = ""
        if d['error_lines']:
            for line in d['error_lines']:
                error_str += f"\n- {line}"

        # URL 健康
        url_health_str = f"\n- 健康: {d['healthy_count']}/{d['url_count']}" if d['healthy_count'] else ""

        # QoS 状态（国内外分开显示）
        qos_str = ""
        if d['qos_enabled']:
            qr = d['qos_result']
            dom = qr.get('domestic', {})
            intl = qr.get('international', {})
            qos_str = f"""
### 🌐 QoS 探测
- 状态: {d['qos_status']}
- 趋势: {d['qos_trend']}
- 国内: {dom.get('avg', 0):.0f}ms / 抖动{dom.get('jitter', 0):.0f}ms / 丢包{dom.get('loss', 0):.0f}%
- 跨境: {intl.get('avg', 0):.0f}ms / 抖动{intl.get('jitter', 0):.0f}ms / 丢包{intl.get('loss', 0):.0f}%"""

        chart_str = self._draw_traffic_chart(service)

        # 带宽监控段
        bw_str = ""
        if d['bw_today']:
            bt = d['bw_today']
            def fmt_bytes(b):
                if b >= 1024**3:
                    return f"{b / (1024**3):.2f} GB"
                elif b >= 1024**2:
                    return f"{b / (1024**2):.1f} MB"
                else:
                    return f"{b / 1024:.0f} KB"
            # 告警详情
            alert_detail = ""
            if bt.get('alert_history'):
                for ah in bt['alert_history']:
                    dur_min = ah['duration_sec'] // 60
                    dur_sec = ah['duration_sec'] % 60
                    alert_detail += f"\n-   {ah['start']}-{ah['end']} 持续{dur_min}分{dur_sec}秒 峰值{ah['peak_mbps']:.0f}Mbps"

            bw_str = f"""### 📊 带宽监控
- 入站峰值: {bt['rx_peak']:.1f} Mbps ({bt['rx_peak_time']})
- 出站峰值: {bt['tx_peak']:.1f} Mbps ({bt['tx_peak_time']})
- 入站平均: {bt['rx_avg']:.1f} Mbps
- 出站平均: {bt['tx_avg']:.1f} Mbps
- 总流量: RX {fmt_bytes(bt['rx_bytes'])} / TX {fmt_bytes(bt['tx_bytes'])}
- 告警: {bt['alert_count']} 次{alert_detail}"""

        # AI 分析段
        ai_str = ""
        if d['ai_analysis']:
            ai_str = f"\n\n### 🤖 AI 分析\n\n{d['ai_analysis']}"

        # 构建报告（- 和内容同行，钉钉渲染为 •）
        report = f"""## 📋 Traffic Padding {d['freq_label']}
**🖥️ {d['server_name']}**
🕐 **{d['now'].strftime("%Y-%m-%d %H:%M")}**
{bw_str}

### 📦 流量填充
- {period_str}
- 累计总量: {d['total_gb']:.3f} GB
- 今日配额: {d['quota_used'] / (1024**3):.3f} / {d['daily_quota_gb']:.1f} GB{d['monthly_str']}

### 📈 下载性能
- 平均速度: {d['avg_speed']:.2f} MB/s
- 最快来源: {d['fastest'][0]} ({d['fastest'][1]:.1f} MB/s)
- 最慢来源: {d['slowest'][0]} ({d['slowest'][1]:.1f} MB/s)

### 📊 流量对比
- 实际 RX: {d['net_stats']['rx_mb']:.1f} MB
- 实际 TX: {d['net_stats']['tx_mb']:.1f} MB
- 填充下载: {d['stats']['total_downloaded_mb']:.1f} MB
- 填充占比: {d['fill_ratio']:.1f}%

### 🔗 URL 状态
- 总数: {d['url_count']} 个{url_health_str}
- 成功: {d['stats']['success_count']} 次
- 失败: {d['stats']['fail_count']} 次{error_str}{qos_str}

{chart_str}

### 📈 运行状态
- 周期: {d['cycle_count']}
- 任务: {d['stats']['task_count']}
- 时长: {d['uptime']}

### ⚙️ 配置
- 网卡: {d['interface']}
- 比例: 1:{d['target_ratio']}
- 权重: {d['time_weight']:.2f}x{ai_str}"""
        return report


# ============================================================================
# 启动健康检查
# ============================================================================

class HealthChecker:
    @staticmethod
    def check_interface(interface: str) -> bool:
        rx, tx = read_net_dev(interface)
        return rx is not None

    @staticmethod
    def check_config(config_path: str) -> bool:
        if not os.path.exists(config_path):
            return True
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                json.load(f)
            return True
        except (json.JSONDecodeError, IOError):
            return False

    @staticmethod
    def check_network() -> bool:
        for url in ["https://www.baidu.com", "https://cn.bing.com", "https://www.aliyun.com"]:
            try:
                req = urllib.request.Request(url, method='HEAD')
                req.add_header('User-Agent', random.choice(USER_AGENTS))
                with urllib.request.urlopen(req, timeout=5, context=SSL_CONTEXT_SAFE) as resp:
                    if resp.status < 400:
                        return True
            except Exception:
                continue
        return False

    @classmethod
    def run_all_checks(cls, interface: str, config_path: str) -> bool:
        log_message("INFO", "启动健康检查...")
        checks = [
            ("Python", lambda: sys.version_info >= (3, 6)),
            ("配置文件", lambda: cls.check_config(config_path)),
            ("网卡", lambda: cls.check_interface(interface)),
            ("网络", cls.check_network),
        ]
        ok = True
        for name, fn in checks:
            try:
                r = fn()
                log_message("INFO", f"  {name}: {'✓' if r else '✗'}")
                if not r:
                    ok = False
            except Exception as e:
                log_message("ERROR", f"  {name}: {e}")
                ok = False
        return ok


# ============================================================================
# 主控制器
# ============================================================================

class TrafficPaddingService:
    def __init__(self, config_path: str = CONFIG_FILE):
        self.config = Config(config_path)
        self.url_pool = URLPool()
        self.downloader = MicroTaskDownloader(self.url_pool, self.config)
        self.scheduler = Scheduler(self.config)
        self.tg_notifier = TelegramNotifier(self.config)
        self.dingtalk_notifier = DingTalkNotifier(self.config)
        self.qos_probe = QoSProbe(self.config)
        self.running = False
        self.cycle_count = 0
        self.last_tg_check = 0
        self.last_qos_check = 0
        self.first_task_done = False
        self._cached_traffic_stats = None
        self.start_time = time.time()
        self.manual_report_requested = False
        self._quota_exhausted_notified = False  # 配额用完通知标记
        self.server_name = self.config.get('server_name', 'Realm中转服务器')

        # 通知管理（8种通知类型，默认全部启用）
        self.notify_settings = {
            'report_daily': True,
            'report_weekly': True,
            'report_monthly': True,
            'bandwidth_alert': True,
            'bandwidth_alert_recovery': True,
            'qos_alert': True,
            'service_start_stop': True,
            'first_test': True,
        }
        self._load_notify_settings()

        # 带宽监控（独立线程）
        self.bandwidth_monitor = None
        if self.config.get('monitor_enabled', True):
            self.bandwidth_monitor = BandwidthMonitor(
                self.config.get('interface', 'eth0'),
                self.config,
                notifier_callback=self._send_notification
            )
            self.bandwidth_monitor.start()

        # 流量监控（从带宽监控读缓存）
        self.monitor = TrafficMonitor(self.config.get('interface', 'eth0'), self.bandwidth_monitor)

        # AI 分析器
        self.ai_analyzer = AIAnalyzer(self.config, self)
        if self.config.get('ai_enabled', True):
            self.ai_analyzer.start()

        # 用量统计
        self.stats_file = STATS_FILE
        self.total_downloaded_all_time = 0  # 历史总下载量
        self.daily_stats = {}  # 每日统计 {date: bytes}
        self._load_stats()

        # 网卡流量统计（用于对比）
        self.start_rx_bytes = 0
        self.start_tx_bytes = 0
        self._init_network_baseline()

        # QoS 探测结果缓存
        self._cached_qos_result = None

        # 流量历史记录
        self.traffic_history_file = TRAFFIC_HISTORY_FILE
        self.traffic_history: List[Dict] = []
        self._load_traffic_history()
        self.last_traffic_record = 0

    def _load_traffic_history(self):
        """加载流量历史记录"""
        try:
            if os.path.exists(self.traffic_history_file):
                with open(self.traffic_history_file, 'r', encoding='utf-8') as f:
                    self.traffic_history = json.load(f)
                    # 只保留最近 90 天的数据
                    cutoff = time.time() - 90 * 86400
                    self.traffic_history = [h for h in self.traffic_history if h.get('timestamp', 0) > cutoff]
        except (json.JSONDecodeError, IOError):
            self.traffic_history = []

    def _save_traffic_history(self):
        """保存流量历史记录"""
        # 只保留最近 10000 条记录
        if len(self.traffic_history) > 10000:
            self.traffic_history = self.traffic_history[-10000:]
        atomic_write_json(self.traffic_history_file, self.traffic_history)

    def record_traffic_snapshot(self):
        """记录当前流量快照"""
        now = time.time()
        if now - self.last_traffic_record < TRAFFIC_RECORD_INTERVAL:
            return
        self.last_traffic_record = now

        net_stats = self.get_network_stats()
        stats = self.downloader.get_stats()

        snapshot = {
            'timestamp': now,
            'rx_bytes': net_stats['rx_bytes'],
            'tx_bytes': net_stats['tx_bytes'],
            'rx_mb': net_stats['rx_mb'],
            'tx_mb': net_stats['tx_mb'],
            'download_bytes': self.total_downloaded_all_time,
            'download_mb': stats['total_downloaded_mb'],
            'task_count': stats['task_count'],
        }

        self.traffic_history.append(snapshot)
        self._save_traffic_history()

    def get_traffic_summary(self, period: str, report_align: str = 'natural', report_hour: int = 23) -> Dict:
        """获取指定时间段的流量汇总"""
        now = datetime.now()
        start = None  # 初始化 start 变量

        if period == 'total':
            # 从最早记录开始
            start_time = 0
            label = "启用至今"
        else:
            # 根据对齐方式计算开始时间
            if report_align == 'push_time':
                # 按推送时间对齐
                if period == 'daily':
                    start = now.replace(hour=report_hour, minute=0, second=0, microsecond=0)
                    if now < start:
                        start = start - timedelta(days=1)
                    label = f"{start.strftime('%m月%d日 %H:%M')} - {now.strftime('%m月%d日 %H:%M')}"
                elif period == 'weekly':
                    start = now.replace(hour=report_hour, minute=0, second=0, microsecond=0)
                    days_since_monday = now.weekday()
                    start = start - timedelta(days=days_since_monday)
                    if now < start:
                        start = start - timedelta(weeks=1)
                    label = f"本周 ({start.strftime('%m/%d %H:%M')} - {now.strftime('%m/%d %H:%M')})"
                elif period == 'monthly':
                    start = now.replace(day=1, hour=report_hour, minute=0, second=0, microsecond=0)
                    if now < start:
                        if start.month == 1:
                            start = start.replace(year=start.year - 1, month=12)
                        else:
                            start = start.replace(month=start.month - 1)
                    label = f"{now.strftime('%Y年%m月')} ({start.strftime('%m/%d %H:%M')} -)"
            else:
                # 自然日/周/月
                today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                if period == 'daily':
                    start = today_start
                    label = f"{now.strftime('%m月%d日')}"
                elif period == 'weekly':
                    start = today_start - timedelta(days=today_start.weekday())
                    label = f"本周 ({start.strftime('%m/%d')}-{now.strftime('%m/%d')})"
                elif period == 'monthly':
                    start = today_start.replace(day=1)
                    label = f"{now.strftime('%Y年%m月')}"

            # 如果 start 未被赋值（未知的 period），使用当前时间
            if start is None:
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                label = "未知周期"

            start_time = start.timestamp()

        # 过滤指定时间段的数据
        period_data = [h for h in self.traffic_history if h.get('timestamp', 0) >= start_time]

        if not period_data:
            return {
                'label': label,
                'rx_mb': 0,
                'tx_mb': 0,
                'download_mb': 0,
                'task_count': 0,
                'records': 0,
            }

        # 计算差值（相对于时间段开始）
        first = period_data[0]
        last = period_data[-1]

        rx_delta = last.get('rx_bytes', 0) - first.get('rx_bytes', 0)
        tx_delta = last.get('tx_bytes', 0) - first.get('tx_bytes', 0)
        download_delta = last.get('download_bytes', 0) - first.get('download_bytes', 0)

        return {
            'label': label,
            'rx_mb': max(0, rx_delta / (1024 * 1024)),
            'tx_mb': max(0, tx_delta / (1024 * 1024)),
            'download_mb': max(0, download_delta / (1024 * 1024)),
            'task_count': last.get('task_count', 0),
            'records': len(period_data),
        }

    def _init_network_baseline(self):
        """初始化网卡流量基准"""
        rx, tx = read_net_dev(self.config.get('interface', 'eth0'))
        if rx is not None:
            self.start_rx_bytes = rx
            self.start_tx_bytes = tx

    def get_network_stats(self) -> Dict:
        """获取网卡流量统计"""
        iface = self.config.get('interface', 'eth0')
        rx, tx = read_net_dev(iface)
        current_rx = rx if rx is not None else 0
        current_tx = tx if tx is not None else 0

        rx_delta = current_rx - self.start_rx_bytes
        tx_delta = current_tx - self.start_tx_bytes

        return {
            'rx_bytes': rx_delta,
            'tx_bytes': tx_delta,
            'rx_mb': rx_delta / (1024 * 1024),
            'tx_mb': tx_delta / (1024 * 1024)
        }

    def _load_stats(self):
        """加载历史统计数据，校准今日数据"""
        try:
            if os.path.exists(self.stats_file):
                with open(self.stats_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.total_downloaded_all_time = data.get('total_downloaded', 0)
                    self.daily_stats = data.get('daily_stats', {})
                    log_message("INFO", f"加载统计数据: 总用量 {self.total_downloaded_all_time / (1024**3):.3f} GB")

            # 保留今日数据（不归零，防止重启丢失进度）
            today = datetime.now().strftime("%Y-%m-%d")
            if today in self.daily_stats:
                log_message("INFO", f"恢复今日统计: {self.daily_stats[today] / (1024**3):.3f} GB")
        except (json.JSONDecodeError, IOError):
            self.total_downloaded_all_time = 0
            self.daily_stats = {}

    def _save_stats(self):
        """保存统计数据"""
        # 清理超过 90 天的旧数据
        cutoff_ts = datetime.now().timestamp() - 90 * 86400
        self.daily_stats = {
            k: v for k, v in self.daily_stats.items()
            if datetime.strptime(k, "%Y-%m-%d").timestamp() > cutoff_ts
        }
        atomic_write_json(self.stats_file, {
            'total_downloaded': self.total_downloaded_all_time,
            'daily_stats': self.daily_stats
        })

    def record_download(self, bytes_downloaded: int):
        """记录下载量"""
        self.total_downloaded_all_time += bytes_downloaded
        today = datetime.now().strftime("%Y-%m-%d")
        self.daily_stats[today] = self.daily_stats.get(today, 0) + bytes_downloaded
        self._save_stats()

    def get_period_stats(self, freq: str) -> dict:
        """获取指定周期的统计"""
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")

        if freq == "daily":
            period_bytes = self.daily_stats.get(today, 0)
            period_label = "今日"
        elif freq == "weekly":
            # 本周（周一到今天）
            period_bytes = 0
            for i in range(7):
                day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
                period_bytes += self.daily_stats.get(day, 0)
                if (now - timedelta(days=i)).weekday() == 0:
                    break
            period_label = "本周"
        elif freq == "monthly":
            # 本月
            period_bytes = 0
            month_prefix = now.strftime("%Y-%m")
            for k, v in self.daily_stats.items():
                if k.startswith(month_prefix):
                    period_bytes += v
            period_label = "本月"
        else:
            period_bytes = 0
            period_label = "总计"

        return {
            'bytes': period_bytes,
            'gb': period_bytes / (1024 ** 3),
            'label': period_label
        }

    def _load_notify_settings(self):
        """加载通知设置"""
        try:
            notify_file = os.path.join(os.path.dirname(self.config.config_path), "notify.json")
            if os.path.exists(notify_file):
                with open(notify_file, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                    self.notify_settings.update(saved)
        except (json.JSONDecodeError, IOError):
            pass

    def save_notify_settings(self):
        """保存通知设置"""
        notify_file = os.path.join(os.path.dirname(self.config.config_path), "notify.json")
        atomic_write_json(notify_file, self.notify_settings)

    def _send_notification(self, title: str, text: str, notify_type: str = None):
        """发送通知（可按类型过滤）"""
        if notify_type and not self.notify_settings.get(notify_type, True):
            return
        if self.tg_notifier.enabled:
            self.tg_notifier.send_message(text)
        if self.dingtalk_notifier.enabled:
            self.dingtalk_notifier.send_message(text)

    def _log_stats(self, traffic_stats: Dict = None):
        stats = self.downloader.get_stats()
        if traffic_stats is None:
            traffic_stats = self._cached_traffic_stats or {}
        rx_mb = traffic_stats.get('avg_rx_delta', 0) / (1024 * 1024)
        tx_mb = traffic_stats.get('avg_tx_delta', 0) / (1024 * 1024)
        ratio = traffic_stats.get('ratio', 1.0)
        ratio_str = f"1:{ratio:.1f}" if ratio != float('inf') else "1:∞"
        need_pad = "需要" if traffic_stats.get('need_padding', False) else "正常"
        now = datetime.now().strftime("%H:%M:%S")

        log_message("INFO", "=" * 65)
        log_message("INFO", f"[{now}] 周期 #{self.cycle_count} | RX:{rx_mb:.1f}MB TX:{tx_mb:.1f}MB 比例:{ratio_str}")
        log_message("INFO", f"填充:{need_pad} | 任务:{stats['task_count']} | 下载:{stats['total_downloaded_mb']:.1f}MB")
        log_message("INFO", f"配额: {self.scheduler.daily_quota_used / (1024**3):.3f}/{self.config.get('max_daily_extra_gb', 10)}GB | "
                      f"URL: {self.url_pool.get_url_count()} 个")
        log_message("INFO", "=" * 65)

    def run_cycle(self):
        try:
            self._run_cycle_inner()
        except Exception as e:
            log_message("ERROR", f"运行周期异常: {e}")
            # 不崩溃，继续下一个周期

    def _run_cycle_inner(self):
        self.cycle_count += 1
        self.config.check_and_reload()

        traffic_stats = self.monitor.get_traffic_stats()
        self._cached_traffic_stats = traffic_stats  # 缓存供 _log_stats 使用

        # 计算是否需要填充
        avg_rx = traffic_stats.get('avg_rx_delta', 0)
        avg_tx = traffic_stats.get('avg_tx_delta', 0)
        target_ratio = self.config.get('target_ratio', 3.0)
        if avg_tx > 0:
            current_ratio = avg_rx / avg_tx
            traffic_stats['need_padding'] = current_ratio < target_ratio
        else:
            traffic_stats['need_padding'] = False

        should_run, target_bytes = self.scheduler.should_execute_task(traffic_stats)

        # 配额用完通知（只通知一次）
        if not should_run and self.scheduler.daily_quota_used >= self.scheduler.daily_quota_limit:
            if not self._quota_exhausted_notified:
                self._quota_exhausted_notified = True
                quota_gb = self.scheduler.daily_quota_limit / (1024**3)
                msg = f"📊 今日配额已用完\n\n配额: {quota_gb:.1f} GB\n填充下载已暂停，明天自动恢复。"
                self._send_notification("📊 配额用完", msg, "bandwidth_alert")
                log_message("INFO", f"今日配额 {quota_gb:.1f} GB 已用完，暂停填充")

        if should_run:
            self._quota_exhausted_notified = False  # 有新任务时重置标记
            url = self.url_pool.get_random_url()
            if url:
                result = self.downloader.execute_task(url, target_bytes)
                if result['success']:
                    self.scheduler.record_usage(result['bytes_downloaded'])
                    self.record_download(result['bytes_downloaded'])  # 记录到统计
                    url_name = self.url_pool.get_url_name(url)
                    mode_str = "[长时]" if result.get('mode') == 'long' else "[短时]"
                    log_message("INFO", f"下载 {result['bytes_downloaded'] / (1024*1024):.1f}MB 耗时 {result['duration']:.1f}s {mode_str} 来源:{url_name}")

                    # 首次任务完成时发送数据推送测试消息（仅一次）
                    if not self.first_task_done:
                        self.first_task_done = True
                        url_name = self.url_pool.get_url_name(url)
                        # 确定推送渠道和频率
                        if self.tg_notifier.enabled and self.notify_settings.get('first_test', True):
                            freq_label = {"daily": "日报", "weekly": "周报", "monthly": "月报"}.get(self.tg_notifier.report_freq, "报告")
                            self.tg_notifier.send_message(
                                f"🧪 <b>【数据推送测试消息】</b>\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                                f"🖥️ {self.server_name}\n\n"
                                f"✅ 首次下载任务已完成\n\n"
                                f"📊 任务详情\n"
                                f"├ 下载量: {result['bytes_downloaded'] / (1024*1024):.1f} MB\n"
                                f"├ 耗时: {result['duration']:.1f}s\n"
                                f"└ 来源: {url_name}\n\n"
                                f"⚙️ 推送设置\n"
                                f"├ 频率: {freq_label}\n"
                                f"└ 状态: 正常运行中\n\n"
                                f"💡 这是一次性测试消息，后续将按 [{freq_label}] 频率自动推送。"
                            )
                        if self.dingtalk_notifier.enabled and self.notify_settings.get('first_test', True):
                            freq_label = {"daily": "日报", "weekly": "周报", "monthly": "月报"}.get(self.dingtalk_notifier.report_freq, "报告")
                            self.dingtalk_notifier.send_message(
                                f"## 🧪 【数据推送测试消息】\n\n---\n\n"
                                f"**🖥️ {self.server_name}**\n\n"
                                f"✅ 首次下载任务已完成\n\n"
                                f"### 📊 任务详情\n"
                                f"- 下载量: {result['bytes_downloaded'] / (1024*1024):.1f} MB\n"
                                f"- 耗时: {result['duration']:.1f}s\n"
                                f"- 来源: {url_name}\n\n"
                                f"### ⚙️ 推送设置\n"
                                f"- 频率: {freq_label}\n"
                                f"- 状态: 正常运行中\n\n"
                                f"> 💡 这是一次性测试消息，后续将按 [{freq_label}] 频率自动推送。"
                            )
                        log_message("INFO", "已发送数据推送测试消息")
                else:
                    log_message("WARN", f"下载失败: {result['error']}", throttle_key="dl_fail")

        if self.cycle_count % 20 == 0:
            self._log_stats()

        # 记录流量快照
        self.record_traffic_snapshot()

        # 手动推送请求处理
        if self.manual_report_requested:
            self.manual_report_requested = False
            log_message("INFO", "收到手动推送请求，发送报告...")
            report_sent = False
            if self.tg_notifier.enabled:
                report = self.tg_notifier.build_report(self)
                if self.tg_notifier.send_message(report):
                    report_sent = True
                    log_message("INFO", "TG 手动报告已发送")
            if self.dingtalk_notifier.enabled:
                report = self.dingtalk_notifier.build_report(self)
                if self.dingtalk_notifier.send_message(report):
                    report_sent = True
                    log_message("INFO", "钉钉手动报告已发送")
            if not report_sent:
                log_message("WARN", "未启用推送或发送失败")

        # 定期报告检查
        now = time.time()
        if now - self.last_tg_check >= TG_CHECK_INTERVAL:
            self.last_tg_check = now
            self.tg_notifier.check_and_send(self)
            self.dingtalk_notifier.check_and_send(self)

        # QoS 定期探测（随机间隔 15-30 分钟）
        qos_interval = random.randint(900, 1800)  # 15-30 分钟
        if now - self.last_qos_check >= qos_interval:
            self.last_qos_check = now
            if self.qos_probe.enabled:
                log_message("INFO", "执行 QoS 探测...")
                self._cached_qos_result = self.qos_probe.probe_all()
                status = self._cached_qos_result.get('qos_level', 'unknown')
                latency = self._cached_qos_result.get('latency_avg', 0)
                loss = self._cached_qos_result.get('loss', 0)
                log_message("INFO", f"QoS 状态: {status} | 延迟: {latency:.0f}ms | 丢包: {loss:.0f}%")

                # 如果检测到严重 QoS，发送告警
                if status == 'bad' and self.qos_probe.history:
                    last_status = self.qos_probe.history[-2].get('qos_level', 'good') if len(self.qos_probe.history) > 1 else 'good'
                    if last_status != 'bad':
                        self._send_qos_alert()

        # 可中断的 sleep（每秒检查一次 self.running）
        sleep_time = self.scheduler.calculate_jitter_sleep()
        for _ in range(int(sleep_time)):
            if not self.running:
                break
            time.sleep(1)

    def _send_qos_alert(self):
        """发送 QoS 告警"""
        if not self.notify_settings.get('qos_alert', True):
            return
        result = self._cached_qos_result
        if not result:
            return

        reasons = result.get('qos_reasons', [])
        reasons_str = '\n'.join([f"- {r}" for r in reasons])

        if self.tg_notifier.enabled:
            self.tg_notifier.send_message(
                f"⚠️ <b>【QoS 告警】</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🖥️ {self.server_name}\n\n"
                f"检测到跨境网络拥堵：\n\n"
                f"📊 网络状态\n"
                f"├ 延迟: {result.get('latency_avg', 0):.0f}ms\n"
                f"├ 抖动: {result.get('jitter', 0):.0f}ms\n"
                f"└ 丢包: {result.get('loss', 0):.0f}%\n\n"
                f"⚠️ 原因\n{reasons_str}"
            )

        if self.dingtalk_notifier.enabled:
            self.dingtalk_notifier.send_message(
                f"## ⚠️ 【QoS 告警】\n\n---\n\n"
                f"**🖥️ {self.server_name}**\n\n"
                f"检测到跨境网络拥堵：\n\n"
                f"### 📊 网络状态\n"
                f"- 延迟: {result.get('latency_avg', 0):.0f}ms\n"
                f"- 抖动: {result.get('jitter', 0):.0f}ms\n"
                f"- 丢包: {result.get('loss', 0):.0f}%\n\n"
                f"### ⚠️ 原因\n{reasons_str}"
            )

        log_message("WARN", "已发送 QoS 告警")

    def run(self):
        log_message("INFO", "=" * 50)
        log_message("INFO", "Traffic Padding 微服务启动")
        log_message("INFO", f"网卡: {self.config.get('interface')} | 比例: 1:{self.config.get('target_ratio')} | 配额: {self.config.get('max_daily_extra_gb')}GB")
        log_message("INFO", "=" * 50)

        self.running = True
        self.url_pool.refresh_pool()

        # 首次启动验证：下载 1MB 测试 URL 池可用性
        log_message("INFO", "执行首次启动验证...")
        verify_url = self.url_pool.get_random_url()
        verify_result = {'success': False, 'error': 'URL 池为空'}
        if verify_url:
            verify_result = self.downloader.execute_micro_task(verify_url, 1048576)  # 1MB
            if verify_result['success']:
                log_message("INFO", f"✓ URL 池验证通过 | 下载: {verify_result['bytes_downloaded'] / 1024:.0f}KB | "
                              f"耗时: {verify_result['duration']:.1f}s | 来源: {verify_url[:50]}")
            else:
                log_message("WARN", f"✗ URL 池验证失败: {verify_result['error']}")
        else:
            log_message("WARN", "✗ URL 池为空，等待下次刷新")

        # 发送启动通知（包含验证结果）
        verify_status = "✓ 验证通过" if verify_result.get('success') else "✗ 验证失败"
        if self.tg_notifier.enabled and self.notify_settings.get('service_start_stop', True):
            self.tg_notifier.send_message(
                f"🟢 <b>Traffic Padding 已启动</b>\n\n"
                f"🖥️ {self.server_name}\n\n"
                f"网卡: {self.config.get('interface')}\n"
                f"比例: 1:{self.config.get('target_ratio')}\n"
                f"日配额: {self.config.get('max_daily_extra_gb')} GB\n"
                f"URL: {self.url_pool.get_url_count()} 个\n"
                f"验证: {verify_status}\n"
                f"报告频率: {self.tg_notifier.report_freq}"
            )
        if self.dingtalk_notifier.enabled and self.notify_settings.get('service_start_stop', True):
            self.dingtalk_notifier.send_message(
                f"## 🟢 Traffic Padding 已启动\n\n"
                f"**🖥️ {self.server_name}**\n\n"
                f"- 网卡: {self.config.get('interface')}\n"
                f"- 比例: 1:{self.config.get('target_ratio')}\n"
                f"- 日配额: {self.config.get('max_daily_extra_gb')} GB\n"
                f"- URL: {self.url_pool.get_url_count()} 个\n"
                f"- 验证: {verify_status}\n"
                f"- 报告频率: {self.dingtalk_notifier.report_freq}"
            )

        try:
            while self.running:
                self.run_cycle()
        except KeyboardInterrupt:
            log_message("INFO", "收到停止信号")
        finally:
            self.running = False
            # 停止带宽监控
            if self.bandwidth_monitor:
                self.bandwidth_monitor.stop()
            # 停止 AI 分析器
            if self.ai_analyzer:
                self.ai_analyzer.stop()
            # 发送停止通知
            stats = self.downloader.get_stats()
            total_gb = self.total_downloaded_all_time / (1024 ** 3)
            if self.tg_notifier.enabled and self.notify_settings.get('service_start_stop', True):
                self.tg_notifier.send_message(
                    f"🔴 <b>Traffic Padding 已停止</b>\n\n"
                    f"🖥️ {self.server_name}\n\n"
                    f"周期: {self.cycle_count}\n"
                    f"任务: {stats['task_count']}\n"
                    f"本次下载: {stats['total_downloaded_mb']:.1f} MB\n"
                    f"累计总量: {total_gb:.3f} GB"
                )
            if self.dingtalk_notifier.enabled and self.notify_settings.get('service_start_stop', True):
                self.dingtalk_notifier.send_message(
                    f"## 🔴 Traffic Padding 已停止\n\n"
                    f"**🖥️ {self.server_name}**\n\n"
                    f"- 周期: {self.cycle_count}\n"
                    f"- 任务: {stats['task_count']}\n"
                    f"- 本次下载: {stats['total_downloaded_mb']:.1f} MB\n"
                    f"- 累计总量: {total_gb:.3f} GB"
                )
            self._log_stats()
            log_message("INFO", "服务已停止")


# ============================================================================
# 入口
# ============================================================================

def main():
    config_path = CONFIG_FILE

    if len(sys.argv) > 1:
        if sys.argv[1] in ('-h', '--help'):
            print(f"用法: python3 main.py [config_path]\n默认: {CONFIG_FILE}")
            sys.exit(0)
        config_path = sys.argv[1]

    interface = "eth0"
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                interface = json.load(f).get('interface', interface)
        except (json.JSONDecodeError, IOError):
            pass

    if not HealthChecker.run_all_checks(interface, config_path):
        log_message("ERROR", "健康检查失败")
        sys.exit(1)

    service = TrafficPaddingService(config_path)

    def sigterm_handler(signum, frame):
        log_message("INFO", "收到 SIGTERM，优雅退出...")
        service.running = False

    def sigusr1_handler(signum, frame):
        log_message("INFO", "收到 SIGUSR1，触发手动推送...")
        service.manual_report_requested = True

    def sigusr2_handler(signum, frame):
        log_message("INFO", "收到 SIGUSR2，触发手动 AI 分析...")
        if service.ai_analyzer:
            service.ai_analyzer.trigger_now()

    signal.signal(signal.SIGTERM, sigterm_handler)
    signal.signal(signal.SIGUSR1, sigusr1_handler)
    signal.signal(signal.SIGUSR2, sigusr2_handler)
    service.run()


if __name__ == "__main__":
    main()
