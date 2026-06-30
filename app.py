"""
Facturador ARCA — SaaS multiusuario.

Cada usuario tiene su cuenta y su configuración fiscal (tenant_config). Emite
Factura C en nombre propio vía el computador fiscal de la plataforma (modelo de
delegación): un solo certificado, y `Auth.Cuit` = CUIT del tenant.
"""

import glob
import os
import secrets
from datetime import datetime, timedelta

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

import afip
import auth
import categorias
import db
import json

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ["FLASK_SECRET_KEY"]
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)  # detrás de Caddy

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "1") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)

csrf = CSRFProtect(app)
limiter = Limiter(get_remote_address, app=app, default_limits=[], storage_uri="memory://")

# Credencial de la PLATAFORMA (computador fiscal). Un solo par para todos.
PLATFORM_CERT = os.environ.get("PLATFORM_CERT_PATH") or os.environ.get("CERT_PATH", "certs/prod.crt")
PLATFORM_KEY = os.environ.get("PLATFORM_KEY_PATH") or os.environ.get("KEY_PATH", "certs/prod.key")
PLATFORM_CUIT = os.environ.get("PLATFORM_CUIT", "")      # para mostrar en onboarding
PLATFORM_ALIAS = os.environ.get("PLATFORM_ALIAS", "")    # alias del computador fiscal

# Capturas guía del onboarding: se suben desde el panel y se guardan en el
# volumen de datos (persisten entre deploys). Cada "slot" es una pantalla de AFIP.
ONBOARDING_DIR = os.path.join(os.path.dirname(__file__), "data", "onboarding")
os.makedirs(ONBOARDING_DIR, exist_ok=True)

GUIA_SLOTS = [
    {"key": "arca", "paso": 1, "titulo": "Paso 1 · Elegir ARCA",
     "desc": "La lista de organismos, con ARCA seleccionado."},
    {"key": "servicio", "paso": 1, "titulo": "Paso 1 · Elegir el servicio",
     "desc": "La lista de servicios, con “Facturación Electrónica”."},
    {"key": "representante", "paso": 1, "titulo": "Paso 1 · Representante",
     "desc": "La pantalla donde se ingresa el CUIT de la plataforma como representante."},
    {"key": "punto_venta", "paso": 2, "titulo": "Paso 2 · Punto de venta",
     "desc": "El alta del punto de venta tipo “Factura Electrónica – Web Services”."},
]
_SLOT_KEYS = {s["key"] for s in GUIA_SLOTS}
_IMG_EXTS = ("png", "jpg", "jpeg", "webp", "gif")


def _slot_file(key):
    matches = glob.glob(os.path.join(ONBOARDING_DIR, key + ".*"))
    return matches[0] if matches else None


def _guia_imgs():
    return {s["key"]: bool(_slot_file(s["key"])) for s in GUIA_SLOTS}


@app.before_request
def _cargar_usuario():
    auth.load_user()  # deja g.user (dict o None) disponible en todas las vistas


# --- filtros de template ----------------------------------------------------

_MESES = ["", "ene", "feb", "mar", "abr", "may", "jun",
          "jul", "ago", "sep", "oct", "nov", "dic"]


@app.template_filter("mes")
def formato_mes(yyyymm):
    return f"{_MESES[int(yyyymm[4:6])]} {yyyymm[0:4]}"


@app.template_filter("ars")
def formato_ars(value):
    s = f"{float(value):,.2f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def _topes_vigentes():
    raw = db.get_config("topes_json")
    if raw:
        try:
            return (
                json.loads(raw),
                db.get_config("topes_vigente_desde", categorias.VIGENTE_DESDE),
                db.get_config("topes_actualizado"),
            )
        except (ValueError, TypeError):
            pass
    return dict(categorias.TOPES), categorias.VIGENTE_DESDE, None


def _parse_importe(raw):
    """Formato argentino '10.000.000,50' -> '10000000.50' (string para float)."""
    return raw.strip().replace(".", "").replace(",", ".")


# --- auth -------------------------------------------------------------------


@app.route("/")
def landing():
    if g.user:
        return redirect(url_for("facturar"))
    return render_template("landing.html")


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("8 per minute", methods=["POST"])
def login():
    if g.user:
        return redirect(url_for("facturar"))
    error = None
    if request.method == "POST":
        email = request.form.get("email", "")
        password = request.form.get("password", "")
        user = db.get_user_by_email(email)
        if user and user["activo"] and auth.verify_password(user["password_hash"], password):
            auth.login_user(user)
            return redirect(url_for("facturar"))
        error = "Email o clave incorrectos."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    auth.logout_user()
    return redirect(url_for("login"))


# --- facturar ---------------------------------------------------------------


@app.route("/facturar", methods=["GET", "POST"])
@auth.login_required
def facturar():
    cfg = db.get_tenant_config(g.user["id"])
    if not cfg or not cfg.get("delegacion_ok") or not cfg.get("cuit"):
        return redirect(url_for("onboarding"))

    resultado = None
    error = None
    if request.method == "POST":
        importe = ""
        try:
            importe = _parse_importe(request.form.get("importe", ""))
            fecha = request.form.get("fecha", "").strip() or None
            doc_tipo = request.form.get("doc_tipo", "99").strip() or "99"
            doc_nro = request.form.get("doc_nro", "").strip()
            cond_iva = request.form.get("cond_iva", "").strip() or None
            nombre = request.form.get("receptor_nombre", "").strip()
            resultado = afip.emitir_factura_c(
                cuit=cfg["cuit"],
                entorno=cfg["entorno"],
                cert_path=PLATFORM_CERT,
                key_path=PLATFORM_KEY,
                punto_venta=cfg["punto_venta"],
                importe=importe,
                concepto=cfg["concepto"],
                fecha=fecha,
                actividad=cfg.get("actividad") or None,
                doc_tipo=doc_tipo,
                doc_nro=doc_nro,
                cond_iva_receptor=cond_iva,
            )
            db.guardar(g.user["id"], resultado)
            # recordar el receptor si quedó identificado (no Consumidor Final)
            if resultado.get("doc_tipo") and int(resultado["doc_tipo"]) != 99:
                db.guardar_receptor(
                    g.user["id"], resultado["doc_tipo"], resultado["doc_nro"],
                    nombre, int(cond_iva) if cond_iva else None,
                )
        except (afip.AfipError, ValueError) as e:
            error = str(e)
        except Exception as e:  # noqa: BLE001
            error = f"Error inesperado: {e}"

        if error:
            try:
                monto = float(importe) if importe else None
            except ValueError:
                monto = None
            try:
                db.registrar_error(
                    g.user["id"], datetime.now(afip.AR_TZ).isoformat(),
                    cfg["entorno"], monto, error,
                )
            except Exception:  # noqa: BLE001
                pass

    hoy = datetime.now(afip.AR_TZ).strftime("%Y-%m-%d")
    return render_template(
        "facturar.html",
        resultado=resultado, error=error,
        entorno=cfg["entorno"], punto_venta=cfg["punto_venta"], cuit=cfg["cuit"],
        hoy=hoy, receptores=db.listar_receptores(g.user["id"]),
        doc_nombres=afip.DOC_TIPO_NOMBRES,
    )


@app.route("/api/padron")
@auth.login_required
def api_padron():
    """Devuelve la razón social/nombre de un CUIT/CUIL (consulta al padrón de ARCA)."""
    doc = "".join(c for c in request.args.get("doc", "") if c.isdigit())
    if len(doc) != 11:
        return {"error": "Ingresá un CUIT/CUIL de 11 dígitos."}
    if not PLATFORM_CUIT:
        return {"error": "La consulta de padrón no está configurada."}
    cfg = db.get_tenant_config(g.user["id"]) or {}
    try:
        info = afip.consultar_padron(
            doc, cfg.get("entorno", "homologacion"), PLATFORM_CERT, PLATFORM_KEY, PLATFORM_CUIT
        )
        return {"nombre": info["nombre"], "estado": info.get("estado")}
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        # No exponer errores internos de config/WSAA al usuario final.
        if any(k in msg.lower() for k in ("autoriz", "wsaa", "login", "ssl", "timed out", "connection")):
            return {"error": "No se pudo verificar el nombre en ARCA ahora."}
        return {"error": msg}


@app.route("/historial")
@auth.login_required
def historial():
    return render_template(
        "historial.html",
        facturas=db.listar(g.user["id"]),
        totales=db.totales(g.user["id"]),
    )


# --- control monotributo ----------------------------------------------------


@app.route("/resumen", methods=["GET", "POST"])
@auth.login_required
def resumen():
    uid = g.user["id"]
    cfg = db.get_tenant_config(uid) or {}
    entorno = cfg.get("entorno", "homologacion")
    msg = error = None

    if request.method == "POST":
        if "refrescar_topes" in request.form:
            try:
                topes, vigente = categorias.fetch_topes_arca()
                db.set_config("topes_json", json.dumps(topes))
                db.set_config("topes_vigente_desde", vigente)
                db.set_config("topes_actualizado", datetime.now(afip.AR_TZ).strftime("%d/%m/%Y %H:%M"))
                msg = f"Topes actualizados desde ARCA (vigentes desde {vigente})."
            except Exception as e:  # noqa: BLE001
                error = f"No pude actualizar los topes desde ARCA: {e}"
        elif "categoria" in request.form:
            cat = request.form["categoria"].strip().upper()
            if cat in categorias.TOPES or cat == "":
                db.upsert_tenant_config(uid, categoria=cat)
                msg = f"Categoría {cat} guardada." if cat else "Categoría desactivada."
            else:
                error = "Categoría inválida."
        else:
            archivo = request.files.get("csv")
            if not archivo or not archivo.filename:
                error = "Elegí el archivo CSV/ZIP exportado de Mis Comprobantes."
            else:
                try:
                    r = db.importar_mis_comprobantes(uid, archivo.read(), entorno)
                    if r.get("error"):
                        error = r["error"]
                    else:
                        rango = ""
                        if r["desde"]:
                            rango = f" (del {r['desde'][6:8]}/{r['desde'][4:6]}/{r['desde'][0:4]} al {r['hasta'][6:8]}/{r['hasta'][4:6]}/{r['hasta'][0:4]})"
                        msg = f"Importados {r['importados']} comprobantes{rango}."
                except Exception as e:  # noqa: BLE001
                    error = f"No pude procesar el archivo: {e}"

    hace_12 = (datetime.now(afip.AR_TZ) - timedelta(days=365)).strftime("%Y%m%d")
    detalle = db.resumen_detallado(uid, entorno, hace_12)
    topes, vigente_desde, topes_actualizado = _topes_vigentes()
    cat = (db.get_tenant_config(uid) or {}).get("categoria", "")
    total = detalle["movil"]["total"]
    cat_info = None
    if cat in topes:
        tope = topes[cat]
        cat_info = {
            "categoria": cat, "tope": tope, "restante": tope - total,
            "excedido": total > tope,
            "pct": min(total / tope * 100, 100) if tope else 0,
            "pct_real": (total / tope * 100) if tope else 0,
            "sugerida": categorias.categoria_para(total, topes),
        }

    return render_template(
        "resumen.html",
        meses=detalle["meses"], movil12=detalle["movil"],
        info=db.info_mis(uid, entorno), entorno=entorno,
        msg=msg, error=error, categoria=cat, cat_info=cat_info,
        topes=topes, vigente_desde=vigente_desde, topes_actualizado=topes_actualizado,
    )


# --- onboarding -------------------------------------------------------------


@app.route("/onboarding", methods=["GET", "POST"])
@auth.login_required
def onboarding():
    uid = g.user["id"]
    cfg = db.get_tenant_config(uid) or {}
    error = None

    if request.method == "POST" and request.form.get("accion") == "marcar":
        # El cliente avisa que completó sus pasos en AFIP.
        db.upsert_tenant_config(
            uid, cliente_marco_arca=1,
            arca_marcado_en=datetime.now(afip.AR_TZ).strftime("%d/%m/%Y %H:%M"),
        )
        cfg = db.get_tenant_config(uid) or {}

    elif request.method == "POST":
        # Carga de datos + verificación — solo si el admin habilitó (o ya conectado).
        if not (cfg.get("admin_habilito") or cfg.get("delegacion_ok")):
            error = "Todavía no está habilitada la carga de datos. Esperá la confirmación del administrador."
        else:
            cuit = "".join(c for c in request.form.get("cuit", "") if c.isdigit())
            pv = request.form.get("punto_venta", "").strip()
            actividad = "".join(c for c in request.form.get("actividad", "") if c.isdigit())
            razon = request.form.get("razon_social", "").strip()
            concepto = request.form.get("concepto", "2").strip()
            entorno = request.form.get("entorno", "homologacion").strip()

            if len(cuit) != 11:
                error = "El CUIT debe tener 11 dígitos (sin guiones)."
            elif not pv.isdigit():
                error = "El punto de venta debe ser un número."
            else:
                db.upsert_tenant_config(
                    uid, cuit=cuit, punto_venta=int(pv), actividad=actividad or None,
                    razon_social=razon, concepto=int(concepto), entorno=entorno,
                )
                try:
                    afip.verificar_delegacion(cuit, entorno, PLATFORM_CERT, PLATFORM_KEY, int(pv))
                    db.set_delegacion_ok(uid, 1)
                    return redirect(url_for("facturar"))
                except Exception as e:  # noqa: BLE001
                    db.set_delegacion_ok(uid, 0)
                    msg = str(e)
                    if "11002" in msg:
                        try:
                            pvs = afip.listar_puntos_venta(cuit, entorno, PLATFORM_CERT, PLATFORM_KEY)
                        except Exception:  # noqa: BLE001
                            pvs = []
                        if pvs:
                            disp = "Tus puntos de venta Web Service disponibles son: " + ", ".join(str(p) for p in pvs) + "."
                        else:
                            disp = "No encontramos puntos de venta Web Service: creá uno del tipo “Factura Electrónica – Web Services”."
                        error = f"El punto de venta {pv} no está habilitado para Web Service. {disp}"
                    elif "lista de relaciones" in msg or "[600]" in msg:
                        error = (
                            "Todavía no está habilitada la delegación. Si ya autorizaste el "
                            "servicio WSFE a la plataforma, ARCA puede tardar un rato en "
                            "habilitarla (de minutos a un par de horas, a veces hasta el día "
                            "siguiente). Esperá un poco y volvé a tocar “Verificar conexión” — "
                            "no hace falta volver a autorizar."
                        )
                    else:
                        error = (
                            f"ARCA no validó la conexión: {e}\n\n"
                            "Revisá que (1) hayas autorizado el computador fiscal de la "
                            "plataforma para el servicio WSFE en Administrador de Relaciones, "
                            "y (2) que el punto de venta sea del tipo Web Service."
                        )
                cfg = db.get_tenant_config(uid) or {}

    return render_template(
        "onboarding.html", cfg=cfg, error=error,
        platform_cuit=PLATFORM_CUIT, platform_alias=PLATFORM_ALIAS,
        imgs=_guia_imgs(),
    )


# --- cuenta -----------------------------------------------------------------


@app.route("/cuenta", methods=["GET", "POST"])
@auth.login_required
def cuenta():
    uid = g.user["id"]
    msg = error = None
    if request.method == "POST":
        actual = request.form.get("password_actual", "")
        nueva = request.form.get("password_nueva", "")
        if not auth.verify_password(g.user["password_hash"], actual):
            error = "La clave actual es incorrecta."
        elif len(nueva) < 8:
            error = "La clave nueva debe tener al menos 8 caracteres."
        else:
            db.set_user_password(uid, auth.hash_password(nueva))
            msg = "Clave actualizada."
    return render_template(
        "cuenta.html", cfg=db.get_tenant_config(uid) or {}, msg=msg, error=error,
    )


# --- admin (invite-only) ----------------------------------------------------


@app.route("/admin/guia")
@auth.admin_required
def admin_guia():
    return render_template(
        "admin_guia.html", platform_cuit=PLATFORM_CUIT, platform_alias=PLATFORM_ALIAS
    )


@app.route("/guia-img/<slot>")
@auth.login_required
def guia_img(slot):
    if slot not in _SLOT_KEYS:
        abort(404)
    path = _slot_file(slot)
    if not path:
        abort(404)
    return send_file(path)


@app.route("/admin/imagenes", methods=["GET", "POST"])
@auth.admin_required
def admin_imagenes():
    msg = error = None
    if request.method == "POST":
        slot = request.form.get("slot", "")
        if slot not in _SLOT_KEYS:
            error = "Slot inválido."
        elif request.form.get("accion") == "borrar":
            f = _slot_file(slot)
            if f:
                os.remove(f)
            msg = "Imagen eliminada."
        else:
            archivo = request.files.get("imagen")
            if not archivo or not archivo.filename:
                error = "Elegí una imagen."
            else:
                ext = archivo.filename.rsplit(".", 1)[-1].lower() if "." in archivo.filename else ""
                if ext not in _IMG_EXTS:
                    error = "Formato no soportado (png, jpg, webp, gif)."
                else:
                    prev = _slot_file(slot)
                    if prev:
                        os.remove(prev)
                    archivo.save(os.path.join(ONBOARDING_DIR, slot + "." + ext))
                    msg = "Imagen subida."
    slots = [{**s, "tiene": bool(_slot_file(s["key"]))} for s in GUIA_SLOTS]
    return render_template("admin_imagenes.html", slots=slots, msg=msg, error=error)


@app.route("/admin/users", methods=["GET", "POST"])
@auth.admin_required
def admin_users():
    msg = error = None
    if request.method == "POST":
        accion = request.form.get("accion")
        if accion == "crear":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            role = "admin" if request.form.get("role") == "admin" else "user"
            if "@" not in email or len(password) < 8:
                error = "Email inválido o clave de menos de 8 caracteres."
            elif db.get_user_by_email(email):
                error = "Ya existe un usuario con ese email."
            else:
                db.create_user(email, auth.hash_password(password), role=role)
                msg = f"Usuario {email} creado."
        elif accion == "toggle":
            target = int(request.form.get("user_id"))
            if target == g.user["id"]:
                error = "No podés desactivar tu propia cuenta."
            else:
                u = db.get_user_by_id(target)
                if u:
                    db.set_user_active(target, 0 if u["activo"] else 1)
                    msg = "Usuario actualizado."
        elif accion == "blanquear":
            target = int(request.form.get("user_id"))
            u = db.get_user_by_id(target)
            if u:
                pw = secrets.token_urlsafe(9)
                db.set_user_password(target, auth.hash_password(pw))
                msg = f"Clave de {u['email']} reseteada. Clave temporal nueva: {pw}"
        elif accion == "habilitar":
            target = int(request.form.get("user_id"))
            u = db.get_user_by_id(target)
            db.upsert_tenant_config(target, admin_habilito=1)
            msg = f"Habilitaste la carga de datos para {u['email'] if u else target}."
        elif accion == "borrar":
            target = int(request.form.get("user_id"))
            if target == g.user["id"]:
                error = "No podés borrar tu propia cuenta."
            else:
                u = db.get_user_by_id(target)
                db.delete_user(target)
                msg = f"Usuario {u['email'] if u else target} borrado (cuenta y datos)."
    usuarios = db.list_users_estado()
    esperando = sum(
        1 for u in usuarios
        if u.get("cliente_marco_arca") and not u.get("admin_habilito")
        and not u.get("delegacion_ok") and u["role"] != "admin"
    )
    return render_template(
        "admin_users.html", usuarios=usuarios, esperando=esperando, msg=msg, error=error
    )


if __name__ == "__main__":
    db.init_db()
    app.run(host="127.0.0.1", port=5001, debug=False)
