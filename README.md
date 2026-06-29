# Factuya 🧾

SaaS **multiusuario** para que monotributistas **emitan Facturas C en ARCA**
(ex-AFIP) y controlen su categoría. Cada cliente tiene su cuenta y su módulo de
facturación; la plataforma factura en su nombre vía **delegación** (un solo
certificado, sin guardar claves de nadie). Habla directo con los web services de
ARCA (**WSAA** + **WSFEv1** + Padrón), sin terceros.

```
cliente entra → carga monto → WSFEv1 (Auth.Cuit = CUIT del cliente) → Factura C con CAE
```

## 📚 Documentación

- **[docs/ALTA-CLIENTE.md](docs/ALTA-CLIENTE.md)** — proceso completo de alta de un
  cliente (pasos del admin y del cliente, autorización/delegación en AFIP,
  troubleshooting). **Empezá por acá para sumar clientes.**
- **[deploy/DEPLOY.md](deploy/DEPLOY.md)** — deploy en el server (Docker + Caddy) y
  setup inicial de la plataforma (certificado, servicios WSFE y padrón).

---

## Qué hace

- **Facturar** con un click: ingresás el monto (con formato argentino
  `10.000.000,50` que se arma solo mientras tipeás) y opcionalmente la fecha
  (hasta 10 días hacia atrás), y emite la **Factura C** con su número y CAE.
- **Spinner** mientras espera la respuesta de ARCA, para que no parezca colgado.
- **Historial de intentos** (SQLite): guarda **todas** las facturas, las
  emitidas y las que fallaron, con el mensaje de error de ARCA.
- **Control monotributo**: importás el **ZIP/CSV de "Mis Comprobantes"** (que
  incluye **todos** los puntos de venta y tipos, no solo lo emitido por esta app)
  y te muestra el **total facturado por mes** y el **acumulado móvil de los
  últimos 12 meses** (el número que mira ARCA para la categoría). Las notas de
  crédito restan; reimportar rangos superpuestos no duplica.

## Páginas

| Ruta | Qué muestra |
|------|-------------|
| `/` | Formulario para emitir la factura (monto + fecha). |
| `/historial` | Tabla de todos los intentos (emitidos y con error) + resumen. |
| `/resumen` | Control monotributo: importar ZIP/CSV de Mis Comprobantes, total por mes y acumulado 12 meses. |
| `/login` `/logout` | Acceso con la clave. |

## Estructura del proyecto

```
arca-facturador/
├── app.py                # web app Flask (rutas, login, filtros)
├── afip.py               # cliente WSAA + WSFEv1 (auth, emisión, consulta)
├── db.py                 # persistencia en SQLite (data/facturas.db)
├── generar_csr.sh        # helper para generar clave privada + CSR
├── templates/            # login, index, historial, resumen
├── certs/                # certificados y claves (gitignored)
├── data/                 # base SQLite y cache del token (gitignored)
├── requirements.txt
└── .env                  # configuración y secretos (gitignored)
```

> 🔒 **Nada sensible se versiona.** El `.gitignore` excluye `.env`, los
> certificados/claves de `certs/` y la base de `data/`. Lo único de ejemplo que
> se sube es `.env.example`.

---

## 1. Instalar

```bash
cd arca-facturador
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # después editá .env (ver paso 3)
```

> Requiere Python 3.10+ (probado en 3.14). `openssl` para generar el certificado.

---

## 2. Generar el certificado digital (lo hacés vos, una vez)

ARCA no deja facturar por API sin un certificado a tu nombre. El flujo es:
generar una clave privada + un pedido (CSR), subir el CSR a ARCA para que te
devuelva el certificado, y **autorizar el servicio `wsfe`** (Factura
Electrónica) para ese certificado.

### 2.1 Generar clave privada y CSR

Hay un helper que te lo arma (te pregunta CUIT y nombre):

```bash
./generar_csr.sh homo      # para homologación (pruebas)
./generar_csr.sh prod      # para producción
```

Deja `certs/<entorno>.key` (privada, **secreta**) y `certs/<entorno>.csr` (el
pedido que vas a subir a ARCA), e imprime el CSR para copiar.

> ⚠️ La clave privada (`.key`) es secreta: nunca la subas a git ni la compartas.
> El `.gitignore` ya la excluye.

### 2.2 — Entorno de HOMOLOGACIÓN (pruebas, empezá acá)

1. Entrá a **WSASS** (autogestión de homologación):
   https://wsass-homo.afip.gob.ar/wsass/portal/main.aspx
   (login con CUIT + clave fiscal).
2. **Crear certificado / Nuevo certificado** → pegá el contenido de
   `certs/homo.csr`.
3. Te devuelve el certificado → guardalo como `certs/homo.crt`.
4. **Crear autorización a servicio** → Servicio **`wsfe`**, representado = tu
   CUIT, y asociá el certificado recién creado. *(Esto es lo que habilita
   facturar.)*

En `.env` dejá:
```
ENTORNO=homologacion
CERT_PATH=certs/homo.crt
KEY_PATH=certs/homo.key
```

### 2.3 — Entorno de PRODUCCIÓN (cuando ya probaste y funciona)

1. **Generá el CSR de producción**: `./generar_csr.sh prod`.
2. AFIP con clave fiscal → **"Administrador de Relaciones de Clave Fiscal"** →
   adherí el servicio **"Administración de Certificados Digitales"**.
3. En ese servicio → **"Agregar alias"** → subí `certs/prod.csr`. Descargá el
   certificado como `certs/prod.crt`.
4. Volvé a **"Administrador de Relaciones"** → **Nueva relación** → buscá el
   servicio **"Facturación Electrónica (wsfe)"** → como representante elegí el
   **certificado** que creaste (su alias/DN). Esto autoriza el cert.
5. **Punto de venta para Web Services**: en AFIP → **"Administración de puntos
   de venta y domicilios"** (o dentro de "Comprobantes en línea") → **Agregar
   punto de venta** → sistema **"Factura Electrónica – Web Services"** (¡NO
   "Comprobantes en línea"!). Anotá el número y ponelo en `PUNTO_VENTA`.
6. En `.env`:
   ```
   ENTORNO=produccion
   CERT_PATH=certs/prod.crt
   KEY_PATH=certs/prod.key
   ```

---

## 3. Configurar `.env`

```
APP_PASSWORD=...            # la clave para entrar a la página
FLASK_SECRET_KEY=...        # string largo al azar (ver abajo)
CUIT=20123456789           # tu CUIT sin guiones
PUNTO_VENTA=1              # el punto de venta habilitado para WS
ENTORNO=homologacion       # o produccion
CONCEPTO=2                 # 1=Productos 2=Servicios 3=Ambos
CERT_PATH=certs/homo.crt
KEY_PATH=certs/homo.key
```

Generá el `FLASK_SECRET_KEY` al azar:
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

Entrás con la clave, tipeás el monto, **"Generar Factura C"** → te muestra
**número + CAE**. Cada intento (emitido o con error) queda en
`data/facturas.db` (SQLite).

### Consultar la base a mano

```bash
sqlite3 data/facturas.db "SELECT numero, importe, cae, estado FROM facturas ORDER BY id DESC;"
```

---

## 5. Ponerla en producción (detrás de Caddy)

Mismo patrón que el resto de los proyectos: gunicorn + un bloque en el Caddyfile.

```bash
pip install gunicorn
gunicorn -w 1 -b 127.0.0.1:5001 app:app
```

```
facturador.tudominio.com {
    reverse_proxy 127.0.0.1:5001
}
```

> - Usá **1 worker** (`-w 1`): el cache del token WSAA es un archivo local.
> - Al ser facturación real, mantené el acceso restringido (clave fuerte y, si
>   querés, IP allowlist en Caddy).
> - El **reloj del server tiene que estar en hora** (WSAA rechaza tickets con el
>   reloj desfasado): asegurate de tener NTP activo (`timedatectl`).

---

## Cómo funciona por dentro

- **WSAA** (`afip.py`): arma un *login ticket*, lo firma como **CMS/PKCS#7** con
  tu certificado (librería `cryptography`, sin shelling-out a openssl), y obtiene
  un **Token + Sign** que valen ~12 hs. Se cachean en `data/ta_*.json` y se
  renuevan solos.
- **WSFEv1** (`afip.py`, vía `zeep`): pide el próximo número con
  `FECompUltimoAutorizado` y emite con `FECAESolicitar` (Factura C, doc receptor
  99 = Consumidor Final, condición IVA receptor 5 según RG 5616). Los servidores
  de **producción** usan Diffie-Hellman de 1024 bits, así que se baja el nivel
  SSL a `SECLEVEL=1` solo para hablar con ARCA (`_AfipSSLAdapter`).
- **SQLite** (`db.py`): tabla `facturas` (intentos: emitidos y con error) y
  `mis_comprobantes` (lo importado del CSV de Mis Comprobantes para los totales).

---

## Notas / límites conocidos

- **Factura C a Consumidor Final**: ARCA exige identificar al receptor (CUIT/DNI)
  cuando el total supera cierto umbral. Por debajo va como Consumidor Final
  (doc 99). Si facturás montos altos a una persona, hay que agregar el campo del
  receptor.
- **Control monotributo**: el web service (WSFEv1) **solo puede leer el punto de
  venta de Web Services**, no los del facturador online de AFIP (devuelve error
  `11002`). Por eso el control se hace **importando el ZIP/CSV de "Mis
  Comprobantes"**, que sí incluye **todos** los puntos de venta y tipos. El tope
  de categoría **no está hardcodeado** porque ARCA lo actualiza cada tanto:
  comparalo contra la tabla vigente. Mis Comprobantes se actualiza con cierto
  retraso, así que el total llega hasta la fecha del último export importado.
- **Fecha atrasada**: se puede elegir hasta 10 días hacia atrás (lo que tolera
  ARCA). Los comprobantes deben ir en **orden cronológico**: la fecha no puede
  ser anterior a la de la última factura ya autorizada en ese punto de venta — si
  lo es, ARCA la rechaza y el error aparece en pantalla y en el historial.
- **Concepto Servicios** (2/3) manda fecha de período y vencimiento = la fecha
  elegida. Para períodos distintos, se puede parametrizar.
- El **token de WSAA dura ~12 hs** y se cachea; no hace falta renovarlo a mano.
- Probá **siempre primero en homologación**. Las facturas de producción son
  reales y válidas fiscalmente.
