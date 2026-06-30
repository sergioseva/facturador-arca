"""Genera el PDF de la Factura C con el formato oficial de ARCA (+ QR)."""

import base64
import io
import json

import qrcode
from fpdf import FPDF

CBTE_TIPO_FACTURA_C = 11
DOC_NOMBRES = {80: "CUIT", 86: "CUIL", 96: "DNI", 99: "Consumidor Final"}
COND_IVA_NOMBRES = {
    1: "IVA Responsable Inscripto", 4: "IVA Sujeto Exento", 5: "Consumidor Final",
    6: "Responsable Monotributo", 7: "Sujeto No Categorizado", 8: "Proveedor del Exterior",
    9: "Cliente del Exterior", 10: "IVA Liberado - Ley 19.640", 13: "Monotributista Social",
    15: "IVA No Alcanzado", 16: "Monotributo Trab. Indep. Promovido",
}


def _ars(v):
    s = f"{float(v):,.2f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def _fecha(yyyymmdd):
    s = str(yyyymmdd or "")
    return f"{s[6:8]}/{s[4:6]}/{s[0:4]}" if len(s) == 8 else (s or "")


def _s(t):
    return str(t if t is not None else "").encode("latin-1", "replace").decode("latin-1")


def _qr_png(factura, emisor):
    f = str(factura["fecha"])
    data = {
        "ver": 1, "fecha": f"{f[0:4]}-{f[4:6]}-{f[6:8]}", "cuit": int(emisor["cuit"]),
        "ptoVta": int(factura["punto_venta"]), "tipoCmp": CBTE_TIPO_FACTURA_C,
        "nroCmp": int(factura["numero"]), "importe": round(float(factura["importe"]), 2),
        "moneda": "PES", "ctz": 1, "tipoDocRec": int(factura.get("doc_tipo") or 99),
        "nroDocRec": int(factura.get("doc_nro") or 0), "tipoCodAut": "E",
        "codAut": int(factura["cae"]),
    }
    b64 = base64.b64encode(json.dumps(data).encode()).decode()
    img = qrcode.make("https://www.afip.gob.ar/fe/qr/?p=" + b64)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _lbl(pdf, x, y, w, label, value, h=4.6, size=8):
    """Label en negrita + valor normal, con wrap del valor. Devuelve y final."""
    pdf.set_xy(x, y)
    pdf.set_font("helvetica", "B", size)
    lw = pdf.get_string_width(label) + 1.2
    pdf.cell(lw, h, _s(label))
    pdf.set_font("helvetica", "", size)
    pdf.set_xy(x + lw, y)
    pdf.multi_cell(w - lw, h, _s(value), align="L")
    return pdf.get_y()


def factura_pdf(factura, emisor) -> bytes:
    pdf = FPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(False)
    pdf.set_margins(10, 10, 10)
    pdf.add_page()
    x0, x1 = 10, 200
    W = x1 - x0          # 190
    xv = x0 + 96         # divisor del encabezado

    # --- ORIGINAL ---
    pdf.rect(x0, 10, W, 8)
    pdf.set_xy(x0, 10)
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(W, 8, "ORIGINAL", align="C")

    # --- encabezado ---
    hy, hh = 18, 42
    pdf.rect(x0, hy, W, hh)
    pdf.line(xv, hy, xv, hy + hh)
    # cuadro de la C
    cw, ch = 18, 15
    pdf.rect(xv - cw / 2, hy, cw, ch)
    pdf.set_xy(xv - cw / 2, hy + 1.5)
    pdf.set_font("helvetica", "B", 26)
    pdf.cell(cw, 9, "C", align="C")
    pdf.set_xy(xv - cw / 2, hy + 10.5)
    pdf.set_font("helvetica", "", 6.5)
    pdf.cell(cw, 3, "COD. 011", align="C")

    # columna izquierda (emisor)
    lw = (xv - x0) - 4
    ly = hy + 6
    ly = _lbl(pdf, x0 + 3, ly, xv - cw / 2 - x0 - 5, "Razón Social: ", emisor.get("razon_social", "")) + 1.5
    ly = _lbl(pdf, x0 + 3, ly, lw, "Domicilio Comercial: ", emisor.get("domicilio", "")) + 1.5
    _lbl(pdf, x0 + 3, ly, lw, "Condición frente al IVA: ", emisor.get("condicion", "Responsable Monotributo"))

    # columna derecha (factura)
    pdf.set_xy(xv + 12, hy + 2)
    pdf.set_font("helvetica", "B", 20)
    pdf.cell(x1 - (xv + 12), 9, "FACTURA")
    rw = x1 - xv - 4
    ry = hy + 13
    # Pto de venta + Nro
    pdf.set_xy(xv + 3, ry)
    pdf.set_font("helvetica", "B", 9)
    pdf.cell(26, 5, "Punto de Venta: ")
    pdf.cell(16, 5, f"{int(factura['punto_venta']):05d}")
    pdf.cell(20, 5, "Comp. Nro: ")
    pdf.cell(22, 5, f"{int(factura['numero']):08d}")
    ry += 6
    _lbl(pdf, xv + 3, ry, rw, "Fecha de Emisión: ", _fecha(factura["fecha"]), size=9)
    ry += 8
    ry = _lbl(pdf, xv + 3, ry, rw, "CUIT: ", emisor.get("cuit", ""))
    ry = _lbl(pdf, xv + 3, ry, rw, "Ingresos Brutos: ", emisor.get("ingresos_brutos") or "No inscripto")
    _lbl(pdf, xv + 3, ry, rw, "Fecha de Inicio de Actividades: ", emisor.get("inicio_actividades", ""))

    # --- receptor ---
    ry0 = hy + hh
    rh = 20
    pdf.rect(x0, ry0, W, rh)
    dt = int(factura.get("doc_tipo") or 99)
    doc = "-" if dt == 99 else f"{DOC_NOMBRES.get(dt, 'Doc')} {factura.get('doc_nro', '')}"
    cond = COND_IVA_NOMBRES.get(int(factura.get("cond_iva") or 5), "Consumidor Final")
    yy = ry0 + 1.5
    _lbl(pdf, x0 + 3, yy, xv - x0, "Doc.: ", doc)
    _lbl(pdf, xv, yy, x1 - xv, "Apellido y Nombre / Razón Social: ", factura.get("receptor_nombre", ""))
    yy += 6
    _lbl(pdf, x0 + 3, yy, xv - x0, "Condición frente al IVA: ", cond)
    _lbl(pdf, xv, yy, x1 - xv, "Domicilio: ", factura.get("receptor_domicilio", ""))
    yy += 6
    _lbl(pdf, x0 + 3, yy, W, "Condición de venta: ", emisor.get("condicion_venta") or "Contado")

    # --- detalle (tabla) ---
    ty = ry0 + rh + 1
    cols = [("Código", 16, "L"), ("Producto / Servicio", 54, "L"), ("Cantidad", 20, "R"),
            ("U. Medida", 20, "C"), ("Precio Unit.", 26, "R"), ("% Bonif", 14, "R"),
            ("Imp. Bonif.", 18, "R"), ("Subtotal", 22, "R")]
    pdf.set_xy(x0, ty)
    pdf.set_font("helvetica", "B", 7.5)
    pdf.set_fill_color(225, 228, 232)
    for nombre, w, _a in cols:
        pdf.cell(w, 6, nombre, border=1, align="C", fill=True)
    pdf.ln(6)
    # fila del item
    imp = float(factura["importe"])
    item = emisor.get("item_descripcion") or "Venta de productos/servicios"
    valores = ["", item, "1,00", "unidades", _ars(imp), "0,00", "0,00", _ars(imp)]
    pdf.set_x(x0)
    pdf.set_font("helvetica", "", 8)
    for (nombre, w, a), val in zip(cols, valores):
        pdf.cell(w, 6, _s(("  " if a == "L" else "") + val + ("  " if a == "R" else "")), align=a)
    pdf.ln(6)

    # cuerpo vacío con bordes laterales + totales
    body_top = pdf.get_y()
    tot_h = 26
    body_h = 70
    # bordes laterales del cuerpo
    pdf.rect(x0, body_top, W, body_h)
    # totales (abajo a la derecha)
    pdf.set_font("helvetica", "B", 9)
    ty2 = body_top + body_h - tot_h + 4
    for label, val, bold in (("Subtotal: $", _ars(imp), False),
                             ("Importe Otros Tributos: $", "0,00", False),
                             ("Importe Total: $", _ars(imp), True)):
        pdf.set_font("helvetica", "B", 11 if bold else 9)
        pdf.set_xy(x1 - 110, ty2)
        pdf.cell(75, 6, label, align="R")
        pdf.cell(35, 6, val + "  ", align="R")
        ty2 += 7

    # --- leyenda ---
    yL = body_top + body_h + 2
    if emisor.get("leyenda"):
        pdf.rect(x0, yL, W, 9)
        pdf.set_xy(x0, yL)
        pdf.set_font("helvetica", "I", 9)
        pdf.cell(W, 9, _s(f'"{emisor["leyenda"]}"'), align="C")
        yL += 11

    # --- pie: QR + ARCA + CAE ---
    yF = max(yL, 258)
    pdf.image(_qr_png(factura, emisor), x=x0 + 2, y=yF, w=26)
    pdf.set_xy(x0 + 32, yF + 2)
    pdf.set_font("helvetica", "B", 16)
    pdf.cell(40, 7, "ARCA")
    pdf.set_xy(x0 + 32, yF + 9)
    pdf.set_font("helvetica", "", 5.5)
    pdf.cell(40, 3, "AGENCIA DE RECAUDACIÓN")
    pdf.set_xy(x0 + 32, yF + 12)
    pdf.cell(40, 3, "Y CONTROL ADUANERO")
    pdf.set_xy(x0 + 32, yF + 17)
    pdf.set_font("helvetica", "BI", 9)
    pdf.cell(60, 4, "Comprobante Autorizado")
    pdf.set_xy(x0 + 32, yF + 21.5)
    pdf.set_font("helvetica", "B", 6)
    pdf.cell(90, 3, "Esta Agencia no se responsabiliza por los datos ingresados en el detalle de la operación")

    pdf.set_xy(x0 + 80, yF)
    pdf.set_font("helvetica", "B", 10)
    pdf.cell(40, 5, "Pág. 1/1", align="C")

    pdf.set_xy(x1 - 80, yF + 1)
    pdf.set_font("helvetica", "B", 10)
    pdf.cell(80, 5, _s(f"CAE N°: {factura['cae']}"), align="R")
    pdf.set_xy(x1 - 80, yF + 7)
    pdf.cell(80, 5, _s(f"Fecha de Vto. de CAE: {_fecha(factura['cae_vto'])}"), align="R")

    if str(factura.get("entorno")) != "produccion":
        pdf.set_xy(x0, yF + 28)
        pdf.set_font("helvetica", "I", 8)
        pdf.set_text_color(150)
        pdf.cell(W, 5, "Comprobante de prueba (homologación) - sin validez fiscal", align="C")
        pdf.set_text_color(0)

    return bytes(pdf.output())
