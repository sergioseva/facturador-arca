"""Genera el PDF (representación impresa) de una Factura C, con el QR de ARCA."""

import base64
import io
import json

import qrcode
from fpdf import FPDF

CBTE_TIPO_FACTURA_C = 11
DOC_NOMBRES = {80: "CUIT", 86: "CUIL", 96: "DNI", 99: "Consumidor Final"}


def _ars(v):
    s = f"{float(v):,.2f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def _fecha(yyyymmdd):
    s = str(yyyymmdd or "")
    return f"{s[6:8]}/{s[4:6]}/{s[0:4]}" if len(s) == 8 else s


def _s(t):
    """Sanitiza a latin-1 (las fuentes core de fpdf no soportan unicode pleno)."""
    return str(t if t is not None else "").encode("latin-1", "replace").decode("latin-1")


def _qr_png(factura, emisor):
    f = str(factura["fecha"])
    data = {
        "ver": 1,
        "fecha": f"{f[0:4]}-{f[4:6]}-{f[6:8]}",
        "cuit": int(emisor["cuit"]),
        "ptoVta": int(factura["punto_venta"]),
        "tipoCmp": CBTE_TIPO_FACTURA_C,
        "nroCmp": int(factura["numero"]),
        "importe": round(float(factura["importe"]), 2),
        "moneda": "PES",
        "ctz": 1,
        "tipoDocRec": int(factura.get("doc_tipo") or 99),
        "nroDocRec": int(factura.get("doc_nro") or 0),
        "tipoCodAut": "E",
        "codAut": int(factura["cae"]),
    }
    b64 = base64.b64encode(json.dumps(data).encode()).decode()
    img = qrcode.make("https://www.afip.gob.ar/fe/qr/?p=" + b64)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _receptor_str(factura):
    dt = int(factura.get("doc_tipo") or 99)
    if dt == 99:
        return factura.get("receptor") or "Consumidor Final"
    return f"{DOC_NOMBRES.get(dt, 'Doc')} {factura.get('doc_nro', '')}"


def factura_pdf(factura, emisor) -> bytes:
    """
    factura: dict con punto_venta, numero, fecha, importe, cae, cae_vto,
             receptor, doc_tipo, doc_nro, concepto (opcional).
    emisor:  dict con cuit, razon_social, condicion.
    """
    pdf = FPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(15, 12, 15)
    pdf.add_page()
    x0, x1 = 15, 195            # bordes izq/der del contenido
    xm = 105                    # centro
    top = 16

    # --- caja superior con la "C" en el medio ---
    box_h = 34
    pdf.rect(x0, top, x1 - x0, box_h)
    pdf.line(xm, top, xm, top + box_h)
    # cuadrito de la letra C
    cw = 16
    pdf.rect(xm - cw / 2, top - 6, cw, 14)
    pdf.set_font("helvetica", "B", 22)
    pdf.set_xy(xm - cw / 2, top - 5)
    pdf.cell(cw, 10, "C", align="C")
    pdf.set_font("helvetica", "", 7)
    pdf.set_xy(xm - cw / 2, top + 4)
    pdf.cell(cw, 4, "COD. 011", align="C")

    # --- emisor (izquierda) ---
    pdf.set_xy(x0 + 4, top + 4)
    pdf.set_font("helvetica", "B", 13)
    pdf.cell(xm - x0 - 8, 7, _s(emisor.get("razon_social") or ""), align="L")
    pdf.set_xy(x0 + 4, top + 13)
    pdf.set_font("helvetica", "", 9)
    pdf.multi_cell(
        xm - x0 - 8, 5,
        _s(f"CUIT: {emisor['cuit']}\nCondicion frente al IVA: {emisor.get('condicion', 'Responsable Monotributo')}"),
        align="L",
    )

    # --- factura (derecha) ---
    pdf.set_xy(xm + 4, top + 9)
    pdf.set_font("helvetica", "B", 15)
    pdf.cell(x1 - xm - 8, 7, "FACTURA", align="L")
    pdf.set_xy(xm + 4, top + 18)
    pdf.set_font("helvetica", "", 9)
    pdf.multi_cell(
        x1 - xm - 8, 5,
        _s(
            f"Punto de Venta: {int(factura['punto_venta']):05d}    "
            f"Comp. Nro: {int(factura['numero']):08d}\n"
            f"Fecha de Emision: {_fecha(factura['fecha'])}"
        ),
        align="L",
    )

    # --- receptor ---
    y = top + box_h + 4
    pdf.rect(x0, y, x1 - x0, 12)
    pdf.set_xy(x0 + 4, y + 1.5)
    pdf.set_font("helvetica", "", 9)
    pdf.multi_cell(
        x1 - x0 - 8, 4.5,
        _s(f"Receptor: {_receptor_str(factura)}"),
        align="L",
    )

    # --- detalle ---
    y = y + 16
    pdf.set_xy(x0, y)
    pdf.set_font("helvetica", "B", 9)
    pdf.set_fill_color(238, 240, 243)
    pdf.cell(120, 8, "  Descripcion", border=1, fill=True)
    pdf.cell(60, 8, "Importe  ", border=1, fill=True, align="R", ln=1)
    pdf.set_font("helvetica", "", 9)
    concepto = {1: "Productos", 2: "Servicios", 3: "Productos y servicios"}.get(
        int(factura.get("concepto") or 2), "Servicios"
    )
    pdf.cell(120, 8, f"  Venta de {concepto.lower()}", border="LR")
    pdf.cell(60, 8, f"$ {_ars(factura['importe'])}  ", border="LR", align="R", ln=1)
    pdf.cell(120, 8, "", border="LBR")
    pdf.set_font("helvetica", "B", 11)
    pdf.cell(60, 8, f"Total: $ {_ars(factura['importe'])}  ", border="LBR", align="R", ln=1)

    # --- QR + CAE ---
    y = pdf.get_y() + 8
    pdf.image(_qr_png(factura, emisor), x=x0, y=y, w=30)
    pdf.set_xy(x0 + 34, y + 2)
    pdf.set_font("helvetica", "", 9)
    pdf.multi_cell(
        x1 - x0 - 34, 5.5,
        _s(
            f"CAE Nro: {factura['cae']}\n"
            f"Fecha de Vto. de CAE: {_fecha(factura['cae_vto'])}"
        ),
        align="L",
    )
    pdf.set_xy(x0 + 34, y + 16)
    pdf.set_font("helvetica", "B", 9)
    pdf.cell(x1 - x0 - 34, 5, "Comprobante Autorizado", align="L")

    if str(factura.get("entorno")) != "produccion":
        pdf.set_xy(x0, y + 24)
        pdf.set_font("helvetica", "I", 8)
        pdf.set_text_color(150)
        pdf.cell(x1 - x0, 5, "Comprobante de prueba (homologacion) - sin validez fiscal", align="C")
        pdf.set_text_color(0)

    out = pdf.output()
    return bytes(out)
