#!/usr/bin/env bash
# hc-logs - browse this machine's logs and learn what each one records.
# Installed by HardeningChef. Run with sudo to read protected logs.
set -uo pipefail

ID=$(. /etc/os-release 2>/dev/null; printf '%s %s' "${ID:-}" "${ID_LIKE:-}")
case " $ID " in
  *" debian "*|*" ubuntu "*|*" kali "*) FAMILY=debian; SSH_UNIT=ssh ;;
  *) FAMILY=rhel; SSH_UNIT=sshd ;;
esac

PAGER_CMD=(less -R +G)
command -v less >/dev/null 2>&1 || PAGER_CMD=(more)

# Known sources: kind|target|title|description
#   file    - a path under /var/log
#   cmd     - a command whose output is shown
#   journal - arguments to journalctl
sources() {
  cat <<'EOF'
journal|-b|Journal: this boot|Everything logged by every service and the kernel since the last boot.
journal|-p err -b|Journal: errors this boot|Only messages of priority "error" or worse since boot. Start here when something is broken.
journal|SYSLOG_FACILITY=4 SYSLOG_FACILITY=10|Journal: authentication|Logins, sudo use, su, PAM and account changes.
journal|-u SSHUNIT|Journal: SSH server|SSH connections, accepted keys (with fingerprints), failed logins and disconnects.
journal|-k|Journal: kernel|Kernel messages: hardware, drivers, AppArmor/SELinux denials, firewall blocks.
firewall|-|Journal: firewall blocks|Packets the firewall rejected or dropped (when logging is on).
cmd|last -F|Login history (wtmp)|Successful logins and reboots, newest first.
cmd|lastb -F|Failed logins (btmp)|Failed login attempts: a quick view of password-guessing activity.
cmd|systemctl --failed --no-pager|Failed services|Systemd units that are currently in a failed state.
file|/var/log/hardeningchef.log|HardeningChef log|Every change made by the HardeningChef hardening script.
file|/var/log/audit/audit.log|Audit log (auditd)|Kernel audit events: SELinux AVC denials, watched files, privileged commands.
file|/var/log/auth.log|Authentication log|Logins, sudo, SSH and PAM messages (Debian family, needs rsyslog).
file|/var/log/secure|Authentication log|Logins, sudo, SSH and PAM messages (Fedora/Rocky, needs rsyslog).
file|/var/log/syslog|System log|General messages from services and the kernel (Debian family, needs rsyslog).
file|/var/log/messages|System log|General messages from services and the kernel (Fedora/Rocky, needs rsyslog).
file|/var/log/kern.log|Kernel log|Kernel messages, including firewall blocks (Debian family, needs rsyslog).
file|/var/log/ufw.log|UFW firewall log|Packets blocked or allowed by ufw rules.
file|/var/log/firewalld|firewalld log|firewalld daemon warnings and errors.
file|/var/log/dpkg.log|Package changes (dpkg)|Every package install, upgrade and removal.
file|/var/log/apt/history.log|APT history|Which apt commands were run, by whom, and what they changed.
file|/var/log/apt/term.log|APT terminal output|Full output of package operations.
file|/var/log/dnf.log|DNF log|Package manager activity (Fedora/Rocky).
file|/var/log/dnf5.log|DNF5 log|Package manager activity (Fedora).
file|/var/log/dnf.rpm.log|RPM transactions|Every package installed, upgraded or removed.
file|/var/log/cron|Cron log|Scheduled job runs (Fedora/Rocky, needs rsyslog).
file|/var/log/maillog|Mail log|Mail server activity (Fedora/Rocky).
file|/var/log/mail.log|Mail log|Mail server activity (Debian family).
file|/var/log/boot.log|Boot log|Service start-up messages from the last boot.
file|/var/log/cloud-init.log|cloud-init log|First-boot provisioning on cloud images: users, keys, network.
file|/var/log/unattended-upgrades/unattended-upgrades.log|Automatic updates|Security updates applied automatically.
file|/var/log/fail2ban.log|fail2ban log|IP addresses banned for repeated failures.
EOF
}

describe_file() {
  local base=${1##*/}; base=${base%.gz}; base=${base%.[0-9]}
  local line
  line=$(sources | awk -F'|' -v b="$base" '$1 == "file" { n = $2; sub(/.*\//, "", n); if (n == b) { print $4; exit } }')
  case "$base" in
    wtmp) line="Binary login history. Viewed with: last" ;;
    btmp) line="Binary failed-login history. Viewed with: lastb" ;;
    lastlog|lastlog2.db) line="Binary last-login database. Viewed with: lastlog" ;;
  esac
  printf '%s' "${line:-No description available.}"
}

view() {
  local kind=$1 target=$2
  case "$kind" in
    file)
      if [ ! -r "$target" ]; then echo "Cannot read $target (try: sudo hc-logs)"; pause; return; fi
      case "$target" in
        *.gz) zcat -f "$target" | "${PAGER_CMD[@]}" ;;
        */wtmp*) last -F -f "$target" | "${PAGER_CMD[@]}" ;;
        */btmp*) lastb -F -f "$target" | "${PAGER_CMD[@]}" ;;
        *) "${PAGER_CMD[@]}" "$target" ;;
      esac ;;
    cmd)
      # shellcheck disable=SC2086
      $target 2>&1 | "${PAGER_CMD[@]}" ;;
    journal)
      local -a args
      read -r -a args <<<"${target//SSHUNIT/$SSH_UNIT}"
      journalctl --no-hostname "${args[@]}" ;;
    firewall)
      journalctl --no-hostname -k --grep='UFW BLOCK|_REJECT|_DROP' ;;
  esac
}

pause() { read -r -p "Press Enter to continue..." _ </dev/tty; }

available() {
  local kind target title desc
  while IFS='|' read -r kind target title desc; do
    if [ "$kind" = file ] && [ ! -e "$target" ]; then continue; fi
    if [[ $kind == journal || $kind == firewall ]] && ! command -v journalctl >/dev/null 2>&1; then continue; fi
    if [ "$kind" = cmd ] && ! command -v "${target%% *}" >/dev/null 2>&1; then continue; fi
    printf '%s|%s|%s|%s\n' "$kind" "$target" "$title" "$desc"
  done < <(sources)
}

# Menu helper: whiptail when available, otherwise a numbered list.
choose() {
  local title=$1; shift
  if command -v whiptail >/dev/null 2>&1 && [ -t 0 ]; then
    local args=() i=1 item
    for item in "$@"; do args+=("$i" "$item"); i=$((i + 1)); done
    whiptail --title "hc-logs" --menu "$title" 24 100 16 "${args[@]}" 3>&1 1>&2 2>&3
  else
    local i=1 item a
    echo; echo "== $title =="
    for item in "$@"; do printf '%3d) %s\n' "$i" "$item"; i=$((i + 1)); done
    read -r -p "Choose a number (q to go back): " a </dev/tty
    [[ $a =~ ^[0-9]+$ ]] && (( a >= 1 && a <= $# )) && echo "$a"
  fi
}

menu_sources() {
  local -a rows labels
  mapfile -t rows < <(available)
  local r
  for r in "${rows[@]}"; do
    IFS='|' read -r _ _ title desc <<<"$r"
    labels+=("$(printf '%-28s %s' "$title" "$desc" | cut -c1-90)")
  done
  while true; do
    local n; n=$(choose "Log sources on this system ($FAMILY family)" "${labels[@]}") || return
    [ -n "$n" ] || return
    IFS='|' read -r kind target title desc <<<"${rows[$((n - 1))]}"
    clear 2>/dev/null; printf '%s\n%s\n\n' "$title" "$desc"
    sleep 1
    view "$kind" "$target"
  done
}

menu_files() {
  local -a files labels
  mapfile -t files < <(find /var/log -type f ! -name '*.journal' ! -name '*.journal~' 2>/dev/null | sort)
  local f
  for f in "${files[@]}"; do labels+=("$(printf '%-45s %s' "${f#/var/log/}" "$(describe_file "$f")" | cut -c1-95)"); done
  while true; do
    local n; n=$(choose "All files in /var/log" "${labels[@]}") || return
    [ -n "$n" ] || return
    view file "${files[$((n - 1))]}"
  done
}

[ "$(id -u)" -eq 0 ] || echo "Note: not running as root; protected logs will be unreadable. Use: sudo hc-logs"
while true; do
  n=$(choose "What would you like to look at?" \
      "Log sources, with what each one records" \
      "Browse every file in /var/log" \
      "Quit") || exit 0
  case "$n" in
    1) menu_sources ;;
    2) menu_files ;;
    *) exit 0 ;;
  esac
done
