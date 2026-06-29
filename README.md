<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=0:0f0f0f,50:fce300,100:00f0ff&height=220&section=header&text=Traffic-Padding&fontSize=50&fontColor=fce300&fontAlignY=35&desc=Traffic%20Padding%20Micro-Service&descSize=15&descColor=00f0ff&descAlignY=55&animation=twinkling" width="100%"/>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-v1.2.2-blue?style=flat-square" alt="Version">
  <a href="https://github.com/linjunhao024-byte/Traffic-Tadding/stargazers"><img src="https://img.shields.io/github/stars/linjunhao024-byte/Traffic-Tadding?style=flat-square&color=yellow&logo=github&cacheSeconds=60" alt="Stars"></a>
  <a href="https://github.com/linjunhao024-byte/Traffic-Tadding/network/members"><img src="https://img.shields.io/github/forks/linjunhao024-byte/Traffic-Tadding?style=flat-square&color=blue&logo=github&cacheSeconds=60" alt="Forks"></a>
  <a href="https://github.com/linjunhao024-byte/Traffic-Tadding/issues"><img src="https://img.shields.io/github/issues/linjunhao024-byte/Traffic-Tadding?style=flat-square&color=red&logo=github&cacheSeconds=60" alt="Issues"></a>
  <a href="https://github.com/linjunhao024-byte/Traffic-Tadding/blob/main/LICENSE"><img src="https://img.shields.io/github/license/linjunhao024-byte/Traffic-Tadding?style=flat-square&color=brightgreen&cacheSeconds=60" alt="License"></a>
  <img src="https://img.shields.io/badge/Python-3.6+-blue?style=flat-square&logo=python" alt="Python">
  <img src="https://img.shields.io/badge/Dependencies-Zero-brightgreen?style=flat-square" alt="Zero Deps">
</p>

<p align="center">
  专为国内代理/中转服务器设计，全天候平滑化流量特征，告别 1:1 封锁风险。
</p>

<p align="center">
  <b>喜欢这个项目？请点击右上角的 ⭐️ Star 以示支持！</b>
</p>

<p align="center">
  <a href="https://github.com/linjunhao024-byte/Traffic-Tadding/issues">🔗 提交 Issue</a> | <a href="https://github.com/linjunhao024-byte/Traffic-Tadding">📖 查看源码</a>
</p>

---

## 🎯 解决的问题

国内云服务器（代理/中转机）的上下行流量比例过于对等（1:1），容易被防火墙识别。

**解决方案**：全天候随机微量碎片填充（Micro-padding），主动发起微量下载，人为制造自然的下行流量特征。

---

## 💻 系统兼容性

| 系统 | 版本 | 状态 |
|------|------|------|
| Ubuntu | 16.04+ | ✅ 支持 |
| Debian | 8+ | ✅ 支持 |
| CentOS | 7+ | ✅ 支持 |
| RHEL | 7+ | ✅ 支持 |
| Fedora | 全版本 | ✅ 支持 |
| Arch Linux | 全版本 | ✅ 支持 |
| AlmaLinux / Rocky | 全版本 | ✅ 支持 |

**核心依赖：**
- `systemd` - 服务管理
- `python3` - 运行环境（3.6+）
- `/proc/net/dev` - Linux 内核接口

> ⚠️ 不支持 macOS、Windows、Alpine Linux 及使用 SysVinit 的旧系统

---

## ⚡ 一键安装

复制下方对应网络环境的命令，粘贴到服务器终端按 `Enter` 即可开始安装。

**🌍 国际 / 海外节点（官方直连）：**

```bash
cd /tmp && wget -q https://raw.githubusercontent.com/linjunhao024-byte/Traffic-Tadding/main/install.sh && wget -q https://raw.githubusercontent.com/linjunhao024-byte/Traffic-Tadding/main/main.py && sudo bash install.sh
```

**🇨🇳 中国大陆服务器（镜像加速）：**

```bash
cd /tmp && wget -q https://ghfast.top/https://raw.githubusercontent.com/linjunhao024-byte/Traffic-Tadding/main/install.sh && wget -q https://ghfast.top/https://raw.githubusercontent.com/linjunhao024-byte/Traffic-Tadding/main/main.py && sudo bash install.sh
```

> 如果上述镜像不可用，可尝试其他镜像：`ghproxy.cc`、`gh.ddlc.top`、`gh-proxy.com`，或手动下载后上传到服务器 `/tmp` 目录。

**⚠️ 更新或重新安装时**，请先删除旧文件再下载（避免使用缓存的旧版本）：

```bash
cd /tmp && rm -f install.sh main.py && wget -q https://ghfast.top/https://raw.githubusercontent.com/linjunhao024-byte/Traffic-Tadding/main/install.sh && wget -q https://ghfast.top/https://raw.githubusercontent.com/linjunhao024-byte/Traffic-Tadding/main/main.py && sudo bash install.sh
```

---

## 📋 安装过程

安装脚本会引导你完成 6 项配置：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| 服务器名称 | 自定义名称，显示在推送消息中 | Realm中转服务器 |
| 网卡 | 自动检测，回车确认 | eth0 |
| 流量比例 | (a)1:2 (b)1:3 (c)1:4 (d)1:5 | b (1:3) |
| 每日配额 | 最大额外下载量 | 10 GB |
| 月流量额度 | 服务器月流量总额度（用于计算占比） | 0（禁用） |
| 消息推送 | 钉钉机器人（推荐）或 Telegram | N（禁用） |
| 报告频率 | 日报/周报/月报 | 日报 |
| 推送时间 | 24小时制 | 23:00 |
| 管理命令 | 快捷命令名称（1-3字符） | tp |

> ⚠️ **国内服务器提示**：如果服务器在中国大陆，Telegram API (`api.telegram.org`) 可能无法访问，建议选择钉钉机器人。

确认配置时可以：
- 输入 `Y` 确认安装
- 输入 `1/2/3/4/5` 返回修改对应配置
- 输入 `N` 取消安装

---

## 🎮 管理

安装时设置的管理命令（默认 `tp`）呼出交互式管理菜单：

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║  🚦 Traffic Padding Manager                                                  ║
╚═══════════════════════════════════════════════════════════════════════════════╝

┌───────────────────────────────────────────────────────────────────────────────┐
│  状态: ● 运行中   自启: 已启用                                                │
│  网卡: eth0  比例: 1:3  配额: 10GB                                            │
│  今日: 0.123 GB                                                               │
└───────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────┬─────────────────────┬─────────────────────┐
│  服务控制             日志查看               配置管理              │
├─────────────────────┼─────────────────────┼─────────────────────┤
│  [1] 查看状态        [5] 实时日志            [8] 查看配置          │
│  [2] 启动服务        [6] 最近日志            [9] 编辑配置          │
│  [3] 停止服务        [7] 手动推送                                  │
│  [4] 重启服务                                                    │
├─────────────────────┴─────────────────────┴─────────────────────┤
│  系统管理                                                        │
├─────────────────────────────────────────────────────────────────┤
│  [10] 开机自启      [12] 卸载        [14] 自动面板               │
│  [11] 网卡与下载    [13] 一键更新    [15] 流量与带宽              │
│  [16] 告警设置      [17] AI 分析     [18] 清空记录               │
├─────────────────────────────────────────────────────────────────┤
│  [0] 退出         [A] 关于                                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📁 文件结构

```
GitHub 仓库                        服务器安装后
Traffic-Tadding/                   /opt/traffic-padding/
├── install.sh  ──────────────────►├── main.py
├── main.py                        └── tpm.sh (自动生成)
└── README.md                           │
                                   /usr/local/bin/tpm (快捷命令)
                                        │
                                   /etc/traffic-padding/
                                   ├── config.json
                                   ├── usage.json
                                   ├── url_health.json
                                   ├── notify.json           ← 通知开关配置
                                   ├── ai_analysis.json      ← AI 分析缓存
                                   ├── stats.json            ← 历史统计
                                   ├── traffic_history.json  ← 流量历史
                                   ├── qos_stats.json        ← QoS 探测历史
                                   └── logs/                 ← 带宽监控 CSV 日志
                                       ├── bandwidth_20260626.csv
                                       └── ...
```

---

## ⚙️ 配置说明

`/etc/traffic-padding/config.json` — **修改后 5 分钟自动生效，无需重启**

```json
{
    "server_name": "Realm中转服务器",   // 服务器名称（显示在推送消息中）
    "interface": "eth0",              // 监控网卡
    "target_ratio": 3.0,              // 下行:上行比例 (2/3/4/5)
    "max_daily_extra_gb": 10.0,       // 每日配额 (GB)
    "monthly_quota_gb": 100,          // 月流量额度: 0=禁用, -1=无限, 正数=具体额度
    "min_task_bytes": 2097152,         // 最小任务 (2MB)
    "max_task_bytes": 15728640,        // 最大任务 (15MB)
    "jitter_base": 5,                 // 基础休眠 (秒)
    "jitter_range": 25,               // 随机抖动范围
    "enable_night_mode": true,        // 凌晨 2-5 点降频
    "night_multiplier": 5.0,          // 降频倍数
    "peak_hours": [19, 20, 21, 22],   // 晚高峰时段
    "peak_multiplier": 0.6,           // 高峰加速倍数
    "tg_enabled": false,              // 启用 TG 推送
    "tg_bot_token": "",               // TG Bot Token
    "tg_chat_id": "",                 // TG Chat ID
    "tg_report_freq": "daily",        // 报告频率: daily/weekly/monthly
    "tg_report_hour": 23,             // 推送时间 (24小时制)
    "tg_monthly_reset_day": 1,        // 月额度重置日（月报用）
    "dingtalk_enabled": false,        // 启用钉钉推送
    "dingtalk_webhook": "",           // 钉钉机器人 Webhook URL
    "dingtalk_secret": "",            // 钉钉加签密钥（可选）
    "dingtalk_report_freq": "daily",  // 报告频率: daily/weekly/monthly
    "dingtalk_report_hour": 23,       // 推送时间 (24小时制)
    "dingtalk_monthly_reset_day": 1,  // 月额度重置日（月报用）
    "qos_probe_enabled": true,        // 启用 QoS 探测（TCP ping，国内外分离）

    // ── 带宽监控 ──
    "monitor_enabled": true,          // 启用带宽监控线程（1秒采样，1分钟写CSV）
    "alert_enabled": false,           // 启用带宽告警
    "alert_threshold_mbps": 50,       // 告警阈值（Mbps）
    "alert_cooldown": 180,            // 告警冷却时间（秒）
    "alert_recovery": true,           // 带宽恢复时发送通知
    "csv_log_dir": "/etc/traffic-padding/logs",  // CSV 日志目录

    // ── AI 分析 ──
    "ai_enabled": false,              // 启用 AI 分析（每小时自动调用）
    "ai_api_key": "",                 // API Key（安装时填写或菜单中配置）
    "ai_base_url": "https://api.openai.com/v1",  // API 地址（OpenAI 兼容）
    "ai_model": "gpt-4o-mini"        // 模型名称
}
```

---

## 📱 消息推送（可选）

安装时可选择启用消息推送，支持 **钉钉机器人**（推荐国内服务器）和 **Telegram** 两种方式。

> ⚠️ 如果服务器在中国大陆，Telegram API (`api.telegram.org`) 可能无法访问，建议选择钉钉机器人。

### 钉钉机器人（推荐）

**前置准备：**

1. 打开钉钉群 → 群设置 → 智能群助手 → 添加机器人 → 自定义
2. 安全设置建议选择「自定义关键词」，填写：`Traffic Padding`
3. 复制 Webhook URL，安装时粘贴即可
4. （可选）如需更高安全性，可启用「加签」并复制密钥

### Telegram

**前置准备：**

1. 在 Telegram 搜索 `@BotFather`，创建机器人获取 Token
2. 在 Telegram 搜索 `@userinfobot`，获取你的 Chat ID
3. 安装时输入 Token 和 Chat ID 即可

### 推送内容示例

```
📋 Traffic Padding 日报
━━━━━━━━━━━━━━━━━━━━

🖥️ Realm中转服务器

🕐 2026-06-22 23:00

📊 带宽监控
├ 入站峰值: 85.2 Mbps (14:32)
├ 出站峰值: 42.1 Mbps (09:15)
├ 入站平均: 12.3 Mbps
├ 出站平均: 8.7 Mbps
├ 总流量: RX 1.20 GB / TX 0.80 GB
└ 告警: 0 次

📦 流量填充
├ 今日: 0.123 GB
├ 累计总量: 12.345 GB
├ 今日配额: 0.568 / 10.0 GB

📈 下载性能
├ 平均速度: 2.50 MB/s
├ 最快来源: 腾讯 (5.2 MB/s)
└ 最慢来源: Cloudflare (0.8 MB/s)

📊 流量对比
├ 实际 RX: 150.0 MB
├ 实际 TX: 80.0 MB
├ 填充下载: 45.0 MB
└ 填充占比: 30.0%

🔗 URL 状态
├ 总数: 10 个
├ 健康: 8/10
├ 成功: 150 次
└ 失败: 3 次
  ├ 超时: 2 次
  └ HTTP 404: 1 次

📈 运行状态
├ 周期: 1234
└ 时长: 10小时17分钟

⚙️ 配置
├ 网卡: eth0
├ 比例: 1:3
└ 权重: 1.00x

🤖 AI 分析
入站流量峰值 85.2Mbps 偏高，出站流量正常。
建议关注入站流量来源，可能是突发访问或爬虫。
```

### 报告频率

| 频率 | 说明 |
|------|------|
| 日报 | 每天 23:00 发送 |
| 周报 | 每周一 23:00 发送 |
| 月报 | 月额度重置日前 12 小时发送 |

---

## 🔧 技术特性

| 特性 | 说明 |
|------|------|
| 零依赖 | 仅使用 Python 标准库 |
| 极低资源 | CPU 最低优先级，内存上限 50MB |
| 国内优先 | 优先使用腾讯/阿里/华为 CDN |
| 健康检查 | URL 成功率追踪，自动降权失败源 |
| 配置热重载 | 修改配置 5 分钟内自动生效 |
| 滑动窗口 | 流量统计平滑，减少误判 |
| 溢出检测 | 兼容 32 位系统计数器溢出 |
| 优雅退出 | 支持 SIGTERM 信号处理 |
| 消息推送 | 支持钉钉机器人和 Telegram，日报/周报/月报 |
| 速度统计 | 追踪下载速度，显示最快/最慢来源 |
| 错误统计 | 记录失败类型，便于排查问题 |
| 流量对比 | 显示实际流量 vs 填充流量占比 |
| 配额预测 | 预估配额用完时间 |
| 手动推送 | 管理菜单一键推送当前状态 |
| QoS 探测 | 检测跨境网络拥堵，自动告警 |
| 带宽监控 | 1 秒采样，1 分钟写 CSV，峰值/均值/流量统计 |
| 实时告警 | 带宽超阈值钉钉推送，冷却+恢复通知 |
| AI 分析 | 每小时自动调用 DeepSeek 分析流量，结果注入报告 |
| 通知管理 | 8 种通知独立开关，tpm 菜单可视化管理 |
| 周报清日志 | 周报发送成功后自动清理 7 天 CSV 日志 |
| 版本检查 | 一键更新自动对比本地和 GitHub 版本 |
| 清空记录 | 分类清空统计/日志/历史，带确认提示 |
| 告警记录 | 记录每次告警的开始/结束/持续/峰值 |
| TCP QoS | TCP ping 探测，国内外分离统计 |

---

## 🗑️ 卸载

**方式 1: 管理菜单（推荐）**

```bash
tp
# 选择 [12] ⚠️ 卸载
```

**方式 2: 命令行**

```bash
sudo bash install.sh uninstall
```

---

## 💬 反馈与支持

- 🐛 [提交 Bug](https://github.com/linjunhao024-byte/Traffic-Tadding/issues)
- 💡 [功能建议](https://github.com/linjunhao024-byte/Traffic-Tadding/issues)
- ⭐ [给个 Star](https://github.com/linjunhao024-byte/Traffic-Tadding)

---

## 📄 License

MIT
