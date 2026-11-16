#!/bin/bash
# One-time setup of the "vphone" WSL distro (Ubuntu 26.04) for vphone-linux.
# Run as root: creates user vphone (passwordless sudo), enables systemd, base tools.
set -euo pipefail
id vphone >/dev/null 2>&1 || useradd -m -s /bin/bash -G sudo vphone
echo 'vphone ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/vphone
chmod 440 /etc/sudoers.d/vphone
cat > /etc/wsl.conf <<'EOF'
[boot]
systemd=true

[user]
default=vphone
EOF
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl ca-certificates sudo > /dev/null
su - vphone -c 'test -d ~/vphone-linux || git clone -q https://github.com/YahiaAngelo/vphone-linux.git ~/vphone-linux'
su - vphone -c 'cd ~/vphone-linux && git submodule update --init -q && git submodule status'
