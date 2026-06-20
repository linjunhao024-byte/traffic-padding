#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# LIN-Padding - Traffic Padding Micro-Service
# 流量伪装微服务：全天候随机微量碎片填充，使上下行流量比例自然化

import calendar
import json
import os
import random
import signal
import struct
import sys
import syslog
import time
import urllib.request
import urllib.error
import urllib.parse
import ssl
from collections import deque
from datetime import datetime
from typing import Dict, List, Optional, Tuple

CONFIG_FILE = "/etc/traffic-padding/config.json"
USAGE_FILE = "/etc/traffic-padding/usage.json"
URL_POOL_REFRESH_INTERVAL = 86400
HTTP_TIMEOUT = 15
SLIDING_WINDOW_SIZE = 5
CONFIG_RELOAD_INTERVAL = 300
TG_CHECK_INTERVAL = 3600

COUNTER_MAX_32BIT = 0xFFFFFFFF
COUNTER_MAX_64BIT = 0xFFFFFFFFFFFFFFFF

USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
]

SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

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


def detect_system_counter_bits() -> int:
    return struct.calcsize("P") * 8


def calculate_counter_delta(prev: int, curr: int, max_val: int) -> int:
    if curr >= prev:
        return curr - prev
    # 计数器溢出处理
    overflow_delta = (max_val - prev) + curr + 1
    log_message("WARN", f"计数器溢出: prev={prev}, curr={curr}, delta={overflow_delta}")
    return overflow_delta


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
    def __init__(self, interface: str):
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
        try:
            with open('/proc/net/dev', 'r') as f:
                for line in f:
                    if ':' not in line:
                        continue
                    iface, data = line.split(':', 1)
                    if iface.strip() == self.interface:
                        fields = data.split()
                        return int(fields[0]), int(fields[8])
        except (IOError, ValueError, IndexError) as e:
            log_message("ERROR", f"读取 /proc/net/dev 失败: {e}")
        return None, None

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
        try:
            with open(self.health_file, 'w', encoding='utf-8') as f:
                json.dump(self.url_health, f, indent=2)
        except (IOError, OSError):
            pass

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

    # 国内大厂 CDN 直链
    def _fetch_domestic_big_files(self) -> List[str]:
        urls = [
            "https://dldir1.qq.com/weixin/Windows/WeChatSetup.exe",
            "https://dldir1.qq.com/qqfile/qq/QQNT/Windows/QQ_Release.exe",
            "https://dldir1.qq.com/invc/tt/QQBrowser_Setup_Wireless.exe",
            "https://dl.360safe.com/netlink/setup_360safe_netlink.exe",
            "https://dldir1.qq.com/music/clntupate/QQMusicSetup.exe",
            "https://dldir1.qq.com/qqtv/TencentVideo_V11.91.9144.0.exe",
            "https://cdn.aliyundcdntest.com/test_100m",
            "https://huaweicloud.obs.cn-north-1.myhuaweicloud.com/obs_test_10m",
        ]
        random.shuffle(urls)
        return urls[:3]

    # 必应中国每日图片
    def _fetch_bing_china(self) -> List[str]:
        urls = []
        try:
            req = urllib.request.Request(
                "https://cn.bing.com/HPImageArchive.aspx?format=js&idx=0&n=5",
                headers=self._get_request_headers()
            )
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=SSL_CONTEXT) as resp:
                for img in json.loads(resp.read().decode('utf-8')).get('images', []):
                    if img.get('url'):
                        urls.append(f"https://cn.bing.com{img['url']}")
        except Exception as e:
            log_message("WARN", f"必应 API 失败: {e}", throttle_key="bing_fail")
        return urls

    # Wikipedia（国内可能被墙）
    def _fetch_wikipedia_random(self) -> List[str]:
        urls = []
        try:
            req = urllib.request.Request(
                "https://en.wikipedia.org/api/rest_v1/page/random/summary",
                headers=self._get_request_headers()
            )
            with urllib.request.urlopen(req, timeout=8, context=SSL_CONTEXT) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                for key in ('thumbnail', 'originalimage'):
                    src = data.get(key, {}).get('source')
                    if src:
                        urls.append(src)
        except Exception as e:
            log_message("WARN", f"Wikipedia 失败: {e}", throttle_key="wiki_fail")
        return urls

    # 公共测速文件（备用）
    def _fetch_looking_glass(self) -> List[str]:
        urls = [
            "https://speed.hetzner.de/100MB.bin",
            "https://proof.ovh.net/files/10Mb.dat",
            "http://speedtest.tele2.net/10MB.zip",
            "http://speedtest.tele2.net/100MB.zip",
        ]
        random.shuffle(urls)
        return urls[:2]

    def refresh_pool(self) -> bool:
        log_message("INFO", "刷新 URL 池...")
        new_urls = []
        sources = [
            ("国内CDN", self._fetch_domestic_big_files),
            ("必应中国", self._fetch_bing_china),
            ("Wikipedia", self._fetch_wikipedia_random),
            ("LookingGlass", self._fetch_looking_glass),
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


# ============================================================================
# 微任务下载器（HTTP Range 切片）
# ============================================================================

class MicroTaskDownloader:
    def __init__(self, url_pool: URLPool):
        self.total_downloaded = 0
        self.task_count = 0
        self.url_pool = url_pool

    def execute_micro_task(self, url: str, target_bytes: int) -> Dict:
        start_time = time.time()
        result = {'success': False, 'bytes_downloaded': 0, 'duration': 0, 'error': None}

        try:
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Range": f"bytes=0-{target_bytes}",
                "Connection": "keep-alive",
            }
            req = urllib.request.Request(url, headers=headers)

            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=SSL_CONTEXT) as resp:
                if resp.status not in (200, 206):
                    result['error'] = f"HTTP {resp.status}"
                    self.url_pool.record_url_failure(url)
                    return result

                bytes_read = 0
                while bytes_read < target_bytes:
                    chunk = resp.read(min(8192, target_bytes - bytes_read))
                    if not chunk:
                        break
                    bytes_read += len(chunk)

                result['success'] = True
                result['bytes_downloaded'] = bytes_read
                self.total_downloaded += bytes_read
                self.task_count += 1
                self.url_pool.record_url_success(url)

        except urllib.error.HTTPError as e:
            result['error'] = f"HTTP {e.code}: {e.reason}"
            self.url_pool.record_url_failure(url)
        except urllib.error.URLError as e:
            result['error'] = f"URL Error: {e.reason}"
            self.url_pool.record_url_failure(url)
        except TimeoutError:
            result['error'] = "超时"
            self.url_pool.record_url_failure(url)
        except Exception as e:
            result['error'] = str(e)
            self.url_pool.record_url_failure(url)

        result['duration'] = time.time() - start_time
        return result

    def get_stats(self) -> Dict:
        return {
            'total_downloaded': self.total_downloaded,
            'task_count': self.task_count,
            'total_downloaded_mb': self.total_downloaded / (1024 * 1024)
        }


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
                        log_message("INFO", f"恢复配额: {self.daily_quota_used / (1024**3):.3f} GB")
                    else:
                        self._save_usage_to_disk()
        except (json.JSONDecodeError, IOError):
            self.daily_quota_used = 0

    def _save_usage_to_disk(self):
        try:
            os.makedirs(os.path.dirname(self.usage_file), exist_ok=True)
            with open(self.usage_file, 'w', encoding='utf-8') as f:
                json.dump({"date": self.current_date, "used_bytes": self.daily_quota_used}, f)
        except (IOError, OSError):
            pass

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
# Telegram 推送（日报/周报/月报）
# ============================================================================

class TelegramNotifier:
    def __init__(self, config: Config):
        self.config = config
        self.enabled = config.get('tg_enabled', False)
        self.bot_token = config.get('tg_bot_token', '')
        self.chat_id = config.get('tg_chat_id', '')
        self.report_freq = config.get('tg_report_freq', 'daily')
        self.monthly_reset_day = config.get('tg_monthly_reset_day', 1)
        self.monthly_quota_gb = config.get('monthly_quota_gb', 0)
        self.last_report_date = ""

    def send_message(self, text: str) -> bool:
        if not self.enabled or not self.bot_token or not self.chat_id:
            return False
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            data = json.dumps({"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}).encode('utf-8')
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15, context=SSL_CONTEXT) as resp:
                return resp.status == 200
        except Exception as e:
            log_message("WARN", f"TG 推送失败: {e}", throttle_key="tg_fail")
            return False

    def _should_report(self) -> bool:
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")

        if today == self.last_report_date:
            return False
        if self.report_freq == "daily":
            return True
        elif self.report_freq == "weekly":
            return now.weekday() == 0
        elif self.report_freq == "monthly":
            reset_day = self.monthly_reset_day
            current_day = now.day
            current_hour = now.hour
            if current_day == reset_day - 1 and current_hour >= 12:
                return True
            if reset_day == 1 and current_day >= 28 and current_hour >= 12:
                _, last_day = calendar.monthrange(now.year, now.month)
                if current_day == last_day:
                    return True
            return False
        return False

    def build_report(self, service: 'TrafficPaddingService') -> str:
        now = datetime.now()
        stats = service.downloader.get_stats()
        quota_used = service.scheduler.daily_quota_used
        daily_quota_gb = service.config.get('max_daily_extra_gb', 10)
        url_count = service.url_pool.get_url_count()

        monthly_usage_str = ""
        if self.monthly_quota_gb != 0:
            monthly_used_gb = stats['total_downloaded'] / (1024 ** 3)
            if self.monthly_quota_gb == -1:
                monthly_usage_str = f"\n\n📊 月流量统计\n├ 月总额度: 无限\n└ 已消耗: {monthly_used_gb:.3f} GB"
            elif self.monthly_quota_gb > 0:
                monthly_pct = (monthly_used_gb / self.monthly_quota_gb) * 100
                monthly_usage_str = f"\n\n📊 月额度使用\n├ 月总额度: {self.monthly_quota_gb:.1f} GB\n├ 已消耗: {monthly_used_gb:.3f} GB\n└ 占比: {monthly_pct:.2f}%"

        freq_label = {"daily": "日报", "weekly": "周报", "monthly": "月报"}.get(self.report_freq, "报告")

        return f"""📋 <b>Traffic Padding {freq_label}</b>
━━━━━━━━━━━━━━━━━━━━

🕐 {now.strftime("%Y-%m-%d %H:%M")}

🖥 服务状态
├ 周期: {service.cycle_count}
├ URL: {url_count} 个
└ 时长: {self._format_uptime(service)}

📈 流量统计
├ 任务: {stats['task_count']}
├ 下载: {stats['total_downloaded_mb']:.1f} MB
└ 配额: {quota_used / (1024**3):.3f} / {daily_quota_gb:.1f} GB{monthly_usage_str}

⚙️ 配置
├ 网卡: {service.config.get('interface')}
├ 比例: 1:{service.config.get('target_ratio')}
└ 权重: {service.scheduler.get_time_weight():.2f}x"""

    def _format_uptime(self, service: 'TrafficPaddingService') -> str:
        seconds = service.cycle_count * 30
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if hours > 24:
            return f"{hours // 24}天{hours % 24}小时"
        return f"{hours}小时{minutes}分钟"

    def check_and_send(self, service: 'TrafficPaddingService'):
        if not self.enabled:
            return
        if self._should_report():
            report = self.build_report(service)
            if self.send_message(report):
                self.last_report_date = datetime.now().strftime("%Y-%m-%d")
                log_message("INFO", f"TG {self.report_freq} 报告已发送")
            else:
                log_message("WARN", "TG 报告发送失败")


# ============================================================================
# 钉钉机器人推送（日报/周报/月报）
# ============================================================================

class DingTalkNotifier:
    def __init__(self, config: Config):
        self.config = config
        self.enabled = config.get('dingtalk_enabled', False)
        self.webhook_url = config.get('dingtalk_webhook', '')
        self.secret = config.get('dingtalk_secret', '')
        self.report_freq = config.get('dingtalk_report_freq', 'daily')
        self.monthly_reset_day = config.get('dingtalk_monthly_reset_day', 1)
        self.monthly_quota_gb = config.get('monthly_quota_gb', 0)
        self.last_report_date = ""

    def _get_sign_url(self) -> str:
        """生成加签 URL（如果配置了 secret）"""
        if not self.secret:
            return self.webhook_url
        import hashlib
        import hmac
        import base64
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
            # 钉钉 markdown 消息格式
            data = json.dumps({
                "msgtype": "markdown",
                "markdown": {
                    "title": "Traffic Padding 报告",
                    "text": text
                }
            }).encode('utf-8')
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15, context=SSL_CONTEXT) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                return result.get('errcode') == 0
        except Exception as e:
            log_message("WARN", f"钉钉推送失败: {e}", throttle_key="dingtalk_fail")
            return False

    def _should_report(self) -> bool:
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")

        if today == self.last_report_date:
            return False
        if self.report_freq == "daily":
            return True
        elif self.report_freq == "weekly":
            return now.weekday() == 0
        elif self.report_freq == "monthly":
            reset_day = self.monthly_reset_day
            current_day = now.day
            current_hour = now.hour
            if current_day == reset_day - 1 and current_hour >= 12:
                return True
            if reset_day == 1 and current_day >= 28 and current_hour >= 12:
                _, last_day = calendar.monthrange(now.year, now.month)
                if current_day == last_day:
                    return True
            return False
        return False

    def build_report(self, service: 'TrafficPaddingService') -> str:
        now = datetime.now()
        stats = service.downloader.get_stats()
        quota_used = service.scheduler.daily_quota_used
        daily_quota_gb = service.config.get('max_daily_extra_gb', 10)
        url_count = service.url_pool.get_url_count()

        monthly_usage_str = ""
        if self.monthly_quota_gb != 0:
            monthly_used_gb = stats['total_downloaded'] / (1024 ** 3)
            if self.monthly_quota_gb == -1:
                monthly_usage_str = f"\n\n### 📊 月流量统计\n- 月总额度: 无限\n- 已消耗: {monthly_used_gb:.3f} GB"
            elif self.monthly_quota_gb > 0:
                monthly_pct = (monthly_used_gb / self.monthly_quota_gb) * 100
                monthly_usage_str = f"\n\n### 📊 月额度使用\n- 月总额度: {self.monthly_quota_gb:.1f} GB\n- 已消耗: {monthly_used_gb:.3f} GB\n- 占比: {monthly_pct:.2f}%"

        freq_label = {"daily": "日报", "weekly": "周报", "monthly": "月报"}.get(self.report_freq, "报告")

        return f"""## 📋 Traffic Padding {freq_label}

---

🕐 **{now.strftime("%Y-%m-%d %H:%M")}**

### 🖥 服务状态
- 周期: {service.cycle_count}
- URL: {url_count} 个
- 时长: {self._format_uptime(service)}

### 📈 流量统计
- 任务: {stats['task_count']}
- 下载: {stats['total_downloaded_mb']:.1f} MB
- 配额: {quota_used / (1024**3):.3f} / {daily_quota_gb:.1f} GB{monthly_usage_str}

### ⚙️ 配置
- 网卡: {service.config.get('interface')}
- 比例: 1:{service.config.get('target_ratio')}
- 权重: {service.scheduler.get_time_weight():.2f}x"""

    def _format_uptime(self, service: 'TrafficPaddingService') -> str:
        seconds = service.cycle_count * 30
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if hours > 24:
            return f"{hours // 24}天{hours % 24}小时"
        return f"{hours}小时{minutes}分钟"

    def check_and_send(self, service: 'TrafficPaddingService'):
        if not self.enabled:
            return
        if self._should_report():
            report = self.build_report(service)
            if self.send_message(report):
                self.last_report_date = datetime.now().strftime("%Y-%m-%d")
                log_message("INFO", f"钉钉 {self.report_freq} 报告已发送")
            else:
                log_message("WARN", "钉钉报告发送失败")


# ============================================================================
# 启动健康检查
# ============================================================================

class HealthChecker:
    @staticmethod
    def check_interface(interface: str) -> bool:
        try:
            with open('/proc/net/dev', 'r') as f:
                return any(f"{interface}:" in line for line in f)
        except IOError:
            return False

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
        for url in ["https://www.baidu.com", "https://www.aliyun.com", "https://www.google.com", "https://www.cloudflare.com"]:
            try:
                req = urllib.request.Request(url, method='HEAD')
                req.add_header('User-Agent', random.choice(USER_AGENTS))
                with urllib.request.urlopen(req, timeout=5, context=SSL_CONTEXT) as resp:
                    if resp.status < 400:
                        return True
            except Exception:
                continue
        return True

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
        self.monitor = TrafficMonitor(self.config.get('interface', 'eth0'))
        self.url_pool = URLPool()
        self.downloader = MicroTaskDownloader(self.url_pool)
        self.scheduler = Scheduler(self.config)
        self.tg_notifier = TelegramNotifier(self.config)
        self.dingtalk_notifier = DingTalkNotifier(self.config)
        self.running = False
        self.cycle_count = 0
        self.last_tg_check = 0
        self.first_task_done = False  # 首次任务完成标志

    def _send_notification(self, text: str):
        """同时发送到 TG 和钉钉"""
        if self.tg_notifier.enabled:
            self.tg_notifier.send_message(text)
        if self.dingtalk_notifier.enabled:
            self.dingtalk_notifier.send_message(text)

    def _log_stats(self):
        stats = self.downloader.get_stats()
        log_message("INFO", "=" * 50)
        log_message("INFO", f"周期 #{self.cycle_count} | URL: {self.url_pool.get_url_count()} | "
                      f"任务: {stats['task_count']} | 流量: {stats['total_downloaded_mb']:.1f}MB | "
                      f"配额: {self.scheduler.daily_quota_used / (1024**3):.3f}/{self.config.get('max_daily_extra_gb', 10)}GB")
        log_message("INFO", "=" * 50)

    def run_cycle(self):
        self.cycle_count += 1
        self.config.check_and_reload()

        traffic_stats = self.monitor.get_traffic_stats()
        should_run, target_bytes = self.scheduler.should_execute_task(traffic_stats)

        if should_run:
            url = self.url_pool.get_random_url()
            if url:
                result = self.downloader.execute_micro_task(url, target_bytes)
                if result['success']:
                    self.scheduler.record_usage(result['bytes_downloaded'])
                    log_message("INFO", f"下载 {result['bytes_downloaded'] / (1024*1024):.1f}MB 耗时 {result['duration']:.1f}s")

                    # 首次任务完成时发送数据推送测试消息（仅一次）
                    if not self.first_task_done:
                        self.first_task_done = True
                        # 确定推送渠道和频率
                        if self.tg_notifier.enabled:
                            freq_label = {"daily": "日报", "weekly": "周报", "monthly": "月报"}.get(self.tg_notifier.report_freq, "报告")
                            self.tg_notifier.send_message(
                                f"🧪 <b>【数据推送测试消息】</b>\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                                f"✅ 首次下载任务已完成\n\n"
                                f"📊 任务详情\n"
                                f"├ 下载量: {result['bytes_downloaded'] / (1024*1024):.1f} MB\n"
                                f"├ 耗时: {result['duration']:.1f}s\n"
                                f"└ 来源: {url[:40]}...\n\n"
                                f"⚙️ 推送设置\n"
                                f"├ 频率: {freq_label}\n"
                                f"└ 状态: 正常运行中\n\n"
                                f"💡 这是一次性测试消息，后续将按 [{freq_label}] 频率自动推送。"
                            )
                        if self.dingtalk_notifier.enabled:
                            freq_label = {"daily": "日报", "weekly": "周报", "monthly": "月报"}.get(self.dingtalk_notifier.report_freq, "报告")
                            self.dingtalk_notifier.send_message(
                                f"## 🧪 【数据推送测试消息】\n\n---\n\n"
                                f"✅ 首次下载任务已完成\n\n"
                                f"### 📊 任务详情\n"
                                f"- 下载量: {result['bytes_downloaded'] / (1024*1024):.1f} MB\n"
                                f"- 耗时: {result['duration']:.1f}s\n"
                                f"- 来源: {url[:40]}...\n\n"
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

        # 定期报告检查
        now = time.time()
        if now - self.last_tg_check >= TG_CHECK_INTERVAL:
            self.last_tg_check = now
            self.tg_notifier.check_and_send(self)
            self.dingtalk_notifier.check_and_send(self)

        time.sleep(self.scheduler.calculate_jitter_sleep())

    def run(self):
        log_message("INFO", "=" * 50)
        log_message("INFO", "Traffic Padding 微服务启动")
        log_message("INFO", f"网卡: {self.config.get('interface')} | 比例: 1:{self.config.get('target_ratio')} | 配额: {self.config.get('max_daily_extra_gb')}GB")
        log_message("INFO", "=" * 50)

        self.running = True
        self.url_pool.refresh_pool()

        # 发送启动通知
        if self.tg_notifier.enabled:
            self.tg_notifier.send_message(
                f"🟢 <b>Traffic Padding 已启动</b>\n"
                f"网卡: {self.config.get('interface')}\n"
                f"比例: 1:{self.config.get('target_ratio')}\n"
                f"日配额: {self.config.get('max_daily_extra_gb')} GB\n"
                f"报告频率: {self.tg_notifier.report_freq}"
            )
        if self.dingtalk_notifier.enabled:
            self.dingtalk_notifier.send_message(
                f"## 🟢 Traffic Padding 已启动\n\n"
                f"- 网卡: {self.config.get('interface')}\n"
                f"- 比例: 1:{self.config.get('target_ratio')}\n"
                f"- 日配额: {self.config.get('max_daily_extra_gb')} GB\n"
                f"- 报告频率: {self.dingtalk_notifier.report_freq}"
            )

        try:
            while self.running:
                self.run_cycle()
        except KeyboardInterrupt:
            log_message("INFO", "收到停止信号")
        finally:
            self.running = False
            # 发送停止通知
            stats = self.downloader.get_stats()
            if self.tg_notifier.enabled:
                self.tg_notifier.send_message(
                    f"🔴 <b>Traffic Padding 已停止</b>\n"
                    f"周期: {self.cycle_count}\n"
                    f"下载: {stats['total_downloaded_mb']:.1f} MB\n"
                    f"任务: {stats['task_count']}"
                )
            if self.dingtalk_notifier.enabled:
                self.dingtalk_notifier.send_message(
                    f"## 🔴 Traffic Padding 已停止\n\n"
                    f"- 周期: {self.cycle_count}\n"
                    f"- 下载: {stats['total_downloaded_mb']:.1f} MB\n"
                    f"- 任务: {stats['task_count']}"
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

    signal.signal(signal.SIGTERM, sigterm_handler)
    service.run()


if __name__ == "__main__":
    main()
