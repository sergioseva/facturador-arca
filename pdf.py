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
COPIAS = ("ORIGINAL", "DUPLICADO", "TRIPLICADO")


def _ars(v):
    s = f"{float(v):,.2f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def _fecha(yyyymmdd):
    s = str(yyyymmdd or "")
    return f"{s[6:8]}/{s[4:6]}/{s[0:4]}" if len(s) == 8 else (s or "")


def _s(t):
    return str(t if t is not None else "").encode("latin-1", "replace").decode("latin-1")


def _qr_bytes(factura, emisor):
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
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data("https://www.afip.gob.ar/fe/qr/?p=" + b64)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    pil = (img.get_image() if hasattr(img, "get_image") else img).convert("RGB")
    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    return buf.getvalue()


def _lbl(pdf, x, y, w, label, value, h=4.6, size=8):
    pdf.set_xy(x, y)
    pdf.set_font("helvetica", "B", size)
    lw = pdf.get_string_width(label) + 1.2
    pdf.cell(lw, h, _s(label))
    pdf.set_font("helvetica", "", size)
    pdf.set_xy(x + lw, y)
    pdf.multi_cell(w - lw, h, _s(value), align="L")
    return pdf.get_y()


def _draw(pdf, factura, emisor, copia, qr_bytes):
    x0, x1 = 10, 200
    W = x1 - x0
    xv = x0 + 96

    # --- copia (ORIGINAL / DUPLICADO / TRIPLICADO) ---
    pdf.rect(x0, 10, W, 8)
    pdf.set_xy(x0, 10)
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(W, 8, copia, align="C")

    # --- encabezado ---
    hy, hh = 19, 42
    pdf.rect(x0, hy, W, hh)
    pdf.line(xv, hy + 16, xv, hy + hh)  # divisor SOLO debajo de la caja C

    # cuadro de la C (relleno blanco, encima de las líneas)
    cw, ch = 16, 16
    pdf.set_fill_color(255, 255, 255)
    pdf.rect(xv - cw / 2, hy, cw, ch, style="DF")
    pdf.set_xy(xv - cw / 2, hy + 1)
    pdf.set_font("helvetica", "B", 28)
    pdf.cell(cw, 10, "C", align="C")
    pdf.set_xy(xv - cw / 2, hy + 11.5)
    pdf.set_font("helvetica", "", 6.5)
    pdf.cell(cw, 3, "COD. 011", align="C")

    # columna izquierda (emisor)
    cap = (xv - cw / 2) - x0 - 6   # ancho que no llega a la caja C
    lw = (xv - x0) - 6
    ly = hy + 5
    ly = _lbl(pdf, x0 + 3, ly, cap, "Razón Social: ", emisor.get("razon_social", "")) + 1.5
    ly = _lbl(pdf, x0 + 3, ly, cap, "Domicilio Comercial: ", emisor.get("domicilio", "")) + 1.5
    _lbl(pdf, x0 + 3, ly, lw, "Condición frente al IVA: ", emisor.get("condicion", "Responsable Monotributo"))

    # columna derecha (factura)
    pdf.set_xy(xv + 13, hy + 2)
    pdf.set_font("helvetica", "B", 20)
    pdf.cell(x1 - (xv + 13), 9, "FACTURA")
    rw = x1 - xv - 4
    ry = hy + 13
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
    yy = ry0 + 2
    _lbl(pdf, x0 + 3, yy, xv - x0, "Doc.: ", doc)
    _lbl(pdf, xv, yy, x1 - xv, "Apellido y Nombre / Razón Social: ", factura.get("receptor_nombre", ""))
    yy += 6
    _lbl(pdf, x0 + 3, yy, xv - x0, "Condición frente al IVA: ", cond)
    _lbl(pdf, xv, yy, x1 - xv, "Domicilio: ", factura.get("receptor_domicilio", ""))
    yy += 6
    _lbl(pdf, x0 + 3, yy, W, "Condición de venta: ", emisor.get("condicion_venta") or "Contado")

    # --- detalle: encabezado de la tabla ---
    ty = ry0 + rh + 1
    cols = [("Código", 16, "C"), ("Producto / Servicio", 54, "L"), ("Cantidad", 20, "R"),
            ("U. Medida", 20, "C"), ("Precio Unit.", 26, "R"), ("% Bonif", 14, "R"),
            ("Imp. Bonif.", 18, "R"), ("Subtotal", 22, "R")]
    pdf.set_xy(x0, ty)
    pdf.set_font("helvetica", "B", 7.5)
    pdf.set_fill_color(225, 228, 232)
    for nombre, w, _a in cols:
        pdf.cell(w, 6, nombre, border=1, align="C", fill=True)

    # --- cuerpo (un solo rectángulo, con el item adentro y los totales abajo) ---
    body_top = ty + 6
    body_h = 80
    pdf.rect(x0, body_top, W, body_h)
    imp = float(factura["importe"])  # total
    items = factura.get("items") or [
        {"desc": emisor.get("item_descripcion") or "Venta de productos/servicios",
         "cant": 1, "precio": imp, "subtotal": imp}
    ]
    # renglones (texto dentro del cuerpo, sin bordes internos)
    pdf.set_font("helvetica", "", 8)
    yrow = body_top + 1.5
    for it in items:
        fila = ["", it.get("desc", ""), _ars(it.get("cant", 1)), "unidades",
                _ars(it.get("precio", 0)), "0,00", "0,00", _ars(it.get("subtotal", 0))]
        pdf.set_xy(x0, yrow)
        for (nombre, w, a), val in zip(cols, fila):
            pad = "  "
            txt = (pad if a == "L" else "") + val + (pad if a == "R" else "")
            pdf.cell(w, 5.5, _s(txt), align=a)
        yrow += 5.5
    # totales (abajo a la derecha, dentro del cuerpo)
    ty2 = body_top + body_h - 23
    for label, val, bold in (("Subtotal: $", _ars(imp), False),
                             ("Importe Otros Tributos: $", "0,00", False),
                             ("Importe Total: $", _ars(imp), True)):
        pdf.set_font("helvetica", "B", 11 if bold else 9)
        pdf.set_xy(x1 - 112, ty2)
        pdf.cell(75, 6, label, align="R")
        pdf.cell(37, 6, _s(val + "  "), align="R")
        ty2 += 7

    # --- leyenda ---
    yL = body_top + body_h + 2
    if emisor.get("leyenda"):
        pdf.rect(x0, yL, W, 9)
        pdf.set_xy(x0, yL)
        pdf.set_font("helvetica", "I", 9)
        pdf.cell(W, 9, _s(f'"{emisor["leyenda"]}"'), align="C")

    # --- pie: QR + ARCA + CAE ---
    yF = 258
    pdf.image(io.BytesIO(qr_bytes), x=x0 + 2, y=yF, w=26)
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
    pdf.cell(110, 3, "Esta Agencia no se responsabiliza por los datos ingresados en el detalle de la operación")

    pdf.set_xy(x0 + 78, yF)
    pdf.set_font("helvetica", "B", 10)
    pdf.cell(44, 5, "Pág. 1/1", align="C")

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


def factura_pdf(factura, emisor) -> bytes:
    pdf = FPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(False)
    pdf.set_margins(10, 10, 10)
    qr_bytes = _qr_bytes(factura, emisor)
    for copia in COPIAS:
        pdf.add_page()
        _draw(pdf, factura, emisor, copia, qr_bytes)
    return bytes(pdf.output())
