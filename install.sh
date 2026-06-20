#!/bin/bash
# LIN-Padding 安装脚本

set -e

INSTALL_DIR="/opt/traffic-padding"
CONFIG_DIR="/etc/traffic-padding"
SERVICE_NAME="traffic-padding"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[✓]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[!]${NC} $1"; }
log_error() { echo -e "${RED}[✗]${NC} $1"; }
log_step() { echo -e "${BLUE}[→]${NC} $1"; }

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "此脚本需要 root 权限"
        echo "请使用: sudo bash install.sh"
        exit 1
    fi
}

check_commands() {
    local missing=()
    command -v python3 &> /dev/null || missing+=("python3")
    command -v systemctl &> /dev/null || missing+=("systemctl")
    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "缺少命令: ${missing[*]}"
        echo "Ubuntu/Debian: sudo apt install ${missing[*]}"
        echo "CentOS/RHEL:   sudo yum install ${missing[*]}"
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
# Logo
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

# ============================================================================
# 交互配置
# ============================================================================

prompt_config() {
    show_logo

    while true; do
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

        echo ""
        echo -e "${BOLD}━━━ 步骤 2/5: 流量比例 ━━━${NC}"
        echo ""
        echo "  1:2 = 保守    1:3 = 推荐    1:4 = 激进"
        echo ""
        read -rp "请输入目标比例 [默认: 3]: " user_ratio
        TARGET_RATIO="${user_ratio:-3}"

        echo ""
        echo -e "${BOLD}━━━ 步骤 3/5: 流量配额 ━━━${NC}"
        echo ""
        read -rp "每日最大额外下载（GB）[默认: 10]: " user_quota
        DAILY_QUOTA="${user_quota:-10}"
        echo ""
        echo "月流量总额度：0=禁用  -1=无限  正数=具体额度(GB)"
        read -rp "月额度 [默认: 0]: " user_monthly
        MONTHLY_QUOTA="${user_monthly:-0}"

        echo ""
        echo -e "${BOLD}━━━ 步骤 4/5: 消息推送 ━━━${NC}"
        echo ""
        read -rp "启用消息推送？(y/N): " enable_notify
        NOTIFY_TYPE=""
        TG_ENABLED="false"
        TG_BOT_TOKEN=""
        TG_CHAT_ID=""
        TG_REPORT_FREQ="daily"
        TG_MONTHLY_RESET_DAY="1"
        DINGTALK_ENABLED="false"
        DINGTALK_WEBHOOK=""
        DINGTALK_SECRET=""
        DINGTALK_REPORT_FREQ="daily"
        DINGTALK_MONTHLY_RESET_DAY="1"

        if [[ "${enable_notify,,}" == "y" ]]; then
            echo ""
            echo -e "${BOLD}请选择推送方式:${NC}"
            echo ""
            echo -e "  ${CYAN}[1]${NC} 钉钉机器人 ${GREEN}(推荐 - 国内服务器首选)${NC}"
            echo -e "  ${CYAN}[2]${NC} Telegram ${YELLOW}(需要服务器能访问 api.telegram.org)${NC}"
            echo ""
            echo -e "${YELLOW}⚠️  提示: 如果您的服务器在中国大陆，Telegram API 可能无法访问，${NC}"
            echo -e "${YELLOW}    建议选择钉钉机器人。${NC}"
            echo ""
            read -rp "请选择 [1/2]: " notify_choice

            if [[ "$notify_choice" == "2" ]]; then
                NOTIFY_TYPE="tg"
                TG_ENABLED="true"
                echo ""
                echo "创建 Bot: https://t.me/BotFather"
                echo "获取 Chat ID: https://t.me/userinfobot"
                echo ""

                local tg_configured=false
                while [[ "$tg_configured" == "false" ]]; do
                    read -rp "Bot Token: " TG_BOT_TOKEN
                    read -rp "Chat ID: " TG_CHAT_ID

                    echo ""
                    log_step "发送测试消息..."
                    local test_result
                    test_result=$(curl -s -o /dev/null -w "%{http_code}" \
                        "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
                        -d "chat_id=${TG_CHAT_ID}" \
                        -d "text=🟢 Traffic Padding 测试消息" \
                        -d "parse_mode=HTML" 2>/dev/null)

                    if [[ "$test_result" == "200" ]]; then
                        echo -e "${GREEN}[✓]${NC} 测试成功！请检查 TG 是否收到"
                        tg_configured=true
                    else
                        echo -e "${RED}[✗]${NC} 测试失败 (HTTP ${test_result})"
                        echo ""
                        echo -e "${YELLOW}[R]${NC} 重新填写"
                        echo -e "${YELLOW}[S]${NC} 跳过推送"
                        echo ""
                        read -rp "请选择 [R/S]: " retry_choice
                        if [[ "${retry_choice,,}" == "s" ]]; then
                            TG_ENABLED="false"
                            TG_BOT_TOKEN=""
                            TG_CHAT_ID=""
                            log_warn "已跳过推送配置"
                            break
                        fi
                    fi
                done

                if [[ "$TG_ENABLED" == "true" ]]; then
                    echo ""
                    echo "报告频率: [1] 日报  [2] 周报  [3] 月报"
                    read -rp "选择 [默认: 1]: " freq_choice
                    case "${freq_choice}" in
                        2) TG_REPORT_FREQ="weekly" ;;
                        3)
                            TG_REPORT_FREQ="monthly"
                            read -rp "月额度重置日（几号）[默认: 1]: " reset_day
                            TG_MONTHLY_RESET_DAY="${reset_day:-1}"
                            ;;
                        *) TG_REPORT_FREQ="daily" ;;
                    esac
                fi
            else
                NOTIFY_TYPE="dingtalk"
                DINGTALK_ENABLED="true"
                echo ""
                echo -e "创建钉钉机器人: 钉钉群 → 群设置 → 智能群助手 → 添加机器人 → 自定义"
                echo -e "${YELLOW}安全设置建议选择「自定义关键词」，填写: Traffic Padding${NC}"
                echo ""

                local dt_configured=false
                while [[ "$dt_configured" == "false" ]]; do
                    read -rp "Webhook URL: " DINGTALK_WEBHOOK
                    read -rp "加签密钥 (Secret，可留空): " DINGTALK_SECRET

                    echo ""
                    log_step "发送测试消息..."
                    local dt_url="$DINGTALK_WEBHOOK"
                    if [[ -n "$DINGTALK_SECRET" ]]; then
                        local timestamp=$(($(date +%s) * 1000))
                        local string_to_sign="${timestamp}\n${DINGTALK_SECRET}"
                        local sign=$(echo -ne "$string_to_sign" | openssl dgst -sha256 -hmac "$DINGTALK_SECRET" -binary | base64)
                        local sign_encoded=$(python3 -c "import urllib.parse; print(urllib.parse.quote_plus('$sign'))" 2>/dev/null || echo "$sign")
                        dt_url="${dt_url}&timestamp=${timestamp}&sign=${sign_encoded}"
                    fi

                    local dt_test_result
                    dt_test_result=$(curl -s -o /dev/null -w "%{http_code}" \
                        -H "Content-Type: application/json" \
                        -d '{"msgtype":"markdown","markdown":{"title":"测试","text":"## 🟢 Traffic Padding 测试消息\n\n钉钉推送配置成功！"}}' \
                        "$dt_url" 2>/dev/null)

                    if [[ "$dt_test_result" == "200" ]]; then
                        echo -e "${GREEN}[✓]${NC} 测试成功！请检查钉钉群是否收到"
                        dt_configured=true
                    else
                        echo -e "${RED}[✗]${NC} 测试失败 (HTTP ${dt_test_result})"
                        echo ""
                        echo -e "${YELLOW}[R]${NC} 重新填写"
                        echo -e "${YELLOW}[S]${NC} 跳过推送"
                        echo ""
                        read -rp "请选择 [R/S]: " retry_choice
                        if [[ "${retry_choice,,}" == "s" ]]; then
                            DINGTALK_ENABLED="false"
                            DINGTALK_WEBHOOK=""
                            DINGTALK_SECRET=""
                            log_warn "已跳过推送配置"
                            break
                        fi
                    fi
                done

                if [[ "$DINGTALK_ENABLED" == "true" ]]; then
                    echo ""
                    echo "报告频率: [1] 日报  [2] 周报  [3] 月报"
                    read -rp "选择 [默认: 1]: " freq_choice
                    case "${freq_choice}" in
                        2) DINGTALK_REPORT_FREQ="weekly" ;;
                        3)
                            DINGTALK_REPORT_FREQ="monthly"
                            read -rp "月额度重置日（几号）[默认: 1]: " reset_day
                            DINGTALK_MONTHLY_RESET_DAY="${reset_day:-1}"
                            ;;
                        *) DINGTALK_REPORT_FREQ="daily" ;;
                    esac
                fi
            fi

            if [[ "$TG_ENABLED" == "true" || "$DINGTALK_ENABLED" == "true" ]]; then
                echo ""
                echo -e "${YELLOW}💡 提示: 服务首次执行下载任务后，将自动推送一条「数据推送测试消息」${NC}"
                echo -e "${YELLOW}   用于验证推送功能是否正常，之后将按设定频率自动推送。${NC}"
            fi
        fi

        echo ""
        echo -e "${BOLD}━━━ 步骤 5/5: 管理命令 ━━━${NC}"
        echo ""
        read -rp "快捷命令名称（1-3字符）[默认: tp]: " user_cmd
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
        if [[ "${TG_ENABLED}" == "true" ]]; then
            echo "│  推送:      Telegram"
            echo "│  报告频率:  ${TG_REPORT_FREQ}"
        elif [[ "${DINGTALK_ENABLED}" == "true" ]]; then
            echo "│  推送:      钉钉机器人"
            echo "│  报告频率:  ${DINGTALK_REPORT_FREQ}"
        else
            echo "│  推送:      未启用"
        fi
        echo "│  管理命令:  ${CMD_NAME}"
        echo "└────────────────────────────────────────┘"
        echo ""
        echo -e "${GREEN}[Y]${NC} 确认安装"
        echo -e "${YELLOW}[1-5]${NC} 重新设置对应项"
        echo -e "${RED}[N]${NC} 取消"
        echo ""
        read -rp "请选择: " confirm

        case "${confirm,,}" in
            y|"") return ;;
            [1-5]) continue ;;
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

    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [[ -f "${script_dir}/main.py" ]]; then
        cp "${script_dir}/main.py" "${INSTALL_DIR}/main.py"
        chmod 755 "${INSTALL_DIR}/main.py"
        log_info "main.py (本地文件)"
    else
        log_error "未找到 main.py，请确保与 install.sh 在同一目录"
        exit 1
    fi
}

# ============================================================================
# 生成管理脚本
# ============================================================================

generate_tpm() {
    log_step "生成管理脚本..."

    cat > "${INSTALL_DIR}/tpm.sh" << 'TPM_EOF'
#!/bin/bash

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
    echo -e "${CYAN}║${BOLD}          Traffic Padding Manager                           ${NC}${CYAN}║${NC}"
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
    echo -e "${RED}需要 root: sudo tpm${NC}"
    wait_key
    return 1
}

do_uninstall() {
    echo ""
    echo -e "${RED}⚠️  一键卸载${NC}"
    echo ""
    echo "将删除: 服务、程序文件、管理命令"
    echo ""
    read -rp "确认卸载？(y/N): " confirm
    [[ "${confirm,,}" != "y" ]] && { echo "已取消"; wait_key; return; }

    systemctl stop "${SERVICE_NAME}" 2>/dev/null
    systemctl disable "${SERVICE_NAME}" 2>/dev/null
    rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload
    rm -f "/usr/local/bin/tpm"
    rm -rf "/opt/traffic-padding"

    echo ""
    read -rp "删除配置文件？(y/N): " del_config
    [[ "${del_config,,}" == "y" ]] && rm -rf "/etc/traffic-padding" && echo "配置已删除"

    echo ""
    echo -e "${GREEN}✅ 卸载完成${NC}"
    echo "  https://github.com/linjunhao024-byte/Traffic-Tadding/issues"
    echo ""
    exit 0
}

log_info() { echo -e "${GREEN}[✓]${NC} $1"; }

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
            9) need_root && systemctl enable "${SERVICE_NAME}" && log_info "已启用"; wait_key ;;
            10) need_root && systemctl disable "${SERVICE_NAME}" && log_info "已取消"; wait_key ;;
            11) grep "$(python3 -c "import json;print(json.load(open('${CONFIG_DIR}/config.json')).get('interface','eth0'))" 2>/dev/null || echo 'eth0'):" /proc/net/dev 2>/dev/null || echo "网卡读取失败"; wait_key ;;
            12) need_root && do_uninstall ;;
            0) clear; exit 0 ;;
            *) echo -e "${RED}无效选项${NC}"; sleep 1 ;;
        esac
    done
}

main
TPM_EOF

    chmod 755 "${INSTALL_DIR}/tpm.sh"
    ln -sf "${INSTALL_DIR}/tpm.sh" "/usr/local/bin/${CMD_NAME}"
    log_info "管理命令: ${CMD_NAME}"
}

# ============================================================================
# 生成配置和服务
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
    "tg_monthly_reset_day": ${TG_MONTHLY_RESET_DAY},
    "dingtalk_enabled": ${DINGTALK_ENABLED},
    "dingtalk_webhook": "${DINGTALK_WEBHOOK}",
    "dingtalk_secret": "${DINGTALK_SECRET}",
    "dingtalk_report_freq": "${DINGTALK_REPORT_FREQ}",
    "dingtalk_monthly_reset_day": ${DINGTALK_MONTHLY_RESET_DAY}
}
EOF
    log_info "配置: ${CONFIG_DIR}/config.json"
}

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
# 完成 & 卸载
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
    echo -e "  ${CYAN}https://github.com/linjunhao024-byte/Traffic-Tadding${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

uninstall() {
    echo ""
    echo -e "${YELLOW}正在卸载...${NC}"

    systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
    systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
    rm -f "${SERVICE_FILE}"
    systemctl daemon-reload
    rm -f /usr/local/bin/tp
    rm -f /usr/local/bin/tpm
    [[ -n "$CMD_NAME" ]] && rm -f "/usr/local/bin/${CMD_NAME}"
    rm -rf "${INSTALL_DIR}"

    read -rp "删除配置文件 ${CONFIG_DIR}？(y/n) [n]: " del
    [[ "${del,,}" == "y" ]] && rm -rf "${CONFIG_DIR}" && echo "配置已删除"

    echo ""
    echo -e "${GREEN}✅ 卸载完成${NC}"
    echo "  https://github.com/linjunhao024-byte/Traffic-Tadding/issues"
    echo ""
}

# ============================================================================
# 主流程
# ============================================================================

main() {
    echo -e "${CYAN}"
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║         Traffic Padding Micro-Service 安装程序              ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"

    if [[ "$1" == "uninstall" || "$1" == "-u" ]]; then
        check_root
        uninstall
        exit 0
    fi

    log_step "检查环境..."
    check_root
    check_commands
    log_info "环境检查通过"
    echo ""

    prompt_config
    echo ""

    install_files
    generate_tpm
    generate_config
    generate_service
    start_service
    show_success
}

main "$@"
