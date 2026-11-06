# First boot of the AEGIS Lightsail server. Lightsail appends this to its own launch script and runs it with
# /bin/sh, so the real (bash) bootstrap is written to a file and run explicitly with bash.
mkdir -p /opt/aegis-bootstrap
cat > /opt/aegis-bootstrap/bootstrap.sh <<'BOOTSTRAP'
#!/bin/bash
# First boot of the AEGIS Lightsail server: OS packages, swap, Caddy and the app user/folders.
# The app itself is installed by deploy/remote-deploy.sh (run by GitHub Actions over SSH).
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

# 2 GB of swap: headroom for pip installing PyTorch and for the embedding model on a 2 GB server
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

apt-get update -y
apt-get install -y python3-venv python3-pip git curl debian-keyring debian-archive-keyring apt-transport-https

# Caddy (automatic HTTPS). Ubuntu's own package first, the official Caddy repository as a fallback.
if ! apt-get install -y caddy; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y && apt-get install -y caddy
fi

id aegis >/dev/null 2>&1 || useradd --system --create-home --home-dir /opt/aegis --shell /usr/sbin/nologin aegis
mkdir -p /opt/aegis/releases /opt/aegis/shared /opt/aegis/web
chown -R aegis:aegis /opt/aegis
touch /var/lib/aegis-bootstrap-done
BOOTSTRAP
bash /opt/aegis-bootstrap/bootstrap.sh > /var/log/aegis-bootstrap.log 2>&1
