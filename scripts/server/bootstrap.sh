#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 server (run as root on the server).
# Installs Docker, uv and tmux, adds 4 GB swap, and creates user `debate`
# (docker group, same SSH keys as root). The repo is pushed by push.sh.
set -euo pipefail

apt-get update -q
apt-get install -y -q ca-certificates curl git rsync tmux htop
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi

if ! swapon --show | grep -q /swapfile; then
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

if ! id debate >/dev/null 2>&1; then
  useradd -m -s /bin/bash -G docker debate
  mkdir -p /home/debate/.ssh
  cp /root/.ssh/authorized_keys /home/debate/.ssh/
  chown -R debate:debate /home/debate/.ssh
  chmod 700 /home/debate/.ssh
fi

su - debate -c 'command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh'
echo "bootstrap done"
