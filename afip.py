"""
Cliente de los web services de ARCA (ex-AFIP):

  WSAA  -> autenticación: firma un ticket con tu certificado y devuelve
           un Token + Sign que valen ~12hs (los cacheamos en disco).
  WSFEv1 -> facturación electrónica: pedimos el CAE de la factura.

Pensado para Monotributo -> Factura C (tipo 11) a Consumidor Final.
No usa servicios de terceros: habla directo con los servidores de ARCA.
"""

import base64
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    load_pem_private_key,
    pkcs7,
)
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context
from zeep import Client
from zeep.transports import Transport

# --- Endpoints de ARCA -------------------------------------------------------

ENDPOINTS = {
    "homologacion": {
        "wsaa": "https://wsaahomo.afip.gov.ar/ws/services/LoginCms?WSDL",
        "wsfe": "https://wswhomo.afip.gov.ar/wsfev1/service.asmx?WSDL",
    },
    "produccion": {
        "wsaa": "https://wsaa.afip.gov.ar/ws/services/LoginCms?WSDL",
        "wsfe": "https://servicios1.afip.gov.ar/wsfev1/service.asmx?WSDL",
    },
}

# Padrón Constancia de Inscripción (A5): consulta razón social/nombre por CUIT.
PADRON_WSDL = {
    "homologacion": "https://awshomo.afip.gov.ar/sr-padron/webservices/personaServiceA5?WSDL",
    "produccion": "https://aws.afip.gov.ar/sr-padron/webservices/personaServiceA5?WSDL",
}

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


class _AfipSSLAdapter(HTTPAdapter):
    """
    Adapter HTTPS que baja el nivel de seguridad de OpenSSL a SECLEVEL=1.
    Los servidores de PRODUCCIÓN de ARCA negocian Diffie-Hellman de 1024 bits,
    que el OpenSSL moderno rechaza por defecto (DH_KEY_TOO_SMALL). Bajamos el
    nivel SOLO para estas conexiones; la verificación del certificado del
    servidor sigue activa.
    """

    def _ctx(self):
        ctx = create_urllib3_context()
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
        return ctx

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = self._ctx()
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        kwargs["ssl_context"] = self._ctx()
        return super().proxy_manager_for(*args, **kwargs)


def _session():
    s = requests.Session()
    s.mount("https://", _AfipSSLAdapter())
    return s
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# Factura C (Monotributo). Consumidor Final = tipo doc 99, número 0.
CBTE_TIPO_FACTURA_C = 11
DOC_TIPO_CONSUMIDOR_FINAL = 99
DOC_NRO_CONSUMIDOR_FINAL = 0

# Condición frente al IVA del receptor (RG 5616, obligatorio).
# 5 = Consumidor Final. (Lista completa: método FEParamGetCondicionIvaReceptor.)
COND_IVA_CONSUMIDOR_FINAL = 5

# Tipos de documento del receptor (FEParamGetTiposDoc).
DOC_TIPO_NOMBRES = {80: "CUIT", 86: "CUIL", 96: "DNI", 99: "Consumidor Final"}


def receptor_str(doc_tipo, doc_nro):
    """Texto legible del receptor, ej. 'CUIT 20242454600' o 'Consumidor Final'."""
    dt = int(doc_tipo)
    if dt == DOC_TIPO_CONSUMIDOR_FINAL:
        return "Consumidor Final"
    return f"{DOC_TIPO_NOMBRES.get(dt, 'Doc')} {doc_nro}"


class AfipError(Exception):
    """Error devuelto por ARCA (o problema armando el pedido)."""


# --- WSAA: autenticación -----------------------------------------------------


def _login_ticket_xml(service: str) -> bytes:
    """Arma el XML del Login Ticket Request (TRA) que vamos a firmar."""
    now = datetime.now(AR_TZ)
    # Ventana de validez holgada para tolerar desfasajes de reloj.
    gen = (now - timedelta(minutes=10)).isoformat()
    exp = (now + timedelta(hours=12)).isoformat()
    unique_id = int(now.timestamp())
    tra = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<loginTicketRequest version=\"1.0\">"
        "<header>"
        f"<uniqueId>{unique_id}</uniqueId>"
        f"<generationTime>{gen}</generationTime>"
        f"<expirationTime>{exp}</expirationTime>"
        "</header>"
        f"<service>{service}</service>"
        "</loginTicketRequest>"
    )
    return tra.encode("utf-8")


def _sign_tra(tra_xml: bytes, cert_path: str, key_path: str) -> str:
    """Firma el TRA como CMS/PKCS#7 y lo devuelve en base64 (lo que pide WSAA)."""
    cert = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
    key = load_pem_private_key(Path(key_path).read_bytes(), password=None)
    cms = (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(tra_xml)
        .add_signer(cert, key, hashes.SHA256())
        .sign(Encoding.DER, [pkcs7.PKCS7Options.Binary])
    )
    return base64.b64encode(cms).decode("ascii")


def _cache_path(entorno: str, service: str) -> Path:
    # El TA (Token+Sign) es del certificado de la plataforma para ese servicio;
    # NO depende del CUIT representado. Un solo TA sirve para todos los tenants.
    return DATA_DIR / f"ta_{entorno}_{service}.json"


def get_auth(cuit, entorno, cert_path, key_path, service="wsfe"):
    """
    Devuelve {'Token','Sign','Cuit'} listo para mandar a WSFE.
    Token+Sign vienen del certificado de la plataforma (cacheados por
    entorno+servicio, compartidos entre tenants); `Cuit` es el representado y
    varía por llamada (modelo de delegación / computador fiscal).
    """
    cache = _cache_path(entorno, service)
    if cache.exists():
        ta = json.loads(cache.read_text())
        # Renovamos con 10 min de margen antes de que expire.
        if datetime.fromisoformat(ta["expires"]) - timedelta(minutes=10) > datetime.now(AR_TZ):
            return {"Token": ta["token"], "Sign": ta["sign"], "Cuit": int(cuit)}

    tra = _login_ticket_xml(service)
    cms_b64 = _sign_tra(tra, cert_path, key_path)

    client = Client(
        ENDPOINTS[entorno]["wsaa"],
        transport=Transport(timeout=30, session=_session()),
    )
    try:
        response = client.service.loginCms(in0=cms_b64)
    except Exception as e:  # zeep levanta Fault si el ticket es inválido/duplicado
        raise AfipError(f"WSAA rechazó el login: {e}") from e

    # La respuesta es un XML con <credentials><token/><sign/></credentials>
    token, sign, expires = _parse_login_response(response)

    cache.write_text(json.dumps({"token": token, "sign": sign, "expires": expires}))
    return {"Token": token, "Sign": sign, "Cuit": int(cuit)}


def _parse_login_response(xml_text: str):
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml_text)
    token = root.findtext(".//token")
    sign = root.findtext(".//sign")
    exp = root.findtext(".//expirationTime")
    return token, sign, exp


# --- WSFEv1: facturación -----------------------------------------------------


def _wsfe_client(entorno: str) -> Client:
    return Client(
        ENDPOINTS[entorno]["wsfe"],
        transport=Transport(timeout=30, session=_session()),
    )


def proximo_numero(client, auth, punto_venta, cbte_tipo=CBTE_TIPO_FACTURA_C):
    """Pide a ARCA el último comprobante autorizado y devuelve el siguiente."""
    resp = client.service.FECompUltimoAutorizado(
        Auth=auth, PtoVta=punto_venta, CbteTipo=cbte_tipo
    )
    _raise_on_errors(getattr(resp, "Errors", None))
    return int(resp.CbteNro) + 1


def verificar_delegacion(cuit, entorno, cert_path, key_path, punto_venta,
                         cbte_tipo=CBTE_TIPO_FACTURA_C):
    """
    Chequea (sin emitir nada) que el tenant autorizó el computador fiscal de la
    plataforma para WSFE: pide el último comprobante autorizado en su PV. Si la
    delegación o el punto de venta no están bien, ARCA devuelve un error que se
    propaga como AfipError. Devuelve {'ultimo': int} si todo está OK.
    """
    auth = get_auth(cuit, entorno, cert_path, key_path, service="wsfe")
    client = _wsfe_client(entorno)
    resp = client.service.FECompUltimoAutorizado(
        Auth=auth, PtoVta=int(punto_venta), CbteTipo=cbte_tipo
    )
    _raise_on_errors(getattr(resp, "Errors", None))
    return {"ultimo": int(resp.CbteNro)}


def consultar_padron(idpersona, entorno, cert_path, key_path, cuit_plataforma):
    """
    Consulta el padrón de ARCA (constancia A5) por CUIT/CUIL y devuelve
    {'nombre', 'tipo_persona', 'estado'}. Requiere que el cert de la PLATAFORMA
    esté autorizado para el servicio 'ws_sr_padron_a5'.
    """
    idp = "".join(c for c in str(idpersona) if c.isdigit())
    if len(idp) != 11:
        raise AfipError("La consulta de padrón necesita un CUIT/CUIL de 11 dígitos.")

    auth = get_auth(
        cuit_plataforma, entorno, cert_path, key_path, service="ws_sr_constancia_inscripcion"
    )
    client = Client(PADRON_WSDL[entorno], transport=Transport(timeout=30, session=_session()))
    try:
        resp = client.service.getPersona(
            token=auth["Token"], sign=auth["Sign"],
            cuitRepresentada=int(cuit_plataforma), idPersona=int(idp),
        )
    except Exception as e:  # noqa: BLE001
        raise AfipError(f"No se pudo consultar el padrón: {e}") from e

    import zeep

    d = zeep.helpers.serialize_object(resp) or {}
    err = d.get("errorConstancia")
    if err:
        e = err.get("error") if isinstance(err, dict) else None
        msg = "; ".join(e) if isinstance(e, list) else (str(e) if e else "documento no encontrado")
        raise AfipError(f"Padrón: {msg}")

    dg = d.get("datosGenerales") or {}
    razon = (dg.get("razonSocial") or "").strip()
    nombre = razon or (
        (dg.get("apellido") or "").strip() + " " + (dg.get("nombre") or "").strip()
    ).strip()
    if not nombre:
        raise AfipError("No se encontró el nombre para ese documento.")
    return {
        "nombre": nombre,
        "tipo_persona": dg.get("tipoPersona"),
        "estado": dg.get("estadoClave"),
    }


def _normalizar_fecha(fecha):
    """
    Devuelve la fecha del comprobante en formato yyyymmdd.
    Acepta None (=hoy), 'yyyy-mm-dd' o 'yyyymmdd'.
    Valida la ventana que permite ARCA (no muy lejos de hoy).
    """
    hoy = datetime.now(AR_TZ).date()
    if not fecha:
        return hoy.strftime("%Y%m%d")

    fecha = fecha.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            d = datetime.strptime(fecha, fmt).date()
            break
        except ValueError:
            d = None
    if d is None:
        raise AfipError(f"Fecha inválida: {fecha!r} (usá AAAA-MM-DD).")

    if d > hoy:
        raise AfipError("La fecha no puede ser futura.")
    # ARCA tolera comprobantes atrasados hasta ~10 días para servicios.
    if (hoy - d).days > 10:
        raise AfipError(
            "ARCA solo permite facturar hasta 10 días hacia atrás. "
            f"La fecha {d.isoformat()} quedó fuera de ese rango."
        )
    return d.strftime("%Y%m%d")


def sincronizar_comprobantes(
    user_id, cuit, entorno, cert_path, key_path, punto_venta,
    cbte_tipo=CBTE_TIPO_FACTURA_C
):
    """
    Lee de ARCA los comprobantes que falten en la base local (incremental) y
    los guarda para `user_id`. Devuelve {'ultimo', 'nuevos'}.
    """
    import db

    auth = get_auth(cuit, entorno, cert_path, key_path, service="wsfe")
    client = _wsfe_client(entorno)

    ult = client.service.FECompUltimoAutorizado(
        Auth=auth, PtoVta=int(punto_venta), CbteTipo=cbte_tipo
    )
    _raise_on_errors(getattr(ult, "Errors", None))
    ultimo = int(ult.CbteNro)

    ya_tengo = db.max_numero_arca(user_id, entorno, int(punto_venta), cbte_tipo)
    filas = []
    for n in range(ya_tengo + 1, ultimo + 1):
        r = client.service.FECompConsultar(
            Auth=auth,
            FeCompConsReq={"CbteTipo": cbte_tipo, "CbteNro": n, "PtoVta": int(punto_venta)},
        )
        _raise_on_errors(getattr(r, "Errors", None))
        d = r.ResultGet
        filas.append(
            (entorno, int(punto_venta), cbte_tipo, n, str(d.CbteFch), float(d.ImpTotal))
        )

    db.guardar_comprobantes_arca(user_id, filas)
    return {"ultimo": ultimo, "nuevos": len(filas)}


def emitir_factura_c(
    cuit,
    entorno,
    cert_path,
    key_path,
    punto_venta,
    importe,
    concepto=2,
    fecha=None,
    actividad=None,
    doc_tipo=None,
    doc_nro=None,
    cond_iva_receptor=None,
):
    """
    Emite una Factura C por `importe` (total).
    Receptor opcional: por defecto Consumidor Final (doc 99). Si se pasa
    `doc_tipo` (80=CUIT, 86=CUIL, 96=DNI) + `doc_nro`, identifica al receptor.
    `cond_iva_receptor` (RG 5616) por defecto Consumidor Final (5).
    Devuelve un dict con el resultado (CAE, vencimiento, número, receptor, etc.).
    """
    importe = round(float(importe), 2)
    if importe <= 0:
        raise AfipError("El importe tiene que ser mayor a 0.")

    # Receptor
    dt = int(doc_tipo) if doc_tipo else DOC_TIPO_CONSUMIDOR_FINAL
    if dt == DOC_TIPO_CONSUMIDOR_FINAL:
        dn, civa = DOC_NRO_CONSUMIDOR_FINAL, COND_IVA_CONSUMIDOR_FINAL
    else:
        dn_str = "".join(ch for ch in str(doc_nro or "") if ch.isdigit())
        if not dn_str:
            raise AfipError("Falta el número de documento del receptor.")
        if dt in (80, 86) and len(dn_str) != 11:
            raise AfipError("El CUIT/CUIL debe tener 11 dígitos.")
        dn = int(dn_str)
        civa = int(cond_iva_receptor) if cond_iva_receptor else COND_IVA_CONSUMIDOR_FINAL

    hoy = _normalizar_fecha(fecha)

    auth = get_auth(cuit, entorno, cert_path, key_path, service="wsfe")
    client = _wsfe_client(entorno)

    numero = proximo_numero(client, auth, punto_venta)

    detalle = {
        "Concepto": int(concepto),
        "DocTipo": dt,
        "DocNro": dn,
        "CondicionIVAReceptorId": civa,
        "CbteDesde": numero,
        "CbteHasta": numero,
        "CbteFch": hoy,
        "ImpTotal": importe,
        "ImpTotConc": 0,      # neto no gravado
        "ImpNeto": importe,   # en Factura C el neto = total (no se discrimina IVA)
        "ImpOpEx": 0,
        "ImpIVA": 0,
        "ImpTrib": 0,
        "MonId": "PES",
        "MonCotiz": 1,
    }

    # Para Servicios (concepto 2 o 3) ARCA exige fechas del período y vto. de pago.
    if int(concepto) in (2, 3):
        detalle["FchServDesde"] = hoy
        detalle["FchServHasta"] = hoy
        detalle["FchVtoPago"] = hoy

    # Actividad asociada (ej. 476110 venta de libros), opcional.
    if actividad:
        detalle["Actividades"] = {"Actividad": [{"Id": int(actividad)}]}

    pedido = {
        "FeCabReq": {
            "CantReg": 1,
            "PtoVta": int(punto_venta),
            "CbteTipo": CBTE_TIPO_FACTURA_C,
        },
        "FeDetReq": {"FECAEDetRequest": [detalle]},
    }

    resp = client.service.FECAESolicitar(Auth=auth, FeCAEReq=pedido)
    _raise_on_errors(getattr(resp, "Errors", None))

    det = resp.FeDetResp.FECAEDetResponse[0]
    cab = resp.FeCabResp

    if cab.Resultado != "A" or det.Resultado != "A":
        obs = _format_observaciones(getattr(det, "Observaciones", None))
        raise AfipError(f"ARCA rechazó la factura (Resultado {det.Resultado}). {obs}".strip())

    resultado = {
        "cae": det.CAE,
        "cae_vto": det.CAEFchVto,        # yyyymmdd
        "numero": numero,
        "punto_venta": int(punto_venta),
        "tipo": "Factura C",
        "importe": importe,
        "fecha": hoy,
        "entorno": entorno,
        "emitido_en": datetime.now(AR_TZ).isoformat(),
        "observaciones": _format_observaciones(getattr(det, "Observaciones", None)),
        "doc_tipo": dt,
        "doc_nro": dn,
        "receptor": receptor_str(dt, dn),
    }
    # La persistencia la hace la ruta (con user_id); afip.py es agnóstico de usuario.
    return resultado


# --- helpers de errores / log ------------------------------------------------


def _raise_on_errors(errors):
    if not errors:
        return
    items = getattr(errors, "Err", None)
    if not items:
        return
    msgs = [f"[{e.Code}] {e.Msg}" for e in items]
    raise AfipError("ARCA devolvió errores: " + " | ".join(msgs))


def _format_observaciones(observaciones):
    if not observaciones:
        return ""
    items = getattr(observaciones, "Obs", None)
    if not items:
        return ""
    return "Observaciones: " + " | ".join(f"[{o.Code}] {o.Msg}" for o in items)
