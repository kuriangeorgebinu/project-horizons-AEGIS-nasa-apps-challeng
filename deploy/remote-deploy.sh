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
[ -f /var/lib/aegis-bootstrap-done ] || { echo "first-boot setup did not finish"; exit 1; }

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
[ -d "$REL/artifacts" ] && cp -r "$REL/artifacts" "$BASE/web.new/"
mkdir -p "$BASE/web.new/docs" && [ -d "$REL/docs/screenshots" ] && cp -r "$REL/docs/screenshots" "$BASE/web.new/docs/"
rm -rf "$BASE/web.old"; [ -d "$BASE/web" ] && mv "$BASE/web" "$BASE/web.old"; mv "$BASE/web.new" "$BASE/web"

chown -R aegis:aegis "$BASE"
chmod 600 "$BASE/shared/.env"
ln -sfn "$REL" "$BASE/current"

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

echo "== keeping the 3 newest releases"
ls -1dt "$BASE"/releases/* | tail -n +4 | xargs -r rm -rf
echo "== deployed $SHA to https://$HOST/"
