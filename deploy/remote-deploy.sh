#!/bin/bash
# Installs one release of AEGIS on the Lightsail server. Run by GitHub Actions over SSH as:
#   sudo bash /tmp/remote-deploy.sh /tmp/aegis.tgz <git-sha> <hostname>
# The .env (secrets) is written separately to /opt/aegis/shared/.env before this runs.
set -euo pipefail
ARCHIVE="$1"; SHA="$2"; HOST="$3"
BASE=/opt/aegis
REL="$BASE/releases/$SHA"

echo "== waiting for first-boot setup"
for i in $(seq 1 90); do [ -f /var/lib/aegis-bootstrap-done ] && break; sleep 10; done
[ -f /var/lib/aegis-bootstrap-done ] || { echo "first-boot setup did not finish:"; tail -n 40 /var/log/aegis-bootstrap.log 2>/dev/null; exit 1; }

echo "== unpacking release $SHA"
rm -rf "$REL" && mkdir -p "$REL"
tar -xzf "$ARCHIVE" -C "$REL"
ln -sfn "$BASE/shared/.env" "$REL/.env"

echo "== python environment"
[ -x "$BASE/venv/bin/python" ] || python3 -m venv "$BASE/venv"
"$BASE/venv/bin/pip" install -q --upgrade pip
# CPU-only PyTorch first (the default wheel pulls ~2 GB of GPU libraries this server can't use)
"$BASE/venv/bin/pip" install -q torch --index-url https://download.pytorch.org/whl/cpu
"$BASE/venv/bin/pip" install -q -r "$REL/requirements.txt"

echo "== static dashboard (only the public files, never the code or .env)"
rm -rf "$BASE/web.new" && mkdir -p "$BASE/web.new"
cp "$REL/aegis-mission-control.html" "$REL/aegis-spacecraft-sim-2d.html" "$REL/aegis-api-client.js" "$BASE/web.new/"
# The dashboard's styles and scripts (split out of the HTML, loaded in the same order)
cp -r "$REL/css" "$REL/js" "$BASE/web.new/"
[ -d "$REL/artifacts" ] && cp -r "$REL/artifacts" "$BASE/web.new/"
mkdir -p "$BASE/web.new/docs" && [ -d "$REL/docs/screenshots" ] && cp -r "$REL/docs/screenshots" "$BASE/web.new/docs/"
rm -rf "$BASE/web.old"; [ -d "$BASE/web" ] && mv "$BASE/web" "$BASE/web.old"; mv "$BASE/web.new" "$BASE/web"

chown -R aegis:aegis "$BASE"
# Caddy (user "caddy") may read only the public web folder; code, venv and secrets stay private to "aegis"
chmod 755 "$BASE"
chmod -R a+rX "$BASE/web"
chmod 750 "$BASE/releases" "$BASE/venv"
chmod 700 "$BASE/shared"
chmod 600 "$BASE/shared/.env"
ln -sfn "$REL" "$BASE/current"

echo "== monitoring: Prometheus + node exporter (localhost only) and Grafana (public at /grafana, read-only)"
MON="$REL/deploy/monitoring"
APT="apt-get -o DPkg::Lock::Timeout=600 -y -q"
export DEBIAN_FRONTEND=noninteractive
if ! command -v prometheus >/dev/null 2>&1 || ! [ -x /usr/bin/prometheus-node-exporter ]; then
  $APT update >/dev/null && $APT install --no-install-recommends prometheus prometheus-node-exporter >/dev/null
fi
if ! [ -x /usr/sbin/grafana-server ] && ! [ -x /usr/share/grafana/bin/grafana ]; then
  $APT install apt-transport-https gnupg wget >/dev/null
  mkdir -p /etc/apt/keyrings
  wget -q -O - https://apt.grafana.com/gpg.key | gpg --dearmor --yes -o /etc/apt/keyrings/grafana.gpg
  echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" > /etc/apt/sources.list.d/grafana.list
  $APT update >/dev/null && $APT install grafana >/dev/null
fi
# Prometheus: our scrape config and alert rules, 7 days of data, listening on 127.0.0.1 only
install -m 644 "$MON/prometheus.yml" /etc/prometheus/prometheus.yml
install -m 644 "$MON/aegis-alerts.yml" /etc/prometheus/aegis-alerts.yml
echo 'ARGS="--web.listen-address=127.0.0.1:9090 --storage.tsdb.retention.time=7d"' > /etc/default/prometheus
echo 'ARGS="--web.listen-address=127.0.0.1:9100"' > /etc/default/prometheus-node-exporter
# Grafana: served under https://$HOST/grafana/, anonymous visitors can view (read-only), admin password kept on the box
GPW="$BASE/shared/grafana-admin-password"
[ -s "$GPW" ] || { openssl rand -base64 24 | tr -d '/+=' | head -c 24 > "$GPW"; }
chmod 600 "$GPW"
cat > /etc/default/grafana-server <<GRAF
GRAFANA_USER=grafana
GRAFANA_GROUP=grafana
GRAFANA_HOME=/usr/share/grafana
LOG_DIR=/var/log/grafana
DATA_DIR=/var/lib/grafana
MAX_OPEN_FILES=10000
CONF_DIR=/etc/grafana
CONF_FILE=/etc/grafana/grafana.ini
RESTART_ON_UPGRADE=true
PLUGINS_DIR=/var/lib/grafana/plugins
PROVISIONING_CFG_DIR=/etc/grafana/provisioning
PID_FILE_DIR=/var/run/grafana
GF_SERVER_HTTP_ADDR=127.0.0.1
GF_SERVER_HTTP_PORT=3000
GF_SERVER_ROOT_URL=https://$HOST/grafana/
GF_SERVER_SERVE_FROM_SUB_PATH=true
GF_SECURITY_ADMIN_USER=admin
GF_SECURITY_ADMIN_PASSWORD=$(cat "$GPW")
GF_SECURITY_COOKIE_SECURE=true
GF_USERS_ALLOW_SIGN_UP=false
GF_AUTH_ANONYMOUS_ENABLED=true
GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer
GF_ANALYTICS_REPORTING_ENABLED=false
GF_ANALYTICS_CHECK_FOR_UPDATES=false
GF_NEWS_NEWS_FEED_ENABLED=false
GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/var/lib/grafana/dashboards/aegis/aegis-backend.json
GRAF
chmod 640 /etc/default/grafana-server; chgrp grafana /etc/default/grafana-server 2>/dev/null || true
mkdir -p /etc/grafana/provisioning/datasources /etc/grafana/provisioning/dashboards /var/lib/grafana/dashboards/aegis
install -m 644 "$MON/grafana/provisioning/datasources/prometheus.yml" /etc/grafana/provisioning/datasources/aegis-prometheus.yml
install -m 644 "$MON/grafana/provisioning/dashboards/aegis.yml" /etc/grafana/provisioning/dashboards/aegis.yml
install -m 644 "$MON/grafana/dashboards/aegis-backend.json" /var/lib/grafana/dashboards/aegis/aegis-backend.json
chown -R grafana:grafana /var/lib/grafana/dashboards
systemctl daemon-reload
systemctl enable prometheus prometheus-node-exporter grafana-server >/dev/null 2>&1
systemctl restart prometheus-node-exporter prometheus grafana-server

echo "== services"
cat > /etc/systemd/system/aegis.service <<UNIT
[Unit]
Description=AEGIS backend (FastAPI)
After=network-online.target
Wants=network-online.target

[Service]
User=aegis
WorkingDirectory=$BASE/current
EnvironmentFile=$BASE/shared/.env
Environment=PYTHONUTF8=1
Environment=HF_HOME=$BASE/shared/hf-cache
ExecStart=$BASE/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/caddy/Caddyfile <<CADDY
$HOST {
	encode gzip
	@api path /api/* /health /docs /docs/* /openapi.json /redoc
	handle @api {
		reverse_proxy 127.0.0.1:8000
	}
	handle /grafana* {
		reverse_proxy 127.0.0.1:3000
	}
	handle / {
		redir * /aegis-mission-control.html
	}
	handle {
		root * $BASE/web
		file_server
	}
	header {
		Strict-Transport-Security "max-age=31536000"
		X-Content-Type-Options nosniff
		Referrer-Policy strict-origin-when-cross-origin
	}
}
CADDY

systemctl daemon-reload
systemctl enable aegis >/dev/null 2>&1
systemctl restart aegis
systemctl enable caddy >/dev/null 2>&1
systemctl reload caddy || systemctl restart caddy

echo "== waiting for the backend (first start downloads the embedding model)"
for i in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null; then echo "backend healthy"; break; fi
  sleep 5
done
curl -fsS http://127.0.0.1:8000/health >/dev/null || { journalctl -u aegis -n 80 --no-pager; exit 1; }

echo "== monitoring check"
for i in $(seq 1 30); do curl -fsS http://127.0.0.1:3000/grafana/api/health >/dev/null 2>&1 && break; sleep 2; done
curl -fsS http://127.0.0.1:3000/grafana/api/health >/dev/null && echo "grafana healthy" || { journalctl -u grafana-server -n 40 --no-pager; echo "WARNING: grafana not healthy"; }
curl -fsS "http://127.0.0.1:9090/-/ready" >/dev/null && echo "prometheus ready" || { journalctl -u prometheus -n 40 --no-pager; echo "WARNING: prometheus not ready"; }

echo "== keeping the 3 newest releases"
ls -1dt "$BASE"/releases/* | tail -n +4 | xargs -r rm -rf
echo "== deployed $SHA to https://$HOST/"
