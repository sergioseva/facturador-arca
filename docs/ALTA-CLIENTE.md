# Alta de un cliente (onboarding) — proceso completo

Cómo dar de alta a un cliente nuevo en Factuya, paso a paso. Hay tareas **tuyas
(admin)** y tareas **del cliente**, y el **orden importa** (hay dependencias).

## Cómo funciona (modelo de delegación)

Factuya usa **un solo certificado** (el "computador fiscal" de la plataforma).
Cada cliente **te autoriza en su AFIP** a facturar en su nombre — sin entregarte
su certificado ni su clave fiscal. La app emite poniendo `Auth.Cuit = CUIT del
cliente` con tu certificado.

**Datos de la plataforma** (los necesita el cliente para autorizarte):
- **CUIT de la plataforma:** `20242454600`
- **Computador fiscal (alias):** `facturador`

---

## Resumen del orden

```
1. ADMIN  → crea la cuenta del cliente en la app (invitación)
2. CLIENTE → en SU AFIP: autoriza el computador fiscal de la plataforma (WSFE)
3. CLIENTE → en SU AFIP: crea un punto de venta "Web Services"
4. ADMIN  → en TU AFIP: acepta la delegación + confirma el vínculo del computador fiscal
5. CLIENTE → en la app: carga CUIT + punto de venta + datos y "Verificar conexión"
6. CLIENTE → empieza a facturar
```

> El paso 2 (cliente autoriza) tiene que pasar **antes** del paso 4 (vos aceptás),
> porque no podés aceptar algo que todavía no existe.

---

## 1. ADMIN — Crear la cuenta del cliente

En la app: **Admin → Usuarios → Invitar usuario**.
- Email del cliente + una **clave temporal** (la cambia él desde "Mi cuenta").
- Rol: `user`.

Le pasás el email y la clave temporal.

## 2. CLIENTE — Autorizar el computador fiscal de la plataforma

El cliente, **con su clave fiscal**, en AFIP/ARCA:

1. **Administrador de Relaciones de Clave Fiscal** → **Nueva Relación**.
2. **Servicio** → Buscar → **Facturación Electrónica** (WSFE).
3. **Representante** → buscá el **Computador Fiscal de la plataforma**:
   CUIT **20242454600**, alias **`facturador`**.
4. Confirmar.

Esto crea la delegación. Va a quedar en estado **"Aceptada: Pendiente"** (la
aceptás vos en el paso 4).

## 3. CLIENTE — Crear un punto de venta Web Service

En AFIP/ARCA del cliente:

1. **Administración de puntos de venta y domicilios** → **Alta**.
2. Sistema: **Factura Electrónica – Web Services**
   (⚠️ NO "Comprobantes en línea" — es otro sistema distinto).
3. **Anotá el número** que asigna (ej. 3). Ese número va en el onboarding.

## 4. ADMIN — Aceptar la delegación y confirmar el vínculo

Vos, **con tu clave fiscal (20242454600)**, en AFIP:

**a) Aceptar la delegación**
- **Administrador de Relaciones de Clave Fiscal** → buscá las delegaciones
  **pendientes de aceptación** (la del cliente, servicio *Facturación
  Electrónica*) y **aceptala**. Pasa de *Pendiente* a *Aprobada*.

**b) Confirmar el vínculo del computador fiscal con el cliente**
- **Nueva Relación** actuando para el cliente (ahora podés elegirlo como
  *Representado* porque te delegó):
  - **Representado:** el cliente (su CUIT).
  - **Servicio:** **Facturación Electrónica** (WSFE).
  - **Representante:** **Computador Fiscal `facturador`** (¡el certificado, no la persona!).
- Si AFIP dice **"la autorización ya existe"**, **es correcto** — significa que el
  vínculo ya está creado. No es un error.

## 5. CLIENTE — Cargar datos y verificar

En la app (cuenta del cliente) → **onboarding**:
- **CUIT** (sin guiones).
- **Punto de venta**: el número **Web Service** del paso 3 (ej. 3).
- Razón social, actividad (opcional), concepto, entorno **Producción**.
- **Guardar y verificar conexión** → debe dar **OK**.

> La verificación pide un **token nuevo** a ARCA, así toma la delegación recién
> habilitada al instante (no hay que esperar ni reiniciar nada).

## 6. CLIENTE — Facturar

Con la conexión verificada, ya puede emitir Facturas C desde **Facturar**.

---

## Problemas frecuentes (y qué significan)

| Error al verificar | Qué significa | Solución |
|---|---|---|
| **[600] No apareció CUIT en lista de relaciones** | La delegación todavía no está completa/activa para el web service. | Revisá: cliente autorizó (paso 2), vos aceptaste (4a) y el computador fiscal está vinculado (4b). Reintentá "Verificar conexión" (pide token fresco). |
| **[11002] El punto de venta no está habilitado para WS** | El número de punto de venta cargado no es del tipo **Web Service**. | Usá el PV del tipo "Factura Electrónica – Web Services" (paso 3). El wizard te muestra cuáles tenés disponibles. |
| **Computador no autorizado a acceder al servicio** (WSAA) | Falta autorizar el servicio (WSFE o el de padrón) al computador fiscal de la plataforma. | Es una tarea de la plataforma (admin), una sola vez. Ver `deploy/DEPLOY.md`. |

## Notas

- **La lista de CUIT que podés representar queda fijada al emitir el token** WSAA
  (válido ~12 h). La app **fuerza un token nuevo al verificar**, por eso una
  delegación recién aceptada se toma enseguida.
- **Consulta de padrón** (razón social al cargar un CUIT): requiere que el
  computador fiscal de la plataforma esté autorizado para `ws_sr_constancia_inscripcion`
  (una sola vez, tarea del admin).
- Probar siempre primero que **"Verificar conexión"** dé OK antes de emitir una
  factura real.
