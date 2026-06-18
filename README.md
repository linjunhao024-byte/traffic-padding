<p align="center">
  <h1 align="center">🛡️ Traffic Padding Micro-Service</h1>
  <p align="center">流量伪装微服务 — 使云服务器上下行流量比例自然化</p>
</p>

<p align="center">
  <a href="https://github.com/linjunhao024-byte/traffic-padding/stargazers"><img src="https://img.shields.io/github/stars/linjunhao024-byte/traffic-padding?style=flat-square&color=yellow&logo=github" alt="Stars"></a>
  <a href="https://github.com/linjunhao024-byte/traffic-padding/network/members"><img src="https://img.shields.io/github/forks/linjunhao024-byte/traffic-padding?style=flat-square&color=blue&logo=github" alt="Forks"></a>
  <a href="https://github.com/linjunhao024-byte/traffic-padding/issues"><img src="https://img.shields.io/github/issues/linjunhao024-byte/traffic-padding?style=flat-square&color=red&logo=github" alt="Issues"></a>
  <a href="https://github.com/linjunhao024-byte/traffic-padding/blob/main/LICENSE"><img src="https://img.shields.io/github/license/linjunhao024-byte/traffic-padding?style=flat-square&color=brightgreen&v=1" alt="License"></a>
  <img src="https://img.shields.io/badge/Python-3.6+-blue?style=flat-square&logo=python" alt="Python">
  <img src="https://img.shields.io/badge/Dependencies-Zero-brightgreen?style=flat-square" alt="Zero Deps">
</p>

```text
    __     _____   __  __             ____                        __        __
   / /    /  _/   / | / /            / __ \   ____    ____/ /   ____/ /   (_)   ____       ____
  / /     / /    /  |/ /   ______   / /_/ /  / __ `/  / __  /   / __  /   / /   / __ \     / __ `/
 / /___  _/ /    / /|  /   /_____/  / .___/  / /_/ /  / /_/ /   / /_/ /   / /   / / / /    / /_/ /
/_____/ /___/   /_/ |_/            /_/       \__,_/   \__,_/    \__,_/   /_/   /_/ /_/     \__, /
                                                                                           /____/

                        Traffic Padding Micro-Service
                            流量伪装微服务
```

<p align="center">
  专为国内代理/中转服务器设计，全天候平滑化流量特征，告别 1:1 封锁风险。
</p>

<p align="center">
  <b>喜欢这个项目？请点击右上角的 ⭐️ Star 以示支持！</b>
</p>

<p align="center">
  <a href="https://github.com/linjunhao024-byte/traffic-padding/issues">🔗 提交 Issue</a> | <a href="https://github.com/linjunhao024-byte/traffic-padding">📖 查看源码</a>
</p>

---

## 🎯 解决的问题

国内云服务器（代理/中转机）的上下行流量比例过于对等（1:1），容易被防火墙识别。

**解决方案**：全天候随机微量碎片填充（Micro-padding），主动发起微量下载，人为制造自然的下行流量特征。

---

## ⚡ 一键安装

复制下方对应网络环境的命令，粘贴到服务器终端按 `Enter` 即可开始安装。

**🌍 国际 / 海外节点（官方直连）：**

```bash
cd /tmp && wget -q https://raw.githubusercontent.com/linjunhao024-byte/traffic-padding/main/install.sh && wget -q https://raw.githubusercontent.com/linjunhao024-byte/traffic-padding/main/main.py && sudo bash install.sh
```

**🇨🇳 中国大陆服务器（镜像加速）：**

```bash
cd /tmp && wget -q https://ghp.ci/https://raw.githubusercontent.com/linjunhao024-byte/traffic-padding/main/install.sh && wget -q https://ghp.ci/https://raw.githubusercontent.com/linjunhao024-byte/traffic-padding/main/main.py && sudo bash install.sh
```

---

## 📋 安装过程

安装脚本会引导你完成 4 项配置：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| 网卡 | 自动检测，回车确认 | eth0 |
| 流量比例 | 下行:上行 | 1:3 |
| 每日配额 | 最大额外下载量 | 10 GB |
| 月流量额度 | 服务器月流量总额度（用于计算占比） | 0（禁用） |
| TG 推送 | Telegram 机器人消息推送 | N（禁用） |
| 报告频率 | 日报/周报/月报 | 日报 |
| 管理命令 | 快捷命令名称（1-3字符） | tp |

确认配置时可以：
- 输入 `Y` 确认安装
- 输入 `1/2/3/4` 返回修改对应配置
- 输入 `N` 取消安装

---

## 🎮 管理

安装时设置的管理命令（默认 `tp`）呼出交互式管理菜单：

```
╔══════════════════════════════════════════════════════════════╗
║          Traffic Padding Manager (tpm)                      ║
╚══════════════════════════════════════════════════════════════╝

状态: 运行中  自启: 已启用
网卡:eth0 比例:1:3 配额:10GB
今日: 0.123GB

服务控制       日志           配置           系统
  [1] 查看状态    [5] 实时日志    [7] 查看配置    [9] 开机自启
  [2] 启动服务    [6] 最近日志    [8] 编辑配置   [10] 取消自启
  [3] 停止服务                               [11] 网卡测试
  [4] 重启服务                               [12] ⚠️ 卸载

  [0] 退出
```

---

## 📁 文件结构

```
GitHub 仓库                        服务器安装后
traffic-padding/                   /opt/traffic-padding/
├── install.sh  ──────────────────►├── main.py
├── main.py                        └── tpm.sh (自动生成)
└── README.md                           │
                                   /usr/local/bin/tpm (快捷命令)
                                        │
                                   /etc/traffic-padding/
                                   ├── config.json
                                   ├── usage.json
                                   └── url_health.json
```

---

## ⚙️ 配置说明

`/etc/traffic-padding/config.json` — **修改后 5 分钟自动生效，无需重启**

```json
{
    "interface": "eth0",              // 监控网卡
    "target_ratio": 3.0,              // 下行:上行比例
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
    "tg_monthly_reset_day": 1         // 月额度重置日（月报用）
}
```

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
| TG 推送 | 日报/周报/月报，支持月额度占比统计 |

---

## 📱 TG 消息推送（可选）

安装时可选择启用 Telegram 机器人推送，接收服务运行状态报告。

**推送内容示例：**

```
📋 Traffic Padding 日报
━━━━━━━━━━━━━━━━━━━━

🕐 时间: 2024-01-15 23:00

🖥 服务状态
├ 运行周期: 1234
├ URL 池: 8 个
└ 运行时长: 10小时17分钟

📈 流量统计
├ 任务数: 45
├ 总下载: 567.8 MB
└ 今日配额: 0.568 / 10.0 GB

📊 月额度使用
├ 月总额度: 100 GB
├ 已消耗: 12.345 GB
└ 占比: 12.35%

⚙️ 配置
├ 网卡: eth0
├ 比例: 1:3
└ 时间权重: 1.00x
```

**前置准备：**

1. 在 Telegram 搜索 `@BotFather`，创建机器人获取 Token
2. 在 Telegram 搜索 `@userinfobot`，获取你的 Chat ID
3. 安装时输入 Token 和 Chat ID 即可

**报告频率：**

| 频率 | 说明 |
|------|------|
| 日报 | 每天 23:00 发送 |
| 周报 | 每周一 23:00 发送 |
| 月报 | 月额度重置日前 12 小时发送 |

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

- 🐛 [提交 Bug](https://github.com/linjunhao024-byte/traffic-padding/issues)
- 💡 [功能建议](https://github.com/linjunhao024-byte/traffic-padding/issues)
- ⭐ [给个 Star](https://github.com/linjunhao024-byte/traffic-padding)

---

## 📄 License

MIT
