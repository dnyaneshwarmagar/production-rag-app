#!/bin/bash
# Amazon Linux 2023, run as root after the repo is in /opt/rag and .env is filled in.
#   sudo bash deploy/ec2-bootstrap.sh
set -euo pipefail

dnf update -y
dnf install -y docker
systemctl enable --now docker
if ! docker compose version >/dev/null 2>&1; then
  mkdir -p /usr/libexec/docker/cli-plugins
  arch=$(uname -m)
  case "$arch" in
    aarch64|arm64) compose_arch=aarch64 ;;
    *) compose_arch=x86_64 ;;
  esac
  curl -fsSL "https://github.com/docker/compose/releases/download/v2.32.4/docker-compose-linux-${compose_arch}" \
    -o /usr/libexec/docker/cli-plugins/docker-compose
  chmod +x /usr/libexec/docker/cli-plugins/docker-compose
fi

if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

usermod -aG docker ec2-user || true
cd /opt/rag
docker compose -f docker-compose.aws.yml up -d --build
docker compose -f docker-compose.aws.yml ps
