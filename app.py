"""
Paginita web para emitir facturas en ARCA.

Una sola clave para entrar, un campo "monto", y al darle "Facturar" se conecta
a los web services de ARCA (WSAA + WSFEv1) y genera la Factura C con su CAE.
"""

import os
from datetime import datetime, timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import (
    Flask,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import afip
import categorias
import db

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ["FLASK_SECRET_KEY"]

# Config tomada del .env
APP_PASSWORD = os.environ["APP_PASSWORD"]
CUIT = os.environ["CUIT"]
PUNTO_VENTA = int(os.environ["PUNTO_VENTA"])
ENTORNO = os.environ.get("ENTORNO", "homologacion")
CONCEPTO = int(os.environ.get("CONCEPTO", "2"))
CERT_PATH = os.environ["CERT_PATH"]
KEY_PATH = os.environ["KEY_PATH"]


_MESES = ["", "ene", "feb", "mar", "abr", "may", "jun",
          "jul", "ago", "sep", "oct", "nov", "dic"]


@app.template_filter("mes")
def formato_mes(yyyymm):
    """'202606' -> 'jun 2026'."""
    return f"{_MESES[int(yyyymm[4:6])]} {yyyymm[0:4]}"


@app.template_filter("ars")
def formato_ars(value):
    """Formatea un número al estilo argentino: 10000000.5 -> '10.000.000,50'."""
    s = f"{float(value):,.2f}"  # estilo inglés: 10,000,000.50
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("auth"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == APP_PASSWORD:
            session["auth"] = True
            return redirect(url_for("index"))
        error = "Clave incorrecta."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    resultado = None
    error = None
    if request.method == "POST":
        try:
            # Llega en formato argentino: "10.000.000,50".
            # Sacamos los puntos de miles y pasamos la coma decimal a punto.
            importe = (
                request.form.get("importe", "")
                .strip()
                .replace(".", "")
                .replace(",", ".")
            )
            fecha = request.form.get("fecha", "").strip() or None
            resultado = afip.emitir_factura_c(
                cuit=CUIT,
                entorno=ENTORNO,
                cert_path=CERT_PATH,
                key_path=KEY_PATH,
                punto_venta=PUNTO_VENTA,
                importe=importe,
                concepto=CONCEPTO,
                fecha=fecha,
            )
        except (afip.AfipError, ValueError) as e:
            error = str(e)
        except Exception as e:  # noqa: BLE001 - mostramos cualquier fallo al usuario
            error = f"Error inesperado: {e}"

        if error:
            # Dejamos registrado el intento fallido en el historial.
            try:
                monto = float(importe) if importe else None
            except ValueError:
                monto = None
            try:
                db.registrar_error(
                    emitido_en=datetime.now(afip.AR_TZ).isoformat(),
                    entorno=ENTORNO,
                    importe=monto,
                    error=error,
                )
            except Exception:  # noqa: BLE001 - no romper la pantalla por el log
                pass

    hoy = datetime.now(afip.AR_TZ).strftime("%Y-%m-%d")
    return render_template(
        "index.html",
        resultado=resultado,
        error=error,
        entorno=ENTORNO,
        punto_venta=PUNTO_VENTA,
        cuit=CUIT,
        hoy=hoy,
    )


@app.route("/resumen", methods=["GET", "POST"])
@login_required
def resumen():
    msg = None
    error = None
    if request.method == "POST":
        if "categoria" in request.form:
            cat = request.form["categoria"].strip().upper()
            if cat in categorias.TOPES or cat == "":
                db.set_config("categoria", cat)
                msg = f"Categoría {cat} guardada." if cat else "Categoría desactivada."
            else:
                error = "Categoría inválida."
        else:
            archivo = request.files.get("csv")
            if not archivo or not archivo.filename:
                error = "Elegí el archivo CSV exportado de Mis Comprobantes."
            else:
                try:
                    r = db.importar_mis_comprobantes(archivo.read(), ENTORNO)
                    if r.get("error"):
                        error = r["error"]
                    else:
                        rango = ""
                        if r["desde"]:
                            rango = f" (del {r['desde'][6:8]}/{r['desde'][4:6]}/{r['desde'][0:4]} al {r['hasta'][6:8]}/{r['hasta'][4:6]}/{r['hasta'][0:4]})"
                        msg = f"Importados {r['importados']} comprobantes{rango}."
                except Exception as e:  # noqa: BLE001
                    error = f"No pude procesar el CSV: {e}"

    # Acumulado móvil de los últimos 12 meses (lo que mira ARCA para la categoría).
    hace_12 = (datetime.now(afip.AR_TZ) - timedelta(days=365)).strftime("%Y%m%d")
    detalle = db.resumen_detallado(ENTORNO, hace_12)

    # Cálculo de la categoría: tope, cuánto falta, porcentaje, sugerida.
    cat = db.get_config("categoria", "")
    total = detalle["movil"]["total"]
    cat_info = None
    if cat in categorias.TOPES:
        tope = categorias.TOPES[cat]
        cat_info = {
            "categoria": cat,
            "tope": tope,
            "restante": tope - total,
            "excedido": total > tope,
            "pct": min(total / tope * 100, 100) if tope else 0,
            "pct_real": (total / tope * 100) if tope else 0,
            "sugerida": categorias.categoria_para(total),
        }

    return render_template(
        "resumen.html",
        meses=detalle["meses"],
        movil12=detalle["movil"],
        info=db.info_mis(ENTORNO),
        entorno=ENTORNO,
        msg=msg,
        error=error,
        categoria=cat,
        cat_info=cat_info,
        topes=categorias.TOPES,
        vigente_desde=categorias.VIGENTE_DESDE,
    )


@app.route("/historial")
@login_required
def historial():
    return render_template(
        "historial.html",
        facturas=db.listar(),
        totales=db.totales(),
    )


if __name__ == "__main__":
    db.init_db()
    # En producción corré con gunicorn/uwsgi detrás de Caddy (ver README).
    app.run(host="127.0.0.1", port=5001, debug=False)
