# Deploy en `prod` (Docker + Caddy)

El facturador corre en el server **`prod`** (`pedidos.librosmario.store`) como un
contenedor Docker, detrás del **Caddy** que ya enruta las otras apps. Vive en
`/aplicaciones/facturador/`, aislado del compose de pedidos.

```
/aplicaciones/facturador/
├── docker-compose.yml        # copiado por deploy.sh
├── src/                      # código (sincronizado por deploy.sh)
├── secrets/
│   ├── .env                  # config + secretos (NO en git)
│   └── certs/                # prod.crt + prod.key (NO en git)
└── data/                     # base SQLite + cache del token (persistente)
```

Auto-arranque: el contenedor usa `restart: unless-stopped`, así que **al
reiniciar el server levanta solo** (lo maneja `docker.service`).

---

## Alta inicial (una sola vez)

Desde tu máquina local, en el repo:

```bash
# 1) Crear estructura y subir los secretos (sensibles, van por scp, no por git)
ssh prod 'mkdir -p /aplicaciones/facturador/secrets/certs /aplicaciones/facturador/data'
scp .env            prod:/aplicaciones/facturador/secrets/.env
scp certs/prod.crt  prod:/aplicaciones/facturador/secrets/certs/prod.crt
scp certs/prod.key  prod:/aplicaciones/facturador/secrets/certs/prod.key

# 2) Primer deploy (sube código, build, up)
./deploy/deploy.sh
```

> En `secrets/.env`, las rutas deben ser **relativas** (se resuelven dentro del
> contenedor): `CERT_PATH=certs/prod.crt` y `KEY_PATH=certs/prod.key`.

### 3) Ruta en Caddy

Agregar al final de `/aplicaciones/pedidos/docker/Caddyfile`:

```
facturador.librosmario.com.ar {
    reverse_proxy facturador:5001
}
```

Y recargar Caddy (sin downtime):

```bash
ssh prod 'docker exec docker-caddy-1 caddy reload --config /etc/caddy/Caddyfile'
```

### 4) DNS

Crear un registro **A** `facturador` (en la zona `librosmario.com.ar`) → IP del
server. Caddy saca el certificado HTTPS solo, automáticamente, en cuanto el DNS
resuelve.

---

## Día a día

```bash
# Redeploy tras cambios de código (desde local)
./deploy/deploy.sh

# Ver estado / logs (en el server)
ssh prod 'docker ps --filter name=facturador'
ssh prod 'cd /aplicaciones/facturador && docker compose logs -f facturador'
# (o por la web: el dozzle de logs ya instalado)

# Levantar / reiniciar a mano
ssh prod 'cd /aplicaciones/facturador && docker compose up -d'
ssh prod 'cd /aplicaciones/facturador && docker compose restart facturador'
```

## Actualizar un secreto (ej. cambiar la clave o renovar el certificado)

```bash
scp .env prod:/aplicaciones/facturador/secrets/.env
ssh prod 'cd /aplicaciones/facturador && docker compose up -d'   # recarga env
```
