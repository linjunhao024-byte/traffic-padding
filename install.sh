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
MAGENTA='\033[0;35m'
BOLD='\033[1m'
DIM='\033[2m'
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

list_interfaces() {
    awk -F: '/:/ && !/lo/{gsub(/^[ \t]+/, "", $1); print $1}' /proc/net/dev 2>/dev/null
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
        # ─── 步骤 1: 网卡配置 ─────────────────────────────────────────
        echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
        echo -e "${CYAN}|${NC}  ${BOLD}步骤 1/5${NC}  网卡配置                                         ${CYAN}|${NC}"
        echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
        echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"

        local iface_list=()
        while IFS= read -r line; do
            iface_list+=("$line")
        done < <(list_interfaces)

        if [[ ${#iface_list[@]} -gt 0 ]]; then
            echo -e "${CYAN}|${NC}  可用网卡:                                                 ${CYAN}|${NC}"
            for iface in "${iface_list[@]}"; do
                printf "${CYAN}|${NC}    ${DIM}•${NC} %-54s${CYAN}|${NC}\n" "${iface}"
            done
            echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
        fi

        local detected=$(detect_interface)
        if [[ -n "$detected" ]]; then
            printf "${CYAN}|${NC}  检测到默认网卡: ${GREEN}%-42s${NC}${CYAN}|${NC}\n" "${detected}"
            echo -e "${CYAN}|${NC}  ${YELLOW}💡 一般选择默认网卡，中转流量走这个接口${NC}                  ${CYAN}|${NC}"
            echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
            echo -ne "${CYAN}|${NC}  使用此网卡？(${GREEN}Y${NC}/n): "
            read use_detected
            INTERFACE="${detected}"
            [[ "${use_detected,,}" == "n" ]] && { echo -ne "${CYAN}|${NC}  请输入网卡名称: "; read INTERFACE; }
        else
            echo -ne "${CYAN}|${NC}  请输入网卡名称 (如 eth0): "
            read INTERFACE
        fi
        validate_interface "$INTERFACE" || log_warn "网卡 '${INTERFACE}' 可能不存在"
        echo -e "${CYAN}+--------------------------------------------------------------+${NC}"

        echo ""

        # ─── 步骤 2: 流量比例 ─────────────────────────────────────────
        echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
        echo -e "${CYAN}|${NC}  ${BOLD}步骤 2/5${NC}  流量比例                                         ${CYAN}|${NC}"
        echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
        echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}    ${CYAN}(a)${NC} 1:2 = 保守                                           ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}    ${GREEN}(b)${NC} 1:3 = 推荐 ${GREEN}← 默认${NC}                                   ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}    ${YELLOW}(c)${NC} 1:4 = 激进                                           ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}    ${RED}(d)${NC} 1:5 = 极端                                           ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
        echo -ne "${CYAN}|${NC}  请选择 (a/b/c/d) [默认: b]: "
        read user_ratio
        case "${user_ratio,,}" in
            a) TARGET_RATIO=2 ;;
            c) TARGET_RATIO=4 ;;
            d) TARGET_RATIO=5 ;;
            *) TARGET_RATIO=3 ;;
        esac
        echo -e "${CYAN}+--------------------------------------------------------------+${NC}"

        echo ""

        # ─── 步骤 3: 流量配额 ─────────────────────────────────────────
        echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
        echo -e "${CYAN}|${NC}  ${BOLD}步骤 3/5${NC}  流量配额                                         ${CYAN}|${NC}"
        echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
        echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
        echo -ne "${CYAN}|${NC}  每日最大额外下载 (GB) [默认: 10]: "
        read user_quota
        DAILY_QUOTA="${user_quota:-10}"
        echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}  月流量总额度:                                               ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}    ${CYAN}0${NC} = 禁用    ${GREEN}-1${NC} = 无限    ${YELLOW}正数${NC} = 具体额度(GB)            ${CYAN}|${NC}"
        echo -ne "${CYAN}|${NC}  月额度 [默认: 0]: "
        read user_monthly
        MONTHLY_QUOTA="${user_monthly:-0}"
        echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
        echo -e "${CYAN}+--------------------------------------------------------------+${NC}"

        echo ""

        # ─── 步骤 4: 消息推送 ─────────────────────────────────────────
        echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
        echo -e "${CYAN}|${NC}  ${BOLD}步骤 4/5${NC}  消息推送                                         ${CYAN}|${NC}"
        echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
        echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
        echo -ne "${CYAN}|${NC}  启用消息推送？(y/N) [默认: N]: "
        read enable_notify
        NOTIFY_TYPE=""
        TG_ENABLED="false"
        TG_BOT_TOKEN=""
        TG_CHAT_ID=""
        TG_REPORT_FREQ="daily"
        TG_REPORT_HOUR="23"
        TG_MONTHLY_RESET_DAY="1"
        DINGTALK_ENABLED="false"
        DINGTALK_WEBHOOK=""
        DINGTALK_SECRET=""
        DINGTALK_REPORT_FREQ="daily"
        DINGTALK_REPORT_HOUR="23"
        DINGTALK_MONTHLY_RESET_DAY="1"

        if [[ "${enable_notify,,}" == "y" ]]; then
            echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
            echo -e "${CYAN}|${NC}  请选择推送方式:                                             ${CYAN}|${NC}"
            echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
            echo -e "${CYAN}|${NC}    ${CYAN}(1)${NC} 钉钉机器人  ${GREEN}← 推荐，国内服务器首选${NC}                    ${CYAN}|${NC}"
            echo -e "${CYAN}|${NC}    ${CYAN}(2)${NC} Telegram    ${YELLOW}需要能访问 api.telegram.org${NC}             ${CYAN}|${NC}"
            echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
            echo -e "${CYAN}|${NC}  ${YELLOW}⚠️  国内服务器建议选择钉钉机器人${NC}                            ${CYAN}|${NC}"
            echo -ne "${CYAN}|${NC}  请选择 (1/2): "
            read notify_choice

            if [[ "$notify_choice" == "2" ]]; then
                NOTIFY_TYPE="tg"
                TG_ENABLED="true"
                echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
                echo -e "${CYAN}|${NC}  创建 Bot: https://t.me/BotFather                           ${CYAN}|${NC}"
                echo -e "${CYAN}|${NC}  获取 Chat ID: https://t.me/userinfobot                     ${CYAN}|${NC}"

                local tg_configured=false
                while [[ "$tg_configured" == "false" ]]; do
                    echo -ne "${CYAN}|${NC}  Bot Token: "
                    read TG_BOT_TOKEN
                    echo -ne "${CYAN}|${NC}  Chat ID: "
                    read TG_CHAT_ID

                    log_step "发送测试消息..."
                    local test_result
                    test_result=$(curl -s -o /dev/null -w "%{http_code}" \
                        "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
                        -d "chat_id=${TG_CHAT_ID}" \
                        -d "text=🟢 Traffic Padding 测试消息" \
                        -d "parse_mode=HTML" 2>/dev/null)

                    if [[ "$test_result" == "200" ]]; then
                        echo -e "${CYAN}|${NC}  ${GREEN}[✓]${NC} 测试成功！请检查 TG 是否收到                        ${CYAN}|${NC}"
                        tg_configured=true
                    else
                        echo -e "${CYAN}|${NC}  ${RED}[✗]${NC} 测试失败 (HTTP ${test_result})                            ${CYAN}|${NC}"
                        echo -e "${CYAN}|${NC}    ${YELLOW}[R]${NC} 重新填写    ${YELLOW}[S]${NC} 跳过推送                            ${CYAN}|${NC}"
                        echo -ne "${CYAN}|${NC}  请选择 (R/S): "
                        read retry_choice
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
                    echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
                    echo -e "${CYAN}|${NC}  报告频率:                                                   ${CYAN}|${NC}"
                    echo -e "${CYAN}|${NC}    ${CYAN}(1)${NC} 日报    ${CYAN}(2)${NC} 周报    ${CYAN}(3)${NC} 月报                          ${CYAN}|${NC}"
                    echo -ne "${CYAN}|${NC}  选择 [默认: 1]: "
                    read freq_choice
                    case "${freq_choice}" in
                        2) TG_REPORT_FREQ="weekly" ;;
                        3)
                            TG_REPORT_FREQ="monthly"
                            echo -ne "${CYAN}|${NC}  月额度重置日（几号）[默认: 1]: "
                            read reset_day
                            TG_MONTHLY_RESET_DAY="${reset_day:-1}"
                            ;;
                        *) TG_REPORT_FREQ="daily" ;;
                    esac
                    echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
                    echo -e "${CYAN}|${NC}  推送时间 (24小时制):                                        ${CYAN}|${NC}"
                    echo -ne "${CYAN}|${NC}  每日推送时间 [默认: 23]: "
                    read report_hour
                    TG_REPORT_HOUR="${report_hour:-23}"

                    # 统计周期对齐选项
                    echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
                    echo -e "${CYAN}|${NC}  统计周期对齐方式:                                           ${CYAN}|${NC}"
                    echo -e "${CYAN}|${NC}    ${CYAN}(1)${NC} 自然日/周/月（00:00 开始）${GREEN}← 默认${NC}                   ${CYAN}|${NC}"
                    echo -e "${CYAN}|${NC}    ${CYAN}(2)${NC} 按推送时间（如 23:00-23:00）                       ${CYAN}|${NC}"
                    echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
                    echo -e "${CYAN}|${NC}  ${YELLOW}💡 如果选择按推送时间，日报将统计从今天${TG_REPORT_HOUR}:00到明天${TG_REPORT_HOUR}:00${NC}  ${CYAN}|${NC}"
                    echo -ne "${CYAN}|${NC}  选择 [默认: 1]: "
                    read align_choice
                    case "${align_choice}" in
                        2) TG_REPORT_ALIGN="push_time" ;;
                        *) TG_REPORT_ALIGN="natural" ;;
                    esac
                fi
            else
                NOTIFY_TYPE="dingtalk"
                DINGTALK_ENABLED="true"
                echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
                echo -e "${CYAN}|${NC}  创建钉钉机器人:                                             ${CYAN}|${NC}"
                echo -e "${CYAN}|${NC}  钉钉群 → 群设置 → 智能群助手 → 添加机器人 → 自定义            ${CYAN}|${NC}"
                echo -e "${CYAN}|${NC}  ${YELLOW}安全设置选「自定义关键词」，填写: Traffic Padding${NC}             ${CYAN}|${NC}"

                local dt_configured=false
                while [[ "$dt_configured" == "false" ]]; do
                    echo -ne "${CYAN}|${NC}  Webhook URL: "
                    read DINGTALK_WEBHOOK
                    echo -ne "${CYAN}|${NC}  加签密钥 (Secret，可留空): "
                    read DINGTALK_SECRET

                    log_step "发送测试消息..."
                    local dt_url="$DINGTALK_WEBHOOK"
                    if [[ -n "$DINGTALK_SECRET" ]]; then
                        local timestamp=$(($(date +%s) * 1000))
                        # 使用 $'\n' 插入真正的换行符
                        local string_to_sign="${timestamp}"$'\n'"${DINGTALK_SECRET}"
                        local sign=$(printf '%s' "$string_to_sign" | openssl dgst -sha256 -hmac "$DINGTALK_SECRET" -binary | base64 -w 0)
                        # 使用环境变量传递，避免命令注入
                        local sign_encoded=$(SIGN="$sign" python3 -c "import urllib.parse,os; print(urllib.parse.quote_plus(os.environ['SIGN']))" 2>/dev/null || echo "$sign")
                        dt_url="${dt_url}&timestamp=${timestamp}&sign=${sign_encoded}"
                    fi

                    local dt_test_result
                    dt_test_result=$(curl -s -o /dev/null -w "%{http_code}" \
                        -H "Content-Type: application/json" \
                        -d '{"msgtype":"markdown","markdown":{"title":"测试","text":"## 🟢 Traffic Padding 测试消息\n\n钉钉推送配置成功！"}}' \
                        "$dt_url" 2>/dev/null)

                    if [[ "$dt_test_result" == "200" ]]; then
                        echo -e "${CYAN}|${NC}  ${GREEN}[✓]${NC} 测试成功！请检查钉钉群是否收到                        ${CYAN}|${NC}"
                        dt_configured=true
                    else
                        echo -e "${CYAN}|${NC}  ${RED}[✗]${NC} 测试失败 (HTTP ${dt_test_result})                            ${CYAN}|${NC}"
                        echo -e "${CYAN}|${NC}    ${YELLOW}[R]${NC} 重新填写    ${YELLOW}[S]${NC} 跳过推送                            ${CYAN}|${NC}"
                        echo -ne "${CYAN}|${NC}  请选择 (R/S): "
                        read retry_choice
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
                    echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
                    echo -e "${CYAN}|${NC}  报告频率:                                                   ${CYAN}|${NC}"
                    echo -e "${CYAN}|${NC}    ${CYAN}(1)${NC} 日报    ${CYAN}(2)${NC} 周报    ${CYAN}(3)${NC} 月报                          ${CYAN}|${NC}"
                    echo -ne "${CYAN}|${NC}  选择 [默认: 1]: "
                    read freq_choice
                    case "${freq_choice}" in
                        2) DINGTALK_REPORT_FREQ="weekly" ;;
                        3)
                            DINGTALK_REPORT_FREQ="monthly"
                            echo -ne "${CYAN}|${NC}  月额度重置日（几号）[默认: 1]: "
                            read reset_day
                            DINGTALK_MONTHLY_RESET_DAY="${reset_day:-1}"
                            ;;
                        *) DINGTALK_REPORT_FREQ="daily" ;;
                    esac
                    echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
                    echo -e "${CYAN}|${NC}  推送时间 (24小时制):                                        ${CYAN}|${NC}"
                    echo -ne "${CYAN}|${NC}  每日推送时间 [默认: 23]: "
                    read report_hour
                    DINGTALK_REPORT_HOUR="${report_hour:-23}"

                    # 统计周期对齐选项
                    echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
                    echo -e "${CYAN}|${NC}  统计周期对齐方式:                                           ${CYAN}|${NC}"
                    echo -e "${CYAN}|${NC}    ${CYAN}(1)${NC} 自然日/周/月（00:00 开始）${GREEN}← 默认${NC}                   ${CYAN}|${NC}"
                    echo -e "${CYAN}|${NC}    ${CYAN}(2)${NC} 按推送时间（如 23:00-23:00）                       ${CYAN}|${NC}"
                    echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
                    echo -e "${CYAN}|${NC}  ${YELLOW}💡 如果选择按推送时间，日报将统计从今天${DINGTALK_REPORT_HOUR}:00到明天${DINGTALK_REPORT_HOUR}:00${NC}  ${CYAN}|${NC}"
                    echo -ne "${CYAN}|${NC}  选择 [默认: 1]: "
                    read align_choice
                    case "${align_choice}" in
                        2) DINGTALK_REPORT_ALIGN="push_time" ;;
                        *) DINGTALK_REPORT_ALIGN="natural" ;;
                    esac
                fi
            fi

            if [[ "$TG_ENABLED" == "true" || "$DINGTALK_ENABLED" == "true" ]]; then
                echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
                echo -e "${CYAN}|${NC}  ${YELLOW}💡 首次下载任务后将自动推送测试消息进行验证${NC}                 ${CYAN}|${NC}"
            fi
        fi
        echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
        echo -e "${CYAN}+--------------------------------------------------------------+${NC}"

        echo ""

        # ─── 步骤 5: 管理命令 ─────────────────────────────────────────
        echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
        echo -e "${CYAN}|${NC}  ${BOLD}步骤 5/5${NC}  基本设置                                         ${CYAN}|${NC}"
        echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
        echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
        echo -ne "${CYAN}|${NC}  服务器名称 [默认: Realm中转服务器]: "
        read server_name
        SERVER_NAME="${server_name:-Realm中转服务器}"
        echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
        echo -ne "${CYAN}|${NC}  快捷命令名称（1-3字符）[默认: tp]: "
        read user_cmd
        CMD_NAME="${user_cmd:-tp}"
        CMD_NAME="${CMD_NAME:0:3}"
        echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
        echo -e "${CYAN}+--------------------------------------------------------------+${NC}"

        echo ""

        # ─── 配置确认 ─────────────────────────────────────────────────
        echo -e "${CYAN}+===============================================================================+══════════════════════════════════════════════════════════════╗${NC}"
        echo -e "${CYAN}|${NC}  ${BOLD}📋 配置确认${NC}                                                ${CYAN}|${NC}"
        echo -e "${CYAN}+===============================================================================+══════════════════════════════════════════════════════════════╣${NC}"
        echo -e "${CYAN}|${NC}                                                              ${CYAN}|${NC}"
        printf "${CYAN}|${NC}    服务器名称  ${GREEN}%-46s${NC}${CYAN}|${NC}\n" "${SERVER_NAME}"
        printf "${CYAN}|${NC}    网卡        ${GREEN}%-46s${NC}${CYAN}|${NC}\n" "${INTERFACE}"
        printf "${CYAN}|${NC}    流量比例    ${GREEN}%-46s${NC}${CYAN}|${NC}\n" "1:${TARGET_RATIO}"
        printf "${CYAN}|${NC}    日配额      ${GREEN}%-46s${NC}${CYAN}|${NC}\n" "${DAILY_QUOTA} GB/天"
        printf "${CYAN}|${NC}    月额度      ${GREEN}%-46s${NC}${CYAN}|${NC}\n" "${MONTHLY_QUOTA} GB"
        if [[ "${TG_ENABLED}" == "true" ]]; then
            printf "${CYAN}|${NC}    推送方式    ${GREEN}%-46s${NC}${CYAN}|${NC}\n" "Telegram"
            printf "${CYAN}|${NC}    报告频率    ${GREEN}%-46s${NC}${CYAN}|${NC}\n" "${TG_REPORT_FREQ}"
            printf "${CYAN}|${NC}    推送时间    ${GREEN}%-46s${NC}${CYAN}|${NC}\n" "每日 ${TG_REPORT_HOUR}:00"
        elif [[ "${DINGTALK_ENABLED}" == "true" ]]; then
            printf "${CYAN}|${NC}    推送方式    ${GREEN}%-46s${NC}${CYAN}|${NC}\n" "钉钉机器人"
            printf "${CYAN}|${NC}    报告频率    ${GREEN}%-46s${NC}${CYAN}|${NC}\n" "${DINGTALK_REPORT_FREQ}"
            printf "${CYAN}|${NC}    推送时间    ${GREEN}%-46s${NC}${CYAN}|${NC}\n" "每日 ${DINGTALK_REPORT_HOUR}:00"
        else
            printf "${CYAN}|${NC}    推送方式    ${YELLOW}%-46s${NC}${CYAN}|${NC}\n" "未启用"
        fi
        printf "${CYAN}|${NC}    管理命令    ${GREEN}%-46s${NC}${CYAN}|${NC}\n" "${CMD_NAME}"
        echo -e "${CYAN}|${NC}                                                              ${CYAN}|${NC}"
        echo -e "${CYAN}+===============================================================================+══════════════════════════════════════════════════════════════╣${NC}"
        echo -e "${CYAN}|${NC}                                                              ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}    ${GREEN}[Y]${NC} 确认安装      ${YELLOW}[1-5]${NC} 重新设置      ${RED}[N]${NC} 取消            ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}                                                              ${CYAN}|${NC}"
        echo -ne "${CYAN}|${NC}  请选择: "
        read confirm
        echo -e "${CYAN}+===============================================================================+══════════════════════════════════════════════════════════════╝${NC}"

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
    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
    echo -e "${CYAN}|${NC}  ${BOLD}安装程序文件${NC}                                                ${CYAN}|${NC}"
    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
    mkdir -p "${INSTALL_DIR}"

    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [[ -f "${script_dir}/main.py" ]]; then
        cp "${script_dir}/main.py" "${INSTALL_DIR}/main.py"
        chmod 755 "${INSTALL_DIR}/main.py"
        echo -e "${CYAN}|${NC}  ${GREEN}[✓]${NC} main.py                                                ${CYAN}|${NC}"
    else
        echo -e "${CYAN}|${NC}  ${RED}[✗]${NC} 未找到 main.py                                         ${CYAN}|${NC}"
        echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
        exit 1
    fi
    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
}

# ============================================================================
# 生成管理脚本
# ============================================================================

generate_tpm() {
    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
    echo -e "${CYAN}|${NC}  ${BOLD}生成管理脚本${NC}                                                ${CYAN}|${NC}"
    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"

    cat > "${INSTALL_DIR}/tpm.sh" << 'TPM_EOF'
#!/bin/bash

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

SERVICE_NAME="traffic-padding"
CONFIG_DIR="/etc/traffic-padding"

show_header() {
    clear
    echo -e "${CYAN}+===========================================================================+${NC}"
    echo -e "${CYAN}|${NC}  ${BOLD}Traffic Padding Manager${NC}                                                ${CYAN}|${NC}"
    echo -e "${CYAN}+===========================================================================+${NC}"
    echo ""
}

get_status() {
    local status="${RED}● 已停止${NC}"
    systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null && status="${GREEN}● 运行中${NC}"
    local boot="${YELLOW}未启用${NC}"
    systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null && boot="${GREEN}已启用${NC}"

    local config=""
    [[ -f "${CONFIG_DIR}/config.json" ]] && config=$(python3 -c "
import json
with open('${CONFIG_DIR}/config.json') as f:
    c=json.load(f)
print(f\"网卡: {c.get('interface','?')}  比例: 1:{c.get('target_ratio','?')}  配额: {c.get('max_daily_extra_gb','?')}GB\")
" 2>/dev/null || echo "配置读取失败")

    local quota="今日: 无记录"
    [[ -f "${CONFIG_DIR}/usage.json" ]] && quota=$(python3 -c "
import json;from datetime import datetime as d
with open('${CONFIG_DIR}/usage.json') as f:
    p=json.load(f)
b=p.get('used_bytes',0)
t=d.now().strftime('%Y-%m-%d')
print(f\"今日: {b/1073741824:.3f} GB\" if p.get('date')==t else '今日: 0.000 GB')
" 2>/dev/null || echo "今日: 读取失败")

    echo -e "${CYAN}+---------------------------------------------------------------------------+${NC}"
    echo -e "${CYAN}|${NC}  状态: ${status}   自启: ${GREEN}${boot}${NC}                                          ${CYAN}|${NC}"
    echo -e "${CYAN}|${NC}  ${config}                                                  ${CYAN}|${NC}"
    echo -e "${CYAN}|${NC}  ${quota}                                                            ${CYAN}|${NC}"
    echo -e "${CYAN}+---------------------------------------------------------------------------+${NC}"
}

wait_key() {
    echo ""
    echo -ne "${DIM}  按 Enter 返回菜单...${NC}"
    read -r
}

need_root() {
    [[ $EUID -eq 0 ]] && return 0
    echo -e "${RED}  需要 root: sudo ${CMD_NAME}${NC}"
    wait_key
    return 1
}

do_uninstall() {
    echo ""
    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
    echo -e "${CYAN}|${NC}  ${RED}⚠️  一键卸载${NC}                                                ${CYAN}|${NC}"
    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
    echo -e "${CYAN}|${NC}  将删除: 服务、程序文件、管理命令                            ${CYAN}|${NC}"
    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
    echo ""
    read -rp "  确认卸载？(y/N): " confirm
    [[ "${confirm,,}" != "y" ]] && { echo "  已取消"; wait_key; return; }

    systemctl stop "${SERVICE_NAME}" 2>/dev/null
    systemctl disable "${SERVICE_NAME}" 2>/dev/null
    rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload
    rm -f "/usr/local/bin/tpm"
    rm -rf "/opt/traffic-padding"

    echo ""
    read -rp "  删除配置文件？(y/N): " del_config
    [[ "${del_config,,}" == "y" ]] && rm -rf "/etc/traffic-padding" && echo "  配置已删除"

    echo ""
    echo -e "${GREEN}  ✅ 卸载完成${NC}"
    echo "  https://github.com/linjunhao024-byte/Traffic-Tadding/issues"
    echo ""
    exit 0
}

log_info() { echo -e "  ${GREEN}[✓]${NC} $1"; }

CMD_NAME="$(basename "$0")"

# 格式化日志行（表格模式）
format_log_line() {
    local line="$1"
    local time=""
    local msg=""

    # 判断是否包含时间戳（journalctl 格式）
    if echo "$line" | grep -qE '^[A-Z][a-z]{2} +[0-9]+ +[0-9]+:[0-9]+:[0-9]+'; then
        # 有时间戳：提取时间和消息
        time=$(echo "$line" | grep -oE '[0-9]+:[0-9]+:[0-9]+' | head -1)
        msg=$(echo "$line" | sed -E 's/^[A-Z][a-z]{2} +[0-9]+ +[0-9]+:[0-9]+:[0-9]+ [^ ]+ [a-z-]+\[[0-9]+\]: //')
    else
        # 无时间戳（-o cat 模式）：使用当前时间
        time=$(date '+%H:%M:%S')
        msg="$line"
    fi

    # 根据消息类型设置颜色
    local color="${NC}"
    if echo "$msg" | grep -q "^\[INFO\]"; then
        color="${GREEN}"
    elif echo "$msg" | grep -q "^\[WARN\]"; then
        color="${YELLOW}"
    elif echo "$msg" | grep -q "^\[ERROR\]"; then
        color="${RED}"
    fi

    # 输出格式化行
    if [[ -n "$msg" ]]; then
        printf "  ${CYAN}│${NC}  %-10s ${color}%-58s${NC} ${CYAN}│${NC}\n" "$time" "$msg"
    fi
}

# 显示日志表头
show_log_header() {
    local title="$1"
    echo -e "${CYAN}+===========================================================================+${NC}"
    printf "${CYAN}|${NC}  ${BOLD}%-69s${NC}${CYAN}|${NC}\n" "$title"
    echo -e "${CYAN}+---------------------------------------------------------------------------+${NC}"
    printf "${CYAN}|${NC}  %-14s %-60s ${CYAN}|${NC}\n" "时间" "消息"
    echo -e "${CYAN}+---------------------------------------------------------------------------+${NC}"
}

# 显示日志表尾
show_log_footer() {
    echo -e "${CYAN}+===========================================================================+${NC}"
}

do_edit_config() {
    while true; do
        echo ""
        # 读取当前配置
        local config_data=$(python3 -c "
import json
with open('${CONFIG_DIR}/config.json') as f:
    c = json.load(f)
print(c.get('server_name', 'Realm中转服务器'))
print(c.get('target_ratio', 3))
print(c.get('max_daily_extra_gb', 10))
print(c.get('monthly_quota_gb', 0))
print(c.get('tg_enabled', False))
print(c.get('dingtalk_enabled', False))
print(c.get('tg_report_freq', c.get('dingtalk_report_freq', 'daily')))
print(c.get('tg_report_hour', c.get('dingtalk_report_hour', 23)))
" 2>/dev/null)

        local server_name=$(echo "$config_data" | sed -n '1p')
        local ratio=$(echo "$config_data" | sed -n '2p')
        local daily_quota=$(echo "$config_data" | sed -n '3p')
        local monthly_quota=$(echo "$config_data" | sed -n '4p')
        local tg_enabled=$(echo "$config_data" | sed -n '5p')
        local dt_enabled=$(echo "$config_data" | sed -n '6p')
        local freq=$(echo "$config_data" | sed -n '7p')
        local hour=$(echo "$config_data" | sed -n '8p')

        local push_status="未启用"
        [[ "$tg_enabled" == "True" ]] && push_status="Telegram"
        [[ "$dt_enabled" == "True" ]] && push_status="钉钉机器人"

        local freq_label="日报"
        [[ "$freq" == "weekly" ]] && freq_label="周报"
        [[ "$freq" == "monthly" ]] && freq_label="月报"

        local monthly_str="${monthly_quota} GB"
        [[ "$monthly_quota" == "-1" ]] && monthly_str="无限"
        [[ "$monthly_quota" == "0" ]] && monthly_str="禁用"

        echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
        echo -e "${CYAN}|${NC}  ${BOLD}编辑配置${NC}                                                    ${CYAN}|${NC}"
        echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
        echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
        printf "${CYAN}|${NC}    ${CYAN}[1]${NC} 服务器名称  ${GREEN}%-40s${NC}${CYAN}|${NC}\n" "${server_name}"
        printf "${CYAN}|${NC}    ${CYAN}[2]${NC} 流量比例    ${GREEN}%-40s${NC}${CYAN}|${NC}\n" "1:${ratio}"
        printf "${CYAN}|${NC}    ${CYAN}[3]${NC} 日配额      ${GREEN}%-40s${NC}${CYAN}|${NC}\n" "${daily_quota} GB"
        printf "${CYAN}|${NC}    ${CYAN}[4]${NC} 月额度      ${GREEN}%-40s${NC}${CYAN}|${NC}\n" "${monthly_str}"
        printf "${CYAN}|${NC}    ${CYAN}[5]${NC} 推送方式    ${GREEN}%-40s${NC}${CYAN}|${NC}\n" "${push_status}"
        printf "${CYAN}|${NC}    ${CYAN}[6]${NC} 报告频率    ${GREEN}%-40s${NC}${CYAN}|${NC}\n" "${freq_label}"
        printf "${CYAN}|${NC}    ${CYAN}[7]${NC} 推送时间    ${GREEN}%-40s${NC}${CYAN}|${NC}\n" "每日 ${hour}:00"
        echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}    ${CYAN}[8]${NC} 打开编辑器（高级）                                      ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}    ${CYAN}[0]${NC} 返回                                                        ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}                                                            ${CYAN}|${NC}"
        echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
        echo ""
        echo -ne "  请选择 [0-8]: "
        read edit_choice

        case "$edit_choice" in
            1)
                echo -ne "  新服务器名称 [当前: ${server_name}]: "
                read new_name
                if [[ -n "$new_name" ]]; then
                    python3 -c "
import json
with open('${CONFIG_DIR}/config.json', 'r') as f:
    config = json.load(f)
config['server_name'] = '${new_name}'
with open('${CONFIG_DIR}/config.json', 'w') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
"
                    log_info "已更新服务器名称"
                fi
                ;;
            2)
                echo "  流量比例: (a)1:2 (b)1:3 (c)1:4 (d)1:5"
                echo -ne "  选择 [当前: 1:${ratio}]: "
                read new_ratio
                local ratio_val=""
                case "${new_ratio,,}" in
                    a) ratio_val=2 ;;
                    b) ratio_val=3 ;;
                    c) ratio_val=4 ;;
                    d) ratio_val=5 ;;
                esac
                if [[ -n "$ratio_val" ]]; then
                    python3 -c "
import json
with open('${CONFIG_DIR}/config.json', 'r') as f:
    config = json.load(f)
config['target_ratio'] = ${ratio_val}
with open('${CONFIG_DIR}/config.json', 'w') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
"
                    log_info "已更新流量比例为 1:${ratio_val}"
                fi
                ;;
            3)
                echo -ne "  新日配额 (GB) [当前: ${daily_quota}]: "
                read new_quota
                if [[ -n "$new_quota" ]] && [[ "$new_quota" =~ ^[0-9]+$ ]]; then
                    python3 -c "
import json
with open('${CONFIG_DIR}/config.json', 'r') as f:
    config = json.load(f)
config['max_daily_extra_gb'] = ${new_quota}
with open('${CONFIG_DIR}/config.json', 'w') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
"
                    log_info "已更新日配额为 ${new_quota} GB"
                fi
                ;;
            4)
                echo "  月额度: 0=禁用 -1=无限 正数=具体额度(GB)"
                echo -ne "  新月额度 [当前: ${monthly_str}]: "
                read new_monthly
                if [[ -n "$new_monthly" ]]; then
                    python3 -c "
import json
with open('${CONFIG_DIR}/config.json', 'r') as f:
    config = json.load(f)
config['monthly_quota_gb'] = ${new_monthly}
with open('${CONFIG_DIR}/config.json', 'w') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
"
                    log_info "已更新月额度"
                fi
                ;;
            5)
                echo "  推送方式: (1) 钉钉机器人 (2) Telegram (0) 禁用"
                echo -ne "  选择: "
                read push_choice
                case "$push_choice" in
                    1)
                        echo -ne "  Webhook URL: "
                        read webhook
                        echo -ne "  加签密钥 (可留空): "
                        read secret
                        python3 -c "
import json
with open('${CONFIG_DIR}/config.json', 'r') as f:
    config = json.load(f)
config['tg_enabled'] = False
config['dingtalk_enabled'] = True
config['dingtalk_webhook'] = '${webhook}'
config['dingtalk_secret'] = '${secret}'
with open('${CONFIG_DIR}/config.json', 'w') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
"
                        log_info "已配置钉钉推送"
                        ;;
                    2)
                        echo -ne "  Bot Token: "
                        read token
                        echo -ne "  Chat ID: "
                        read chatid
                        python3 -c "
import json
with open('${CONFIG_DIR}/config.json', 'r') as f:
    config = json.load(f)
config['tg_enabled'] = True
config['tg_bot_token'] = '${token}'
config['tg_chat_id'] = '${chatid}'
config['dingtalk_enabled'] = False
with open('${CONFIG_DIR}/config.json', 'w') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
"
                        log_info "已配置 Telegram 推送"
                        ;;
                    0)
                        python3 -c "
import json
with open('${CONFIG_DIR}/config.json', 'r') as f:
    config = json.load(f)
config['tg_enabled'] = False
config['dingtalk_enabled'] = False
with open('${CONFIG_DIR}/config.json', 'w') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
"
                        log_info "已禁用推送"
                        ;;
                esac
                ;;
            6)
                echo "  报告频率: (1) 日报 (2) 周报 (3) 月报"
                echo -ne "  选择 [当前: ${freq_label}]: "
                read freq_choice
                local new_freq=""
                case "$freq_choice" in
                    1) new_freq="daily" ;;
                    2) new_freq="weekly" ;;
                    3) new_freq="monthly" ;;
                esac
                if [[ -n "$new_freq" ]]; then
                    python3 -c "
import json
with open('${CONFIG_DIR}/config.json', 'r') as f:
    config = json.load(f)
if config.get('tg_enabled'):
    config['tg_report_freq'] = '${new_freq}'
if config.get('dingtalk_enabled'):
    config['dingtalk_report_freq'] = '${new_freq}'
with open('${CONFIG_DIR}/config.json', 'w') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
"
                    log_info "已更新报告频率"
                fi
                ;;
            7)
                echo -ne "  推送时间 (0-23) [当前: ${hour}]: "
                read new_hour
                if [[ -n "$new_hour" ]] && [[ "$new_hour" =~ ^[0-9]+$ ]] && [[ "$new_hour" -ge 0 ]] && [[ "$new_hour" -le 23 ]]; then
                    python3 -c "
import json
with open('${CONFIG_DIR}/config.json', 'r') as f:
    config = json.load(f)
if config.get('tg_enabled'):
    config['tg_report_hour'] = ${new_hour}
if config.get('dingtalk_enabled'):
    config['dingtalk_report_hour'] = ${new_hour}
with open('${CONFIG_DIR}/config.json', 'w') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
"
                    log_info "已更新推送时间为 ${new_hour}:00"
                fi
                ;;
            8)
                ${EDITOR:-nano} "${CONFIG_DIR}/config.json"
                ;;
            0)
                return
                ;;
        esac
    done
}

get_auto_panel_status() {
    if grep -q "tpm$" ~/.bashrc 2>/dev/null; then
        echo -e "${GREEN}已开启${NC}"
    else
        echo -e "${DIM}已关闭${NC}"
    fi
}

get_download_mode() {
    local mode=$(python3 -c "
import json
try:
    with open('${CONFIG_DIR}/config.json') as f:
        print(json.load(f).get('download_mode', 'short'))
except:
    print('short')
" 2>/dev/null)
    case "$mode" in
        short) echo -e "${GREEN}短时${NC} (2-15MB)" ;;
        long) echo -e "${YELLOW}长时${NC} (完整文件)" ;;
        mixed) echo -e "${CYAN}长短结合${NC}" ;;
        *) echo -e "${GREEN}短时${NC}" ;;
    esac
}

set_download_mode() {
    local mode="$1"
    python3 -c "
import json
with open('${CONFIG_DIR}/config.json', 'r') as f:
    config = json.load(f)
config['download_mode'] = '${mode}'
with open('${CONFIG_DIR}/config.json', 'w') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
" 2>/dev/null
}

do_traffic_monitor() {
    while true; do
        echo ""
        echo -e "${CYAN}+======================================================================+${NC}"
        echo -e "${CYAN}|${NC}  ${BOLD}流量监控${NC}                                                               ${CYAN}|${NC}"
        echo -e "${CYAN}+======================================================================+${NC}"
        echo -e "${CYAN}|${NC}                                                                    ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}    ${CYAN}(a)${NC} 今日                                                              ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}    ${CYAN}(b)${NC} 本周                                                              ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}    ${CYAN}(c)${NC} 本月                                                              ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}    ${CYAN}(d)${NC} 启用至今                                                          ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}                                                                    ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}    ${CYAN}(0)${NC} 返回                                                              ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}                                                                    ${CYAN}|${NC}"
        echo -e "${CYAN}+======================================================================+${NC}"
        echo ""
        echo -ne "  请选择 (a/b/c/d/0): "
        read period_choice

        case "${period_choice,,}" in
            a|b|c|d)
                local period=""
                case "${period_choice,,}" in
                    a) period="daily" ;;
                    b) period="weekly" ;;
                    c) period="monthly" ;;
                    d) period="total" ;;
                esac

                # 调用 Python 获取流量数据并绘制柱状图
                python3 << PYEOF
import json
import os
from datetime import datetime, timedelta

CONFIG_DIR = "${CONFIG_DIR}"
TRAFFIC_HISTORY_FILE = os.path.join(CONFIG_DIR, "traffic_history.json")

def load_history():
    try:
        with open(TRAFFIC_HISTORY_FILE, 'r') as f:
            return json.load(f)
    except:
        return []

def load_config():
    try:
        with open(os.path.join(CONFIG_DIR, "config.json"), 'r') as f:
            return json.load(f)
    except:
        return {}

def get_period_summary(history, period):
    now = datetime.now()
    config = load_config()

    # 获取推送时间和对齐方式
    report_hour = config.get('dingtalk_report_hour', config.get('tg_report_hour', 23))
    report_align = config.get('dingtalk_report_align', config.get('tg_report_align', 'natural'))

    if period == 'total':
        start_time = 0
        label = "启用至今"
    else:
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
                label = now.strftime('%m月%d日')
            elif period == 'weekly':
                start = today_start - timedelta(days=today_start.weekday())
                label = f"本周 ({start.strftime('%m/%d')}-{now.strftime('%m/%d')})"
            elif period == 'monthly':
                start = today_start.replace(day=1)
                label = now.strftime('%Y年%m月')

        start_time = start.timestamp()

    period_data = [h for h in history if h.get('timestamp', 0) >= start_time]

    if not period_data or len(period_data) < 2:
        return None

    first = period_data[0]
    last = period_data[-1]

    rx_delta = max(0, last.get('rx_bytes', 0) - first.get('rx_bytes', 0))
    tx_delta = max(0, last.get('tx_bytes', 0) - first.get('tx_bytes', 0))
    download_delta = max(0, last.get('download_bytes', 0) - first.get('download_bytes', 0))

    return {
        'label': label,
        'rx_mb': rx_delta / (1024 * 1024),
        'tx_mb': tx_delta / (1024 * 1024),
        'download_mb': download_delta / (1024 * 1024),
    }

def draw_bar(label, value, max_value, width=40):
    if max_value <= 0:
        filled = 0
    else:
        filled = int((value / max_value) * width)
    bar = '█' * filled + '░' * (width - filled)
    return f"  {label:12s} {bar}  {value:>8.1f} MB"

history = load_history()
summary = get_period_summary(history, "${period}")

if not summary:
    print()
    print("  +----------------------------------------------------------------------+")
    print("  |  流量监控                                                            |")
    print("  +----------------------------------------------------------------------+")
    print("  |                                                                      |")
    print("  |    数据不足，请等待一段时间后再查看                                   |")
    print("  |                                                                      |")
    print("  +----------------------------------------------------------------------+")
else:
    max_val = max(summary['rx_mb'], summary['tx_mb'], summary['download_mb'], 1)
    ratio_str = "1:{:.1f}".format(summary['rx_mb'] / summary['tx_mb']) if summary['tx_mb'] > 0 else "N/A"

    print()
    print(f"  +----------------------------------------------------------------------+")
    print(f"  |  流量监控 - {summary['label']:<54s}  |")
    print(f"  +----------------------------------------------------------------------+")
    print(f"  |                                                                      |")
    print(f"  |{draw_bar('上行 (TX)', summary['tx_mb'], max_val)}   |")
    print(f"  |                                                                      |")
    print(f"  |{draw_bar('下行 (RX)', summary['rx_mb'], max_val)}   |")
    print(f"  |                                                                      |")
    print(f"  |{draw_bar('填充下载', summary['download_mb'], max_val)}   |")
    print(f"  |                                                                      |")
    print(f"  +----------------------------------------------------------------------+")
    print(f"  |  比例: {ratio_str:<10s}  目标: 1:{summary['rx_mb']/summary['tx_mb'] if summary['tx_mb'] > 0 else 0:.1f}                                  |")
    print(f"  +----------------------------------------------------------------------+")
PYEOF
                echo ""
                echo -ne "  按 Enter 返回..."
                read -r
                ;;
            0)
                return
                ;;
            *)
                echo -e "  ${RED}无效选项${NC}"
                sleep 1
                ;;
        esac
    done
}

do_update() {
    echo ""
    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
    echo -e "${CYAN}|${NC}  ${BOLD}一键更新${NC}                                                    ${CYAN}|${NC}"
    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
    echo -e "${CYAN}|${NC}  正在从 GitHub 下载最新版本...                                ${CYAN}|${NC}"
    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
    echo ""

    # 检测是否能访问 GitHub
    local mirror_url="https://ghfast.top/"
    if curl -s --connect-timeout 3 https://raw.githubusercontent.com > /dev/null 2>&1; then
        mirror_url=""
    fi

    # 下载 main.py
    echo -ne "  下载 main.py..."
    if curl -sL "${mirror_url}https://raw.githubusercontent.com/linjunhao024-byte/Traffic-Tadding/main/main.py" -o /tmp/main.py.new; then
        echo -e " ${GREEN}✓${NC}"
    else
        echo -e " ${RED}✗${NC}"
        echo -e "  ${RED}下载失败，请检查网络${NC}"
        wait_key
        return 1
    fi

    # 下载 install.sh
    echo -ne "  下载 install.sh..."
    if curl -sL "${mirror_url}https://raw.githubusercontent.com/linjunhao024-byte/Traffic-Tadding/main/install.sh" -o /tmp/install.sh.new; then
        echo -e " ${GREEN}✓${NC}"
    else
        echo -e " ${RED}✗${NC}"
        echo -e "  ${RED}下载失败，请检查网络${NC}"
        wait_key
        return 1
    fi

    # 备份并替换
    echo -ne "  备份旧文件..."
    cp "${INSTALL_DIR}/main.py" "${INSTALL_DIR}/main.py.bak" 2>/dev/null
    cp "${INSTALL_DIR}/tpm.sh" "${INSTALL_DIR}/tpm.sh.bak" 2>/dev/null
    echo -e " ${GREEN}✓${NC}"

    echo -ne "  更新 main.py..."
    mv /tmp/main.py.new "${INSTALL_DIR}/main.py"
    chmod 755 "${INSTALL_DIR}/main.py"
    echo -e " ${GREEN}✓${NC}"

    echo -ne "  更新管理脚本..."
    mv /tmp/install.sh.new /tmp/install.sh
    # 获取当前快捷命令名称
    local current_cmd=$(basename "$0")
    # 执行更新，显示错误信息
    if CMD_NAME="$current_cmd" bash /tmp/install.sh --update-tpm-only; then
        echo -e " ${GREEN}✓${NC}"
    else
        echo -e " ${RED}✗${NC}"
        echo -e "  ${RED}管理脚本更新失败，正在恢复备份...${NC}"
        cp "${INSTALL_DIR}/tpm.sh.bak" "${INSTALL_DIR}/tpm.sh" 2>/dev/null
    fi

    echo ""
    echo -e "${GREEN}  ✅ 更新完成！${NC}"
    echo -e "  建议重启服务: ${CYAN}systemctl restart traffic-padding${NC}"
    echo ""
    wait_key
}

main() {
    while true; do
        show_header
        get_status
        echo ""

        echo -e "${CYAN}+===========================================================================+${NC}"
        echo -e "${CYAN}|${NC}  ${BOLD}服务控制${NC}                                                                ${CYAN}|${NC}"
        echo -e "${CYAN}+--------------------------------------+------------------------------------+${NC}"
        echo -e "${CYAN}|${NC}  ${CYAN}[1]${NC} 查看状态                        ${CYAN}|${NC}  ${CYAN}[5]${NC} 实时日志                      ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}  ${CYAN}[2]${NC} 启动服务                        ${CYAN}|${NC}  ${CYAN}[6]${NC} 最近日志                      ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}  ${CYAN}[3]${NC} 停止服务                        ${CYAN}|${NC}  ${GREEN}[7]${NC} 手动推送                      ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}  ${CYAN}[4]${NC} 重启服务                        ${CYAN}|${NC}  ${CYAN}[8]${NC} 查看配置                      ${CYAN}|${NC}"
        echo -e "${CYAN}+--------------------------------------+------------------------------------+${NC}"
        echo -e "${CYAN}|${NC}  ${BOLD}系统管理${NC}                                                                ${CYAN}|${NC}"
        echo -e "${CYAN}+===========================================================================+${NC}"
        echo -e "${CYAN}|${NC}  ${CYAN}[9]${NC} 编辑配置     ${CYAN}[10]${NC} 开机自启     ${CYAN}[11]${NC} 取消自启                      ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}  ${CYAN}[12]${NC} 网卡测试     ${RED}[13]${NC} 卸载         ${GREEN}[14]${NC} 一键更新                      ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}  ${YELLOW}[15]${NC} 自动面板: $(get_auto_panel_status)                                                   ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}  ${GREEN}[16]${NC} 流量监控                                                               ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}  ${MAGENTA}[17]${NC} 下载模式: $(get_download_mode)                                                ${CYAN}|${NC}"
        echo -e "${CYAN}+===========================================================================+${NC}"
        echo -e "${CYAN}|${NC}  ${CYAN}[0]${NC} 退出                                                                    ${CYAN}|${NC}"
        echo -e "${CYAN}+===========================================================================+${NC}"
        echo ""
        echo -ne "  请选择 [0-17]: "
        read choice

        case "$choice" in
            1)
                echo ""
                # 获取服务状态信息
                local is_active=$(systemctl is-active "${SERVICE_NAME}" 2>/dev/null)
                local is_enabled=$(systemctl is-enabled "${SERVICE_NAME}" 2>/dev/null)
                local pid=$(systemctl show -p MainPID "${SERVICE_NAME}" 2>/dev/null | cut -d= -f2)
                local memory=$(systemctl show -p MemoryCurrent "${SERVICE_NAME}" 2>/dev/null | cut -d= -f2)
                local uptime=$(systemctl show -p ActiveEnterTimestamp "${SERVICE_NAME}" 2>/dev/null | cut -d= -f2)

                # 格式化内存
                if [[ -n "$memory" && "$memory" != "[not set]" ]]; then
                    memory="$(( memory / 1024 / 1024 )) MB"
                else
                    memory="N/A"
                fi

                # 格式化运行时间
                if [[ -n "$uptime" ]]; then
                    local start_ts=$(date -d "$uptime" +%s 2>/dev/null)
                    local now_ts=$(date +%s)
                    local diff=$(( now_ts - start_ts ))
                    local days=$(( diff / 86400 ))
                    local hours=$(( (diff % 86400) / 3600 ))
                    local mins=$(( (diff % 3600) / 60 ))
                    if [[ $days -gt 0 ]]; then
                        uptime="${days}天${hours}小时${mins}分钟"
                    elif [[ $hours -gt 0 ]]; then
                        uptime="${hours}小时${mins}分钟"
                    else
                        uptime="${mins}分钟"
                    fi
                else
                    uptime="N/A"
                fi

                # 状态颜色
                local status_color="${RED}"
                [[ "$is_active" == "active" ]] && status_color="${GREEN}"

                local boot_color="${YELLOW}"
                [[ "$is_enabled" == "enabled" ]] && boot_color="${GREEN}"

                # 显示格式化状态
                echo -e "${CYAN}+===========================================================================+${NC}"
                printf "${CYAN}|${NC}  ${BOLD}%-69s${NC}${CYAN}|${NC}\n" "服务状态"
                echo -e "${CYAN}+---------------------------------------------------------------------------+${NC}"
                printf "${CYAN}|${NC}  状态: ${status_color}%-10s${NC}  自启: ${boot_color}%-10s${NC}  PID: %-10s       ${CYAN}|${NC}\n" "$is_active" "$is_enabled" "$pid"
                printf "${CYAN}|${NC}  内存: %-10s  运行时间: %-30s   ${CYAN}|${NC}\n" "$memory" "$uptime"
                echo -e "${CYAN}+---------------------------------------------------------------------------+${NC}"
                printf "${CYAN}|${NC}  ${BOLD}%-69s${NC}${CYAN}|${NC}\n" "最近日志 (5条)"
                echo -e "${CYAN}+---------------------------------------------------------------------------+${NC}"
                journalctl -u "${SERVICE_NAME}" -n 5 --no-pager -o cat | while IFS= read -r line; do
                    format_log_line "$line"
                done
                echo -e "${CYAN}+===========================================================================+${NC}"
                wait_key
                ;;
            2)
                need_root && systemctl start "${SERVICE_NAME}" && log_info "服务已启动"
                wait_key
                ;;
            3)
                need_root && systemctl stop "${SERVICE_NAME}" && log_info "服务已停止"
                wait_key
                ;;
            4)
                need_root && systemctl restart "${SERVICE_NAME}" && log_info "服务已重启"
                wait_key
                ;;
            5)
                echo ""
                show_log_header "实时日志"
                # 使用 tail -f 实时跟踪，并格式化输出
                journalctl -u "${SERVICE_NAME}" -f --no-pager -o cat | while IFS= read -r line; do
                    format_log_line "$(date '+%b %d %H:%M:%S') $line"
                done &
                local jctl_pid=$!
                echo -e "${YELLOW}  按 Enter 返回菜单...${NC}"
                read -r
                kill $jctl_pid 2>/dev/null
                wait $jctl_pid 2>/dev/null
                show_log_footer
                ;;
            6)
                echo ""
                show_log_header "最近日志 (50条)"
                journalctl -u "${SERVICE_NAME}" -n 50 --no-pager | while IFS= read -r line; do
                    format_log_line "$line"
                done
                show_log_footer
                wait_key
                ;;
            7)
                if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
                    systemctl kill -s SIGUSR1 "${SERVICE_NAME}"
                    echo ""
                    log_info "已发送推送请求，请检查钉钉/TG"
                else
                    echo ""
                    echo -e "  ${RED}[✗]${NC} 服务未运行"
                fi
                wait_key
                ;;
            8)
                echo ""
                if [[ -f "${CONFIG_DIR}/config.json" ]]; then
                    python3 -c "
import json
with open('${CONFIG_DIR}/config.json') as f:
    print(json.dumps(json.load(f), indent=2, ensure_ascii=False))
" 2>/dev/null || cat "${CONFIG_DIR}/config.json"
                else
                    echo "  配置不存在"
                fi
                wait_key
                ;;
            9)
                need_root && do_edit_config
                ;;
            10)
                need_root && systemctl enable "${SERVICE_NAME}" && log_info "已启用开机自启"
                wait_key
                ;;
            11)
                need_root && systemctl disable "${SERVICE_NAME}" && log_info "已取消开机自启"
                wait_key
                ;;
            12)
                echo ""
                local iface=$(python3 -c "import json;print(json.load(open('${CONFIG_DIR}/config.json')).get('interface','eth0'))" 2>/dev/null || echo 'eth0')
                if grep -q "${iface}:" /proc/net/dev 2>/dev/null; then
                    log_info "网卡 ${iface} 存在"
                    echo ""
                    cat /proc/net/dev | head -3
                    grep "${iface}:" /proc/net/dev
                else
                    echo -e "  ${RED}[✗]${NC} 网卡 ${iface} 不存在"
                fi
                wait_key
                ;;
            13)
                need_root && do_uninstall
                ;;
            14)
                need_root && do_update
                ;;
            15)
                echo ""
                if grep -q "tpm$" ~/.bashrc 2>/dev/null; then
                    sed -i '/tpm$/d' ~/.bashrc
                    log_info "已关闭自动面板"
                else
                    echo "# Traffic Padding 管理面板" >> ~/.bashrc
                    echo "tpm" >> ~/.bashrc
                    log_info "已开启自动面板（下次登录生效）"
                fi
                wait_key
                ;;
            16)
                do_traffic_monitor
                ;;
            17)
                echo ""
                echo -e "${CYAN}+===========================================================================+${NC}"
                printf "${CYAN}|${NC}  ${BOLD}%-69s${NC}${CYAN}|${NC}\n" "下载模式切换"
                echo -e "${CYAN}+===========================================================================+${NC}"
                echo -e "${CYAN}|${NC}                                                                    ${CYAN}|${NC}"
                echo -e "${CYAN}|${NC}    ${GREEN}(a)${NC} 短时模式    下载 2-15MB，快速完成                              ${CYAN}|${NC}"
                echo -e "${CYAN}|${NC}    ${YELLOW}(b)${NC} 长时模式    下载完整文件，持续 1-5 分钟                        ${CYAN}|${NC}"
                echo -e "${CYAN}|${NC}    ${CYAN}(c)${NC} 长短结合    交替使用两种模式                                  ${CYAN}|${NC}"
                echo -e "${CYAN}|${NC}                                                                    ${CYAN}|${NC}"
                echo -e "${CYAN}|${NC}  ${DIM}短时模式：更隐蔽，流量可控${NC}                                          ${CYAN}|${NC}"
                echo -e "${CYAN}|${NC}  ${DIM}长时模式：更真实，像人类下载${NC}                                        ${CYAN}|${NC}"
                echo -e "${CYAN}|${NC}  ${DIM}长短结合：随机切换，兼顾隐蔽和真实${NC}                                  ${CYAN}|${NC}"
                echo -e "${CYAN}|${NC}                                                                    ${CYAN}|${NC}"
                echo -e "${CYAN}+===========================================================================+${NC}"
                echo ""
                echo -ne "  请选择 (a/b/c): "
                read mode_choice
                case "${mode_choice,,}" in
                    a)
                        set_download_mode "short"
                        log_info "已切换到短时模式"
                        ;;
                    b)
                        set_download_mode "long"
                        log_info "已切换到长时模式"
                        ;;
                    c)
                        set_download_mode "mixed"
                        log_info "已切换到长短结合模式"
                        ;;
                    *)
                        echo -e "  ${RED}无效选项${NC}"
                        ;;
                esac
                wait_key
                ;;
            0)
                clear
                exit 0
                ;;
            *)
                echo -e "  ${RED}无效选项${NC}"
                sleep 1
                ;;
        esac
    done
}

main
TPM_EOF

    chmod 755 "${INSTALL_DIR}/tpm.sh"
    ln -sf "${INSTALL_DIR}/tpm.sh" "/usr/local/bin/${CMD_NAME}"
    echo -e "${CYAN}|${NC}  ${GREEN}[✓]${NC} 管理命令: ${CMD_NAME}                                             ${CYAN}|${NC}"
    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
}

# ============================================================================
# 生成配置和服务
# ============================================================================

generate_config() {
    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
    echo -e "${CYAN}|${NC}  ${BOLD}生成配置文件${NC}                                                ${CYAN}|${NC}"
    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
    mkdir -p "${CONFIG_DIR}"

    cat > "${CONFIG_DIR}/config.json" << EOF
{
    "server_name": "${SERVER_NAME}",
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
    "tg_report_hour": ${TG_REPORT_HOUR},
    "tg_report_align": "${TG_REPORT_ALIGN:-natural}",
    "tg_monthly_reset_day": ${TG_MONTHLY_RESET_DAY},
    "dingtalk_enabled": ${DINGTALK_ENABLED},
    "dingtalk_webhook": "${DINGTALK_WEBHOOK}",
    "dingtalk_secret": "${DINGTALK_SECRET}",
    "dingtalk_report_freq": "${DINGTALK_REPORT_FREQ}",
    "dingtalk_report_hour": ${DINGTALK_REPORT_HOUR},
    "dingtalk_report_align": "${DINGTALK_REPORT_ALIGN:-natural}",
    "dingtalk_monthly_reset_day": ${DINGTALK_MONTHLY_RESET_DAY},
    "qos_probe_enabled": true,
    "qos_probe_targets": ["https://cn.bing.com", "https://www.baidu.com", "https://cdn.aliyundcdntest.com/test_1m", "https://dl.google.com", "https://www.apple.com"],
    "qos_probe_count": 5,
    "download_mode": "short"
}
EOF
    echo -e "${CYAN}|${NC}  ${GREEN}[✓]${NC} 配置: ${CONFIG_DIR}/config.json                               ${CYAN}|${NC}"
    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
}

generate_service() {
    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
    echo -e "${CYAN}|${NC}  ${BOLD}注册系统服务${NC}                                                ${CYAN}|${NC}"
    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"

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
    echo -e "${CYAN}|${NC}  ${GREEN}[✓]${NC} 服务已注册                                              ${CYAN}|${NC}"
    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
}

start_service() {
    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
    echo -e "${CYAN}|${NC}  ${BOLD}启动服务${NC}                                                    ${CYAN}|${NC}"
    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
    systemctl enable "${SERVICE_NAME}" 2>/dev/null
    systemctl start "${SERVICE_NAME}"
    sleep 2

    if systemctl is-active --quiet "${SERVICE_NAME}"; then
        echo -e "${CYAN}|${NC}  ${GREEN}[✓]${NC} 服务启动成功                                            ${CYAN}|${NC}"
    else
        echo -e "${CYAN}|${NC}  ${RED}[✗]${NC} 服务启动失败                                            ${CYAN}|${NC}"
        echo -e "${CYAN}|${NC}  查看日志: journalctl -u ${SERVICE_NAME} -n 20                    ${CYAN}|${NC}"
        echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
        exit 1
    fi
    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
}

# ============================================================================
# 完成 & 卸载
# ============================================================================

show_success() {
    echo ""
    echo -e "${GREEN}+===============================================================================+═══════════════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}|${NC}                                                                              ${GREEN}|${NC}"
    echo -e "${GREEN}|${NC}                    🎉  ${BOLD}安装成功！${NC}  🎉                                       ${GREEN}|${NC}"
    echo -e "${GREEN}|${NC}                                                                              ${GREEN}|${NC}"
    echo -e "${GREEN}+===============================================================================+═══════════════════════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${GREEN}|${NC}                                                                              ${GREEN}|${NC}"
    echo -e "${GREEN}|${NC}  ${BOLD}管理命令:${NC}                                                                   ${GREEN}|${NC}"
    printf "${GREEN}|${NC}    ${CYAN}%-12s${NC}  呼出管理菜单                                              ${GREEN}|${NC}\n" "${CMD_NAME}"
    echo -e "${GREEN}|${NC}                                                                              ${GREEN}|${NC}"
    echo -e "${GREEN}|${NC}  ${BOLD}常用命令:${NC}                                                                   ${GREEN}|${NC}"
    printf "${GREEN}|${NC}    ${CYAN}%-44s${NC} 查看状态   ${GREEN}|${NC}\n" "systemctl status ${SERVICE_NAME}"
    printf "${GREEN}|${NC}    ${CYAN}%-44s${NC} 查看日志   ${GREEN}|${NC}\n" "journalctl -u ${SERVICE_NAME} -f"
    printf "${GREEN}|${NC}    ${CYAN}%-44s${NC} 重启服务   ${GREEN}|${NC}\n" "systemctl restart ${SERVICE_NAME}"
    echo -e "${GREEN}|${NC}                                                                              ${GREEN}|${NC}"
    echo -e "${GREEN}|${NC}  ${BOLD}配置文件:${NC}                                                                   ${GREEN}|${NC}"
    printf "${GREEN}|${NC}    ${CYAN}%-44s${NC} 热重载     ${GREEN}|${NC}\n" "${CONFIG_DIR}/config.json"
    echo -e "${GREEN}|${NC}                                                                              ${GREEN}|${NC}"
    echo -e "${GREEN}+===============================================================================+═══════════════════════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${GREEN}|${NC}  如果这个项目对你有帮助，请给一个 ${YELLOW}⭐ Star${NC}！                                  ${GREEN}|${NC}"
    echo -e "${GREEN}|${NC}  ${CYAN}https://github.com/linjunhao024-byte/Traffic-Tadding${NC}                        ${GREEN}|${NC}"
    echo -e "${GREEN}+===============================================================================+═══════════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

uninstall() {
    echo ""
    echo -e "${YELLOW}  正在卸载...${NC}"

    systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
    systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
    rm -f "${SERVICE_FILE}"
    systemctl daemon-reload
    rm -f /usr/local/bin/tp
    rm -f /usr/local/bin/tpm
    [[ -n "$CMD_NAME" ]] && rm -f "/usr/local/bin/${CMD_NAME}"
    rm -rf "${INSTALL_DIR}"

    read -rp "  删除配置文件 ${CONFIG_DIR}？(y/n) [n]: " del
    [[ "${del,,}" == "y" ]] && rm -rf "${CONFIG_DIR}" && echo "  配置已删除"

    echo ""
    echo -e "${GREEN}  ✅ 卸载完成${NC}"
    echo "  https://github.com/linjunhao024-byte/Traffic-Tadding/issues"
    echo ""
}

# ============================================================================
# 主流程
# ============================================================================

# 仅更新管理脚本模式（供一键更新使用）
if [[ "$1" == "--update-tpm-only" ]]; then
    # 如果环境变量 CMD_NAME 已设置（从 do_update 传递），则使用它
    # 否则默认为 tp
    if [[ -z "$CMD_NAME" ]]; then
        CMD_NAME="tp"
    fi
    generate_tpm
    exit 0
fi

main() {
    echo -e "${CYAN}"
    echo "+===============================================================================+═══════════════════════════════════════════════════════════════════════════════╗"
    echo "|                                                                               |"
    echo "|           Traffic Padding Micro-Service 安装程序                               |"
    echo "|                          流量伪装微服务                                        |"
    echo "|                                                                               |"
    echo "+===============================================================================+═══════════════════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"

    if [[ "$1" == "uninstall" || "$1" == "-u" ]]; then
        check_root
        uninstall
        exit 0
    fi

    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
    echo -e "${CYAN}|${NC}  ${BOLD}环境检查${NC}                                                    ${CYAN}|${NC}"
    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
    check_root
    check_commands
    echo -e "${CYAN}|${NC}  ${GREEN}[✓]${NC} 环境检查通过                                             ${CYAN}|${NC}"
    echo -e "${CYAN}+--------------------------------------------------------------+${NC}"
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
