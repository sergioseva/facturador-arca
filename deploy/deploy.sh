#!/usr/bin/env bash
# Despliega/actualiza el facturador en el server `prod` (Docker + Caddy).
#
# Uso:  ./deploy/deploy.sh
#
# Sube el código (sin secretos ni datos), reconstruye la imagen y levanta el
# contenedor. Los secretos (secrets/.env y secrets/certs/) y la base (data/)
# viven en el server y NO se tocan acá — ver deploy/DEPLOY.md para el alta inicial.
set -euo pipefail

HOST="${DEPLOY_HOST:-prod}"
REMOTE="/aplicaciones/facturador"
cd "$(dirname "$0")/.."

echo "→ Sincronizando código a $HOST:$REMOTE/src ..."
rsync -az --delete \
  --exclude '.git' --exclude '.venv' --exclude 'venv' \
  --exclude 'data' --exclude 'certs' --exclude '.env' \
  --exclude '__pycache__' --exclude '*.bak*' --exclude 'deploy' \
  ./ "$HOST:$REMOTE/src/"

echo "→ Copiando compose ..."
scp deploy/docker-compose.yml "$HOST:$REMOTE/docker-compose.yml"

echo "→ Build + up en el server ..."
ssh "$HOST" "cd $REMOTE && docker compose up -d --build"

echo "✅ Desplegado. Estado:"
ssh "$HOST" "docker ps --filter name=facturador --format '   {{.Names}}  {{.Status}}'"
