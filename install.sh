#!/bin/bash

set -e

# ============================================================================
# 配置
# ============================================================================

# 安装路径
INSTALL_DIR="/opt/traffic-padding"
CONFIG_DIR="/etc/traffic-padding"
SERVICE_NAME="traffic-padding"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ============================================================================
# 辅助函数
# ============================================================================

print_banner() {
    echo -e "${CYAN}"
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║         Traffic Padding Micro-Service 安装程序              ║"
    echo "║              流量伪装微服务 - 一键部署                      ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

log_info() { echo -e "${GREEN}[✓]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[!]${NC} $1"; }
log_error() { echo -e "${RED}[✗]${NC} $1"; }
log_step() { echo -e "${BLUE}[→]${NC} $1"; }

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "此脚本需要 root 权限运行"
        echo "请使用: sudo bash install.sh"
        exit 1
    fi
}

check_commands() {
    local missing=()
    command -v python3 &> /dev/null || missing+=("python3")
    command -v systemctl &> /dev/null || missing+=("systemctl")

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "缺少必要命令: ${missing[*]}"
        echo "请先安装:"
        echo "  Ubuntu/Debian: sudo apt install ${missing[*]}"
        echo "  CentOS/RHEL:   sudo yum install ${missing[*]}"
        exit 1
    fi
}

detect_interface() {
    local iface=""
    if command -v ip &> /dev/null; then
        iface=$(ip route get 8.8.8.8 2>/dev/null | awk '/dev/{for(i=1;i<=NF;i++) if($i=="dev") {print $(i+1); exit}}')
    fi
    [[ -z "$iface" ]] && iface=$(ip route show default 2>/dev/null | awk '{print $5}' | head -1)
    [[ -z "$iface" ]] && iface=$(awk -F: '/:/ && !/lo/{print $1; exit}' /proc/net/dev 2>/dev/null | tr -d ' ')
    echo "$iface"
}

validate_interface() {
    grep -q "^[[:space:]]*${1}:" /proc/net/dev 2>/dev/null
}

# ============================================================================
# 交互配置（支持返回修改）
# ============================================================================

show_logo() {
    clear
    echo -e "${CYAN}"
    echo '    __     _____   __  __             ____                        __        __                      '
    echo '   / /    /  _/   / | / /            / __ \   ____    ____/ /   ____/ /   (_)   ____       ____   '
    echo '  / /     / /    /  |/ /   ______   / /_/ /  / __ `/  / __  /   / __  /   / /   / __ \     / __ `/ '
    echo ' / /___  _/ /    / /|  /   /_____/  / .___/  / /_/ /  / /_/ /   / /_/ /   / /   / / / /    / /_/ /  '
    echo '/_____/ /___/   /_/ |_/            /_/       \__,_/   \__,_/    \__,_/   /_/   /_/ /_/     \__, /   '
    echo '                                                                                          /____/    '
    echo -e "${NC}"
    echo -e "${BOLD}                         Traffic Padding Micro-Service${NC}"
    echo -e "${BOLD}                             流量伪装微服务${NC}"
    echo ""
}

prompt_config() {
    show_logo

    while true; do
        # 步骤 1: 网卡
        echo -e "${BOLD}━━━ 步骤 1/5: 网卡配置 ━━━${NC}"
        echo ""
        local detected=$(detect_interface)
        if [[ -n "$detected" ]]; then
            echo -e "检测到默认网卡: ${GREEN}${detected}${NC}"
            read -rp "使用此网卡？(Y/n): " use_detected
            INTERFACE="${detected}"
            [[ "${use_detected,,}" == "n" ]] && read -rp "请输入网卡名称: " INTERFACE
        else
            read -rp "请输入网卡名称 (如 eth0): " INTERFACE
        fi
        validate_interface "$INTERFACE" || log_warn "网卡 '${INTERFACE}' 可能不存在"

        # 步骤 2: 流量比例
        echo ""
        echo -e "${BOLD}━━━ 步骤 2/5: 流量比例 ━━━${NC}"
        echo ""
        echo "  1:2 = 保守    1:3 = 推荐    1:4 = 激进"
        echo ""
        read -rp "请输入目标比例 [默认: 3]: " user_ratio
        TARGET_RATIO="${user_ratio:-3}"

        # 步骤 3: 配额设置
        echo ""
        echo -e "${BOLD}━━━ 步骤 3/5: 流量配额 ━━━${NC}"
        echo ""
        read -rp "每日最大额外下载（GB）[默认: 10]: " user_quota
        DAILY_QUOTA="${user_quota:-10}"
        echo ""
        echo "服务器月流量总额度（用于计算占比）："
        echo "  0  = 禁用月额度统计"
        echo "  -1 = 无限流量"
        echo "  正数 = 具体额度 (GB)"
        echo ""
        read -rp "月额度 [默认: 0]: " user_monthly
        MONTHLY_QUOTA="${user_monthly:-0}"

        # 步骤 4: TG 推送设置
        echo ""
        echo -e "${BOLD}━━━ 步骤 4/5: TG 消息推送 ━━━${NC}"
        echo ""
        echo "是否启用 Telegram 机器人消息推送？"
        echo "  启用后可接收服务运行状态的日报/周报/月报"
        echo ""
        read -rp "启用 TG 推送？(y/N) [默认: N]: " enable_tg
        TG_ENABLED="false"
        TG_BOT_TOKEN=""
        TG_CHAT_ID=""
        TG_REPORT_FREQ="daily"
        TG_MONTHLY_RESET_DAY="1"

        if [[ "${enable_tg,,}" == "y" ]]; then
            TG_ENABLED="true"
            echo ""
            echo "请先创建 TG Bot：https://t.me/BotFather"
            echo "获取 Chat ID：https://t.me/userinfobot"
            echo ""

            # 循环直到测试成功或用户跳过
            local tg_configured=false
            while [[ "$tg_configured" == "false" ]]; do
                read -rp "Bot Token: " TG_BOT_TOKEN
                read -rp "Chat ID: " TG_CHAT_ID

                # 发送测试消息
                echo ""
                log_step "正在发送测试消息..."
                local test_result
                test_result=$(curl -s -o /dev/null -w "%{http_code}" \
                    "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
                    -d "chat_id=${TG_CHAT_ID}" \
                    -d "text=🟢 Traffic Padding 测试消息%0A安装配置正在进行中..." \
                    -d "parse_mode=HTML" 2>/dev/null)

                if [[ "$test_result" == "200" ]]; then
                    echo -e "${GREEN}[✓]${NC} 测试消息发送成功！请检查 TG 是否收到"
                    tg_configured=true
                else
                    echo ""
                    echo -e "${RED}[✗]${NC} 测试消息发送失败 (HTTP ${test_result})"
                    echo ""
                    echo -e "${YELLOW}[R]${NC} 重新填写 Token 和 Chat ID"
                    echo -e "${YELLOW}[S]${NC} 跳过 TG 推送配置"
                    echo ""
                    read -rp "请选择 [R/S]: " retry_choice
                    if [[ "${retry_choice,,}" == "s" ]]; then
                        TG_ENABLED="false"
                        TG_BOT_TOKEN=""
                        TG_CHAT_ID=""
                        log_warn "已跳过 TG 推送配置"
                        break
                    fi
                    echo ""
                fi
            done

            # 如果 TG 启用成功，继续配置报告频率
            if [[ "$TG_ENABLED" == "true" ]]; then
                echo ""
                echo "报告频率："
                echo "  [1] 日报 - 每天发送"
                echo "  [2] 周报 - 每周一发送"
                echo "  [3] 月报 - 每月指定日期前发送"
                echo ""
                read -rp "选择频率 [默认: 1]: " freq_choice
                case "${freq_choice}" in
                    2) TG_REPORT_FREQ="weekly" ;;
                    3)
                        TG_REPORT_FREQ="monthly"
                        echo ""
                        echo "月额度重置日期（几号）："
                        read -rp "重置日期 [默认: 1]: " reset_day
                        TG_MONTHLY_RESET_DAY="${reset_day:-1}"
                        ;;
                    *) TG_REPORT_FREQ="daily" ;;
                esac
            fi
        fi

        # 步骤 5: 管理命令名称
        echo ""
        echo -e "${BOLD}━━━ 步骤 5/5: 管理命令 ━━━${NC}"
        echo ""
        echo "设置呼出管理菜单的快捷命令（1-3 个字符）"
        read -rp "命令名称 [默认: tp]: " user_cmd
        CMD_NAME="${user_cmd:-tp}"
        CMD_NAME="${CMD_NAME:0:3}"

        # 配置确认
        echo ""
        echo -e "${BOLD}━━━ 配置确认 ━━━${NC}"
        echo "┌────────────────────────────────────────┐"
        echo "│  网卡:      ${INTERFACE}"
        echo "│  比例:      1:${TARGET_RATIO}"
        echo "│  日配额:    ${DAILY_QUOTA} GB/天"
        echo "│  月额度:    ${MONTHLY_QUOTA} GB"
        echo "│  TG 推送:   ${TG_ENABLED}"
        [[ "${TG_ENABLED}" == "true" ]] && echo "│  报告频率:  ${TG_REPORT_FREQ}"
        echo "│  管理命令:  ${CMD_NAME}"
        echo "└────────────────────────────────────────┘"
        echo ""
        echo -e "${GREEN}[Y]${NC} 确认安装"
        echo -e "${YELLOW}[1]${NC} 重新设置网卡"
        echo -e "${YELLOW}[2]${NC} 重新设置比例"
        echo -e "${YELLOW}[3]${NC} 重新设置配额"
        echo -e "${YELLOW}[4]${NC} 重新设置 TG 推送"
        echo -e "${YELLOW}[5]${NC} 重新设置命令"
        echo -e "${RED}[N]${NC} 取消安装"
        echo ""
        read -rp "请选择 [Y/1/2/3/4/5/N]: " confirm

        case "${confirm,,}" in
            y|"") return ;;
            1) continue ;;
            2) continue ;;
            3) continue ;;
            4) continue ;;
            5) continue ;;
            n) log_warn "安装已取消"; exit 0 ;;
            *) continue ;;
        esac
    done
}

# ============================================================================
# 文件安装
# ============================================================================

install_files() {
    log_step "安装程序文件..."

    mkdir -p "${INSTALL_DIR}"

    # 获取 main.py（优先从 install.sh 同目录）
    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    if [[ -f "${script_dir}/main.py" ]]; then
        cp "${script_dir}/main.py" "${INSTALL_DIR}/main.py"
        chmod 755 "${INSTALL_DIR}/main.py"
        log_info "main.py (本地文件)"
    else
        log_error "未找到 main.py"
        echo ""
        echo "请确保 main.py 和 install.sh 在同一目录，然后重新执行："
        echo "  sudo bash install.sh"
        echo ""
        echo "或者从 GitHub 克隆完整仓库："
        echo "  git clone https://github.com/YOUR_USER/traffic-padding.git"
        echo "  cd traffic-padding"
        echo "  sudo bash install.sh"
        echo ""
        exit 1
    fi
}

# ============================================================================
# 生成 tpm 管理脚本
# ============================================================================

generate_tpm() {
    log_step "生成管理脚本..."

    cat > "${INSTALL_DIR}/tpm.sh" << 'TPM_EOF'
#!/bin/bash
# Traffic Padding Manager - 交互式管理菜单

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

SERVICE_NAME="traffic-padding"
CONFIG_DIR="/etc/traffic-padding"

show_header() {
    clear
    echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${BOLD}          Traffic Padding Manager (tpm)                      ${NC}${CYAN}║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

get_status() {
    local status="${RED}已停止${NC}"
    systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null && status="${GREEN}运行中${NC}"

    local boot="${YELLOW}未启用${NC}"
    systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null && boot="${GREEN}已启用${NC}"

    local config=""
    [[ -f "${CONFIG_DIR}/config.json" ]] && config=$(python3 -c "
import json
with open('${CONFIG_DIR}/config.json') as f:
    c=json.load(f)
print(f\"网卡:{c.get('interface','?')} 比例:1:{c.get('target_ratio','?')} 配额:{c.get('max_daily_extra_gb','?')}GB\")
" 2>/dev/null || echo "配置读取失败")

    local quota="今日: 无记录"
    [[ -f "${CONFIG_DIR}/usage.json" ]] && quota=$(python3 -c "
import json;from datetime import datetime as d
with open('${CONFIG_DIR}/usage.json') as f:
    p=json.load(f)
b=p.get('used_bytes',0)
t=d.now().strftime('%Y-%m-%d')
print(f\"今日: {b/1073741824:.3f}GB\" if p.get('date')==t else '今日: 0.000GB')
" 2>/dev/null || echo "今日: 读取失败")

    echo -e "状态: ${status}  自启: ${boot}"
    echo -e "${config}"
    echo -e "${quota}"
}

wait_key() {
    echo ""
    echo -e "${YELLOW}按 Enter 返回菜单...${NC}"
    read -r
}

need_root() {
    [[ $EUID -eq 0 ]] && return 0
    echo -e "${RED}需要 root 权限，请使用: sudo tpm${NC}"
    wait_key
    return 1
}

do_uninstall() {
    echo ""
    echo -e "${RED}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}║                    ⚠️  一键卸载                             ║${NC}"
    echo -e "${RED}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "此操作将："
    echo "  • 停止并删除服务"
    echo "  • 删除程序文件 (/opt/traffic-padding)"
    echo "  • 删除管理命令 (tpm)"
    echo ""
    read -rp "确认卸载？(y/N): " confirm
    if [[ "${confirm,,}" != "y" ]]; then
        echo "已取消"
        wait_key
        return
    fi

    echo ""
    echo "正在卸载..."

    # 停止服务
    systemctl stop "${SERVICE_NAME}" 2>/dev/null
    systemctl disable "${SERVICE_NAME}" 2>/dev/null

    # 删除服务文件
    rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload

    # 删除快捷命令
    rm -f "/usr/local/bin/tpm"

    # 删除程序
    rm -rf "/opt/traffic-padding"

    # 询问删除配置
    echo ""
    read -rp "是否删除配置文件 (/etc/traffic-padding)？(y/N): " del_config
    if [[ "${del_config,,}" == "y" ]]; then
        rm -rf "/etc/traffic-padding"
        echo -e "${GREEN}[✓]${NC} 配置已删除"
    else
        echo "配置已保留: /etc/traffic-padding"
    fi

    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║                    ✅ 卸载完成                              ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    exit 0
}

main() {
    while true; do
        show_header
        get_status
        echo ""
        echo -e "${BOLD}服务控制${NC}       ${BOLD}日志${NC}           ${BOLD}配置${NC}           ${BOLD}系统${NC}"
        echo "  [1] 查看状态    [5] 实时日志    [7] 查看配置    [9] 开机自启"
        echo "  [2] 启动服务    [6] 最近日志    [8] 编辑配置   [10] 取消自启"
        echo "  [3] 停止服务                               [11] 网卡测试"
        echo "  [4] 重启服务                               [12] ⚠️ 卸载"
        echo ""
        echo "  [0] 退出"
        echo ""
        read -rp "选择 [0-12]: " choice

        case "$choice" in
            1) systemctl status "${SERVICE_NAME}" --no-pager; wait_key ;;
            2) need_root && systemctl start "${SERVICE_NAME}" && log_info "已启动"; wait_key ;;
            3) need_root && systemctl stop "${SERVICE_NAME}" && log_info "已停止"; wait_key ;;
            4) need_root && systemctl restart "${SERVICE_NAME}" && log_info "已重启"; wait_key ;;
            5) journalctl -u "${SERVICE_NAME}" -f --no-pager; wait_key ;;
            6) journalctl -u "${SERVICE_NAME}" -n 50 --no-pager; wait_key ;;
            7) cat "${CONFIG_DIR}/config.json" 2>/dev/null || echo "配置不存在"; wait_key ;;
            8) need_root && ${EDITOR:-nano} "${CONFIG_DIR}/config.json"; wait_key ;;
            9) need_root && systemctl enable "${SERVICE_NAME}" && log_info "已启用自启"; wait_key ;;
            10) need_root && systemctl disable "${SERVICE_NAME}" && log_info "已取消自启"; wait_key ;;
            11) grep "$(python3 -c "import json;print(json.load(open('${CONFIG_DIR}/config.json')).get('interface','eth0'))" 2>/dev/null || echo 'eth0'):" /proc/net/dev 2>/dev/null || echo "网卡读取失败"; wait_key ;;
            12) need_root && do_uninstall ;;
            0) clear; exit 0 ;;
            *) echo -e "${RED}无效选项${NC}"; sleep 1 ;;
        esac
    done
}

log_info() { echo -e "${GREEN}[✓]${NC} $1"; }
main
TPM_EOF

    chmod 755 "${INSTALL_DIR}/tpm.sh"

    # 创建用户自定义的快捷命令
    ln -sf "${INSTALL_DIR}/tpm.sh" "/usr/local/bin/${CMD_NAME}"
    log_info "管理命令: ${CMD_NAME}"
}

# ============================================================================
# 生成配置文件
# ============================================================================

generate_config() {
    log_step "生成配置文件..."

    mkdir -p "${CONFIG_DIR}"

    cat > "${CONFIG_DIR}/config.json" << EOF
{
    "interface": "${INTERFACE}",
    "target_ratio": ${TARGET_RATIO},
    "max_daily_extra_gb": ${DAILY_QUOTA},
    "monthly_quota_gb": ${MONTHLY_QUOTA},
    "min_task_bytes": 2097152,
    "max_task_bytes": 15728640,
    "jitter_base": 5,
    "jitter_range": 25,
    "enable_night_mode": true,
    "night_start_hour": 2,
    "night_end_hour": 5,
    "night_multiplier": 5.0,
    "peak_hours": [19, 20, 21, 22],
    "peak_multiplier": 0.6,
    "tg_enabled": ${TG_ENABLED},
    "tg_bot_token": "${TG_BOT_TOKEN}",
    "tg_chat_id": "${TG_CHAT_ID}",
    "tg_report_freq": "${TG_REPORT_FREQ}",
    "tg_monthly_reset_day": ${TG_MONTHLY_RESET_DAY}
}
EOF

    log_info "配置: ${CONFIG_DIR}/config.json"
}

# ============================================================================
# 生成 Systemd 服务
# ============================================================================

generate_service() {
    log_step "注册系统服务..."

    cat > "${SERVICE_FILE}" << 'EOF'
[Unit]
Description=Traffic Padding Micro-Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
ExecStart=/usr/bin/python3 /opt/traffic-padding/main.py /etc/traffic-padding/config.json
Restart=on-failure
RestartSec=30
StartLimitInterval=300
StartLimitBurst=5
Nice=19
CPUSchedulingPolicy=idle
IOSchedulingClass=idle
IOSchedulingPriority=0
MemoryMax=50M
MemoryHigh=40M
StandardOutput=journal
StandardError=journal
LimitNOFILE=1024
LimitNPROC=64
ProtectHome=yes
NoNewPrivileges=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    log_info "服务: ${SERVICE_FILE}"
}

# ============================================================================
# 启动服务
# ============================================================================

start_service() {
    log_step "启动服务..."

    systemctl enable "${SERVICE_NAME}" 2>/dev/null
    systemctl start "${SERVICE_NAME}"

    sleep 2

    if systemctl is-active --quiet "${SERVICE_NAME}"; then
        log_info "服务启动成功"
    else
        log_error "服务启动失败"
        echo "查看日志: journalctl -u ${SERVICE_NAME} -n 20"
        exit 1
    fi
}

# ============================================================================
# 安装完成
# ============================================================================

show_success() {
    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║                  🎉 安装成功！                              ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${BOLD}管理命令:${NC}"
    echo ""
    echo -e "  ${CYAN}${CMD_NAME}${NC}                     呼出管理菜单"
    echo ""
    echo -e "${BOLD}常用命令:${NC}"
    echo ""
    echo "  systemctl status ${SERVICE_NAME}    查看状态"
    echo "  journalctl -u ${SERVICE_NAME} -f    查看日志"
    echo "  systemctl restart ${SERVICE_NAME}   重启服务"
    echo ""
    echo -e "${BOLD}配置文件:${NC}"
    echo ""
    echo "  ${CONFIG_DIR}/config.json   修改后 5 分钟自动生效"
    echo ""
    echo -e "${BOLD}卸载:${NC}"
    echo ""
    echo "  sudo bash install.sh uninstall"
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "  如果这个项目对你有帮助，请给一个 ${GREEN}⭐ Star！${NC}"
    echo -e "  ${CYAN}https://github.com/linjunhao024-byte/traffic-padding${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

# ============================================================================
# 卸载
# ============================================================================

uninstall() {
    echo ""
    echo -e "${YELLOW}正在卸载...${NC}"

    systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
    systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
    rm -f "${SERVICE_FILE}"
    systemctl daemon-reload

    # 删除所有可能的快捷命令（tp, tpm, 以及用户自定义的）
    rm -f /usr/local/bin/tp
    rm -f /usr/local/bin/tpm
    # 如果用户自定义了其他名称，也需要删除
    [[ -n "$CMD_NAME" ]] && rm -f "/usr/local/bin/${CMD_NAME}"

    rm -rf "${INSTALL_DIR}"

    read -rp "删除配置文件 ${CONFIG_DIR}？(y/n) [n]: " del
    [[ "${del,,}" == "y" ]] && rm -rf "${CONFIG_DIR}" && echo "配置已删除"

    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║                    ✅ 卸载完成                              ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "  感谢使用！如有问题或建议，欢迎提交 Issue："
    echo -e "  ${CYAN}https://github.com/linjunhao024-byte/traffic-padding/issues${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

# ============================================================================
# 主流程
# ============================================================================

main() {
    print_banner

    # 卸载模式
    if [[ "$1" == "uninstall" || "$1" == "-u" ]]; then
        check_root
        uninstall
        exit 0
    fi

    # 环境检查
    log_step "检查环境..."
    check_root
    check_commands
    log_info "环境检查通过"
    echo ""

    # 交互配置
    prompt_config
    echo ""

    # 安装
    install_files
    generate_tpm
    generate_config
    generate_service
    start_service

    # 完成
    show_success
}

main "$@"
