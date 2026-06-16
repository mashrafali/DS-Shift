#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${DS_SHIFT_REPO_URL:-https://github.com/mashrafali/DS-Shift.git}"
BRANCH="${DS_SHIFT_BRANCH:-main}"
INSTALL_DIR="${DS_SHIFT_INSTALL_DIR:-/opt/ds-shift}"
SKIP_DOCKER_INSTALL="${DS_SHIFT_SKIP_DOCKER_INSTALL:-false}"
SKIP_DEPLOY="${DS_SHIFT_SKIP_DEPLOY:-false}"
SKIP_VALIDATE="${DS_SHIFT_SKIP_VALIDATE:-false}"
ADMIN_USERNAME="${DS_SHIFT_ADMIN_INITIAL_USERNAME:-}"
ADMIN_PASSWORD="${DS_SHIFT_ADMIN_INITIAL_PASSWORD:-}"
POSTGRES_PASSWORD="${DS_SHIFT_POSTGRES_PASSWORD:-}"

log() {
  printf '[ds-shift] %s\n' "$*"
}

fail() {
  printf '[ds-shift] ERROR: %s\n' "$*" >&2
  exit 1
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    fail "Run this installer as root. Example: curl -fsSL <raw-url>/install-ds-shift.sh | sudo bash"
  fi
}

need_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

install_base_packages() {
  if command -v git >/dev/null 2>&1 && command -v curl >/dev/null 2>&1 && command -v openssl >/dev/null 2>&1; then
    return
  fi
  if command -v dnf >/dev/null 2>&1; then
    dnf -y install git curl openssl ca-certificates >/dev/null
    return
  fi
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update >/dev/null
    apt-get install -y git curl openssl ca-certificates >/dev/null
    return
  fi
  fail "Unsupported package manager. Install git, curl, and openssl manually."
}

install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return
  fi
  if [[ "${SKIP_DOCKER_INSTALL}" == "true" ]]; then
    fail "Docker is unavailable and DS_SHIFT_SKIP_DOCKER_INSTALL=true was set"
  fi
  if command -v dnf >/dev/null 2>&1; then
    dnf -y install dnf-plugins-core curl ca-certificates >/dev/null
    dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo >/dev/null
    dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
    systemctl enable --now docker
    return
  fi
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    . /etc/os-release
    if [[ -z "${ID:-}" || -z "${VERSION_CODENAME:-}" ]]; then
      fail "Could not determine apt-based distribution details from /etc/os-release"
    fi
    curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' \
      "$(dpkg --print-architecture)" \
      "${ID}" \
      "${VERSION_CODENAME}" \
      > /etc/apt/sources.list.d/docker.list
    apt-get update >/dev/null
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
    systemctl enable --now docker
    return
  fi
  fail "Unsupported platform for automatic Docker installation"
}

clone_or_update_repo() {
  mkdir -p "$(dirname "${INSTALL_DIR}")"
  if [[ -d "${INSTALL_DIR}/.git" ]]; then
    log "Updating existing repository in ${INSTALL_DIR}"
    git -C "${INSTALL_DIR}" remote set-url origin "${REPO_URL}"
    git -C "${INSTALL_DIR}" fetch --tags origin
    git -C "${INSTALL_DIR}" checkout "${BRANCH}"
    git -C "${INSTALL_DIR}" pull --ff-only origin "${BRANCH}"
    return
  fi
  if [[ -e "${INSTALL_DIR}" ]]; then
    fail "${INSTALL_DIR} exists but is not a git repository"
  fi
  log "Cloning ${REPO_URL} into ${INSTALL_DIR}"
  git clone --branch "${BRANCH}" --single-branch "${REPO_URL}" "${INSTALL_DIR}"
}

random_secret() {
  openssl rand -base64 32 | tr -d '\n'
}

urlencode() {
  python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"
}

current_env_value() {
  local env_file="$1"
  local key="$2"
  awk -F= -v key="${key}" '$1 == key {sub(/^[^=]*=/, "", $0); print $0; exit}' "${env_file}"
}

set_env_value() {
  local env_file="$1"
  local key="$2"
  local value="$3"
  if grep -q "^${key}=" "${env_file}"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "${env_file}"
  else
    printf '%s=%s\n' "${key}" "${value}" >> "${env_file}"
  fi
}

ensure_env_file() {
  local env_file="${INSTALL_DIR}/.env"
  local current_postgres_password
  local current_admin_username
  local current_admin_password
  if [[ ! -f "${env_file}" ]]; then
    cp "${INSTALL_DIR}/.env.example" "${env_file}"
  fi

  current_postgres_password="$(current_env_value "${env_file}" "POSTGRES_PASSWORD")"
  current_admin_username="$(current_env_value "${env_file}" "ADMIN_INITIAL_USERNAME")"
  current_admin_password="$(current_env_value "${env_file}" "ADMIN_INITIAL_PASSWORD")"

  if [[ -z "${POSTGRES_PASSWORD}" && ( -z "${current_postgres_password}" || "${current_postgres_password}" == "change-me-to-a-random-local-secret" ) ]]; then
    POSTGRES_PASSWORD="$(random_secret)"
  fi
  if [[ -z "${POSTGRES_PASSWORD}" ]]; then
    POSTGRES_PASSWORD="${current_postgres_password}"
  fi

  if [[ -z "${ADMIN_PASSWORD}" && ( -z "${current_admin_password}" || "${current_admin_password}" == "change-me-before-production" ) ]]; then
    ADMIN_PASSWORD="$(random_secret)"
  fi
  if [[ -z "${ADMIN_PASSWORD}" ]]; then
    ADMIN_PASSWORD="${current_admin_password}"
  fi
  if [[ -z "${current_admin_username}" ]]; then
    current_admin_username="admin"
  fi
  if [[ -z "${ADMIN_USERNAME}" ]]; then
    ADMIN_USERNAME="${current_admin_username}"
  fi

  set_env_value "${env_file}" "POSTGRES_PASSWORD" "${POSTGRES_PASSWORD}"
  set_env_value "${env_file}" "POSTGRES_PASSWORD_URLENCODED" "$(urlencode "${POSTGRES_PASSWORD}")"
  set_env_value "${env_file}" "ADMIN_INITIAL_USERNAME" "${ADMIN_USERNAME}"
  set_env_value "${env_file}" "ADMIN_INITIAL_PASSWORD" "${ADMIN_PASSWORD}"
  if ! grep -q '^SPARK_LIVE_EXECUTION_ENABLED=' "${env_file}"; then
    printf 'SPARK_LIVE_EXECUTION_ENABLED=true\n' >> "${env_file}"
  fi
}

deploy_stack() {
  log "Deploying DS Shift"
  (cd "${INSTALL_DIR}" && ./scripts/deploy.sh)
}

ensure_host_staging() {
  mkdir -p /DS-Shift-Staging
}

validate_stack() {
  log "Validating DS Shift"
  (cd "${INSTALL_DIR}" && ./scripts/validate.sh)
}

print_summary() {
  local host_ip
  host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  printf '\n'
  log "Installation complete"
  printf 'Install directory: %s\n' "${INSTALL_DIR}"
  printf 'Admin username: %s\n' "${ADMIN_USERNAME}"
  printf 'Admin password: %s\n' "${ADMIN_PASSWORD}"
  if [[ -n "${host_ip}" ]]; then
    printf 'GUI: https://%s/\n' "${host_ip}"
    printf 'API health: https://%s/api/health\n' "${host_ip}"
  else
    printf 'GUI: https://<host>/\n'
    printf 'API health: https://<host>/api/health\n'
  fi
}

main() {
  require_root
  install_base_packages
  need_command git
  need_command curl
  need_command openssl
  install_docker
  need_command docker
  clone_or_update_repo
  ensure_env_file
  ensure_host_staging
  if [[ "${SKIP_DEPLOY}" == "true" ]]; then
    log "Skipping deployment because DS_SHIFT_SKIP_DEPLOY=true"
    print_summary
    exit 0
  fi
  deploy_stack
  if [[ "${SKIP_VALIDATE}" != "true" ]]; then
    validate_stack
  fi
  print_summary
}

main "$@"
