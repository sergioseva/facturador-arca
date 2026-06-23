# Facturador ARCA 🧾

Paginita web con una clave que te pide un monto y genera una **Factura C**
(Monotributo, a Consumidor Final) en ARCA (ex-AFIP), hablando directo con los
web services **WSAA** + **WSFEv1**. Sin servicios de terceros.

```
ingresás monto → WSAA (autentica con tu certificado) → WSFEv1 (pide el CAE) → factura
```

---

## 1. Instalar

```bash
cd arca-facturador
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # después editá .env
```

---

## 2. Generar el certificado digital (lo hacés vos, una vez)

ARCA no deja facturar por API sin un certificado a tu nombre. Hay que generar
una clave privada + un pedido (CSR), subirlo a ARCA y **autorizar el servicio
`wsfe`** (Factura Electrónica) para ese certificado.

### 2.1 Generar clave privada y CSR

Reemplazá `TU NOMBRE` y el CUIT por los tuyos (CUIT sin guiones):

```bash
# Clave privada
openssl genrsa -out certs/homo.key 2048

# Pedido de certificado (CSR)
openssl req -new -key certs/homo.key \
  -subj "/C=AR/O=TU NOMBRE/CN=facturador/serialNumber=CUIT 20123456789" \
  -out certs/homo.csr
```

> ⚠️ La clave privada (`.key`) es secreta: nunca la subas a git ni la compartas.
> El `.gitignore` ya la excluye.

### 2.2 — Entorno de HOMOLOGACIÓN (pruebas, empezamos acá)

1. Entrá a **WSASS** (autogestión de homologación):
   https://wsass-homo.afip.gob.ar/wsass/portal/main.aspx
   (login con CUIT + clave fiscal).
2. **"Adherir/Generar certificado"** → pegá el contenido de `certs/homo.csr`.
3. Te devuelve el certificado → guardalo como `certs/homo.crt`.
4. **"Autorizar Web Service"** → elegí el servicio **`wsfe`** y asociá tu
   certificado. (Esto es lo que permite facturar.)

En `.env` dejá:
```
ENTORNO=homologacion
CERT_PATH=certs/homo.crt
KEY_PATH=certs/homo.key
```

### 2.3 — Entorno de PRODUCCIÓN (cuando ya probaste y funciona)

1. AFIP con clave fiscal → **"Administrador de Relaciones de Clave Fiscal"** →
   adherí el servicio **"Administración de Certificados Digitales"**.
2. En ese servicio → **"Agregar alias"** → subí un CSR **nuevo de producción**
   (repetí el paso 2.1 generando `certs/prod.key` y `certs/prod.csr`).
   Descargá el certificado como `certs/prod.crt`.
3. Volvé a **"Administrador de Relaciones"** → **Nueva relación** → buscá el
   servicio **"Facturación Electrónica (wsfe)"** → como representante elegí el
   **certificado** que acabás de crear (su alias/DN). Esto autoriza el cert.
4. En "Comprobantes en línea" de AFIP, **dá de alta tu punto de venta** para
   **Web Services** (no sirve uno que ya uses para el facturador web manual).
   Poné ese número en `PUNTO_VENTA`.
5. En `.env`:
   ```
   ENTORNO=produccion
   CERT_PATH=certs/prod.crt
   KEY_PATH=certs/prod.key
   ```

---

## 3. Configurar `.env`

```
APP_PASSWORD=...            # la clave para entrar a la página
FLASK_SECRET_KEY=...        # string largo al azar
CUIT=20123456789           # tu CUIT sin guiones
PUNTO_VENTA=1              # el punto de venta habilitado para WS
ENTORNO=homologacion       # o produccion
CONCEPTO=2                 # 1=Productos 2=Servicios 3=Ambos
CERT_PATH=certs/homo.crt
KEY_PATH=certs/homo.key
```

Generá un secret al azar:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## 4. Correr

```bash
source .venv/bin/activate
python app.py
# abrí http://127.0.0.1:5001
```

Ponés la clave, tipeás el monto, "Generar Factura C" → te muestra **número + CAE**.
Cada factura emitida queda registrada en `data/facturas.json`.

---

## 5. Ponerla en producción (en `bot-prod`, detrás de Caddy)

Igual que tus otros proyectos: gunicorn + un bloque en el Caddyfile.

```bash
pip install gunicorn
gunicorn -w 1 -b 127.0.0.1:5001 app:app
```

Caddy (reverse proxy con HTTPS automático):
```
facturador.tudominio.com {
    reverse_proxy 127.0.0.1:5001
}
```

> Al ser facturación real, mantené el acceso restringido (clave fuerte, y si
> querés, IP allowlist en Caddy). El reloj del server tiene que estar en hora
> (WSAA rechaza tickets con el reloj desfasado): `timedatectl` con NTP activo.

---

## Notas / límites conocidos

- **Factura C a Consumidor Final**: ARCA exige identificar al receptor (CUIT/DNI)
  cuando el total supera cierto umbral. Por debajo va como Consumidor Final
  (doc 99). Si facturás montos altos a una persona, habría que agregar el campo
  del receptor — avisame y lo sumo.
- El **token de WSAA dura ~12hs** y se cachea en `data/`. No hace falta renovar
  a mano.
- Probá **siempre primero en homologación**. Las facturas de producción son
  reales y válidas fiscalmente.
- Concepto **Servicios** (2/3) manda fecha de período y vencimiento = la fecha
  elegida. Si facturás por períodos distintos, se puede parametrizar.
- **Fecha atrasada**: la página deja elegir la fecha (hasta 10 días hacia atrás,
  que es lo que tolera ARCA). Ojo: los comprobantes deben ir en **orden
  cronológico**, así que la fecha no puede ser anterior a la de la última
  factura ya autorizada en ese punto de venta — si lo es, ARCA la rechaza y vas
  a ver el error en pantalla.
