"""Persistencia de los intentos de facturación en SQLite (data/facturas.db).

Guarda TODOS los intentos: los emitidos con éxito y los que ARCA (u otro
problema) rechazó, con su mensaje de error, para tener un historial completo.
"""

import csv
import io
import sqlite3
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "facturas.db"
DB_PATH.parent.mkdir(exist_ok=True)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facturas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    emitido_en    TEXT NOT NULL,
    entorno       TEXT NOT NULL,
    estado        TEXT NOT NULL DEFAULT 'emitida',   -- 'emitida' | 'error'
    tipo          TEXT,
    punto_venta   INTEGER,
    numero        INTEGER,
    fecha         TEXT,
    importe       REAL,
    cae           TEXT,
    cae_vto       TEXT,
    observaciones TEXT,
    error         TEXT
)
"""


# Tabla espejo de los comprobantes leídos de ARCA (fuente de verdad para los
# totales: incluye también facturas hechas fuera de esta app).
_SCHEMA_ARCA = """
CREATE TABLE IF NOT EXISTS arca_comprobantes (
    entorno     TEXT NOT NULL,
    punto_venta INTEGER NOT NULL,
    cbte_tipo   INTEGER NOT NULL,
    numero      INTEGER NOT NULL,
    fecha       TEXT NOT NULL,       -- yyyymmdd
    importe     REAL NOT NULL,
    PRIMARY KEY (entorno, punto_venta, cbte_tipo, numero)
)
"""


# Comprobantes importados del CSV de "Mis Comprobantes" de ARCA. A diferencia
# del web service, este CSV incluye TODOS los puntos de venta y tipos (también
# los del facturador online), así que es la fuente completa para el control de
# categoría. El importe se guarda con signo (las notas de crédito restan).
_SCHEMA_MIS = """
CREATE TABLE IF NOT EXISTS mis_comprobantes (
    entorno     TEXT NOT NULL,
    punto_venta INTEGER NOT NULL,
    tipo        TEXT NOT NULL,
    numero      INTEGER NOT NULL,
    fecha       TEXT NOT NULL,       -- yyyymmdd
    importe     REAL NOT NULL,       -- con signo (NC negativas)
    PRIMARY KEY (entorno, punto_venta, tipo, numero)
)
"""


# Preferencias de la app (ej. la categoría de monotributo elegida).
_SCHEMA_CONFIG = """
CREATE TABLE IF NOT EXISTS config (
    clave TEXT PRIMARY KEY,
    valor TEXT
)
"""


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_config(clave, default=None):
    init_db()
    with _conn() as conn:
        row = conn.execute("SELECT valor FROM config WHERE clave=?", (clave,)).fetchone()
        return row["valor"] if row else default


def set_config(clave, valor):
    init_db()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO config (clave, valor) VALUES (?, ?) "
            "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
            (clave, valor),
        )


def _norm(s):
    """minúsculas, sin acentos, sin espacios/puntos — para matchear encabezados."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return "".join(c for c in s.lower() if c.isalnum())


def _parse_num(s):
    """'1.234,56' / '1234.56' / '1234,56' / '1234' -> float."""
    s = (s or "").strip().replace("$", "").replace(" ", "")
    if not s:
        return 0.0
    if "," in s and "." in s:
        # el separador más a la derecha es el decimal
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_fecha(s):
    """Devuelve yyyymmdd o None."""
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y%m%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y%m%d")
        except ValueError:
            pass
    return None


def init_db():
    """Crea/migra la tabla. Idempotente."""
    with _conn() as conn:
        existe = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='facturas'"
        ).fetchone()
        cols = (
            [r["name"] for r in conn.execute("PRAGMA table_info(facturas)")]
            if existe
            else []
        )
        if existe and "estado" not in cols:
            # Esquema viejo (solo facturas emitidas): migramos a la tabla nueva.
            conn.execute("ALTER TABLE facturas RENAME TO facturas_old")
            conn.execute(_SCHEMA)
            conn.execute(
                """
                INSERT INTO facturas
                    (id, emitido_en, entorno, estado, tipo, punto_venta,
                     numero, fecha, importe, cae, cae_vto, observaciones)
                SELECT id, emitido_en, entorno, 'emitida', tipo, punto_venta,
                       numero, fecha, importe, cae, cae_vto, observaciones
                FROM facturas_old
                """
            )
            conn.execute("DROP TABLE facturas_old")
        else:
            conn.execute(_SCHEMA)
        conn.execute(_SCHEMA_ARCA)
        conn.execute(_SCHEMA_MIS)
        conn.execute(_SCHEMA_CONFIG)


def guardar(resultado: dict) -> int:
    """Guarda una factura emitida con éxito y devuelve su id."""
    init_db()
    with _conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO facturas
                (emitido_en, entorno, estado, tipo, punto_venta, numero,
                 fecha, importe, cae, cae_vto, observaciones, error)
            VALUES (?, ?, 'emitida', ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                resultado["emitido_en"],
                resultado["entorno"],
                resultado["tipo"],
                resultado["punto_venta"],
                resultado["numero"],
                resultado["fecha"],
                resultado["importe"],
                resultado["cae"],
                resultado["cae_vto"],
                resultado.get("observaciones", ""),
            ),
        )
        return cur.lastrowid


def registrar_error(emitido_en, entorno, importe, error) -> int:
    """Guarda un intento que falló (rechazo de ARCA, monto inválido, red, etc.)."""
    init_db()
    with _conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO facturas
                (emitido_en, entorno, estado, importe, error)
            VALUES (?, ?, 'error', ?, ?)
            """,
            (emitido_en, entorno, importe, error),
        )
        return cur.lastrowid


def max_numero_arca(entorno, punto_venta, cbte_tipo) -> int:
    """Último número de comprobante ya sincronizado (0 si no hay ninguno)."""
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(numero), 0) m FROM arca_comprobantes "
            "WHERE entorno=? AND punto_venta=? AND cbte_tipo=?",
            (entorno, punto_venta, cbte_tipo),
        ).fetchone()
        return row["m"]


def guardar_comprobantes_arca(filas):
    """Inserta (o reemplaza) comprobantes leídos de ARCA. `filas` = lista de tuplas."""
    if not filas:
        return
    init_db()
    with _conn() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO arca_comprobantes "
            "(entorno, punto_venta, cbte_tipo, numero, fecha, importe) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            filas,
        )


def resumen_mensual(entorno):
    """Total e importe por mes (yyyymm) para un entorno, más reciente primero."""
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT substr(fecha,1,6) mes, COUNT(*) cant, SUM(importe) total "
            "FROM arca_comprobantes WHERE entorno=? GROUP BY mes ORDER BY mes DESC",
            (entorno,),
        ).fetchall()
        return [dict(r) for r in rows]


def acumulado_desde(entorno, fecha_desde):
    """Suma de importes con fecha (yyyymmdd) >= fecha_desde. Para el móvil 12 meses."""
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(importe),0) total, COUNT(*) cant "
            "FROM arca_comprobantes WHERE entorno=? AND fecha>=?",
            (entorno, fecha_desde),
        ).fetchone()
        return {"total": row["total"], "cantidad": row["cant"]}


def importar_mis_comprobantes(contenido: bytes, entorno: str):
    """
    Parsea el CSV de 'Mis Comprobantes' (emitidos) y lo guarda. Matchea columnas
    por nombre (tolerante a acentos/orden), detecta el separador, y suma con
    signo (notas de crédito restan). Devuelve un resumen del import.
    """
    init_db()

    # AFIP entrega el export en un ZIP con el CSV adentro. Si viene comprimido,
    # lo descomprimimos y tomamos el primer .csv. Si ya es un CSV, lo usamos tal cual.
    if contenido[:2] == b"PK":  # firma de archivo ZIP
        try:
            with zipfile.ZipFile(io.BytesIO(contenido)) as z:
                csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
                if not csvs:
                    return {"error": "El ZIP no contiene ningún archivo .csv."}
                contenido = z.read(csvs[0])
        except zipfile.BadZipFile:
            return {"error": "El archivo parece un ZIP pero está dañado."}

    # Mis Comprobantes suele venir en latin-1; probamos utf-8 primero.
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            texto = contenido.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return {"error": "No pude leer el archivo (codificación desconocida)."}

    # Detectar separador (; o ,) por la primera línea.
    primera = texto.splitlines()[0] if texto.strip() else ""
    sep = ";" if primera.count(";") >= primera.count(",") else ","
    lector = csv.reader(io.StringIO(texto), delimiter=sep)

    filas = list(lector)
    if not filas:
        return {"error": "El archivo está vacío."}

    cab = {_norm(c): i for i, c in enumerate(filas[0])}

    def col(*nombres):
        for n in nombres:
            if _norm(n) in cab:
                return cab[_norm(n)]
        return None

    i_fecha = col("Fecha de Emisión", "Fecha", "Fecha Emision")
    i_tipo = col("Tipo de Comprobante", "Tipo")
    i_pv = col("Punto de Venta", "Punto Venta")
    i_num = col("Número Desde", "Numero Desde", "Número", "Numero")
    i_imp = col("Imp. Total", "Importe Total", "Imp Total")

    if i_fecha is None or i_imp is None:
        return {
            "error": "El CSV no tiene las columnas esperadas (Fecha de Emisión / Imp. Total). "
            "¿Exportaste 'Comprobantes Emitidos' de Mis Comprobantes?"
        }

    registros, leidas, ignoradas = [], 0, 0
    for fila in filas[1:]:
        if not fila or len(fila) <= i_imp:
            continue
        fecha = _parse_fecha(fila[i_fecha])
        if not fecha:  # filas de subtotales/encabezados sueltos
            ignoradas += 1
            continue
        tipo = fila[i_tipo].strip() if i_tipo is not None else "Comprobante"
        pv = int(_parse_num(fila[i_pv])) if i_pv is not None else 0
        numero = int(_parse_num(fila[i_num])) if i_num is not None else leidas + 1
        importe = _parse_num(fila[i_imp])
        if "credito" in _norm(tipo):  # nota de crédito resta
            importe = -abs(importe)
        registros.append((entorno, pv, tipo, numero, fecha, importe))
        leidas += 1

    if registros:
        with _conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO mis_comprobantes "
                "(entorno, punto_venta, tipo, numero, fecha, importe) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                registros,
            )

    fechas = [r[4] for r in registros]
    return {
        "importados": leidas,
        "ignoradas": ignoradas,
        "desde": min(fechas) if fechas else None,
        "hasta": max(fechas) if fechas else None,
    }


def resumen_mensual_mis(entorno):
    """Total por mes (yyyymm) desde el CSV de Mis Comprobantes."""
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT substr(fecha,1,6) mes, COUNT(*) cant, SUM(importe) total "
            "FROM mis_comprobantes WHERE entorno=? GROUP BY mes ORDER BY mes DESC",
            (entorno,),
        ).fetchall()
        return [dict(r) for r in rows]


def acumulado_desde_mis(entorno, fecha_desde):
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(importe),0) total, COUNT(*) cant "
            "FROM mis_comprobantes WHERE entorno=? AND fecha>=?",
            (entorno, fecha_desde),
        ).fetchone()
        return {"total": row["total"], "cantidad": row["cant"]}


def resumen_detallado(entorno, fecha_movil):
    """
    Unifica las dos fuentes para el control de monotributo:
      - 'facturador': facturas emitidas por esta app (tabla facturas).
      - 'importada':  comprobantes del CSV de Mis Comprobantes (otros PV, etc.).
    Evita el doble conteo: si una factura del CSV coincide con una emitida por la
    app (mismo PV + número), se cuenta una sola vez como 'facturador'.
    Devuelve los comprobantes agrupados por mes (con su detalle) y el acumulado
    móvil de 12 meses.
    """
    init_db()
    with _conn() as conn:
        app_rows = conn.execute(
            "SELECT punto_venta, numero, fecha, importe FROM facturas "
            "WHERE entorno=? AND estado='emitida'",
            (entorno,),
        ).fetchall()
        mis_rows = conn.execute(
            "SELECT punto_venta, numero, fecha, importe, tipo FROM mis_comprobantes "
            "WHERE entorno=?",
            (entorno,),
        ).fetchall()

    app_keys = {(r["punto_venta"], r["numero"]) for r in app_rows}
    comprobantes = [
        {
            "fecha": r["fecha"], "tipo": "Factura C", "punto_venta": r["punto_venta"],
            "numero": r["numero"], "importe": r["importe"], "origen": "facturador",
        }
        for r in app_rows
    ]
    for r in mis_rows:
        es_factura = "factura" in _norm(r["tipo"])
        if es_factura and (r["punto_venta"], r["numero"]) in app_keys:
            continue  # ya contada como 'facturador'
        comprobantes.append(
            {
                "fecha": r["fecha"], "tipo": r["tipo"], "punto_venta": r["punto_venta"],
                "numero": r["numero"], "importe": r["importe"], "origen": "importada",
            }
        )

    # Agrupar por mes (yyyymm)
    meses = {}
    for c in comprobantes:
        m = meses.setdefault(
            c["fecha"][:6],
            {"mes": c["fecha"][:6], "total": 0.0, "cantidad": 0,
             "n_facturador": 0, "n_importada": 0, "comprobantes": []},
        )
        m["total"] += c["importe"]
        m["cantidad"] += 1
        m["n_facturador" if c["origen"] == "facturador" else "n_importada"] += 1
        m["comprobantes"].append(c)

    for m in meses.values():
        m["comprobantes"].sort(key=lambda x: (x["fecha"], x["punto_venta"], x["numero"]))

    meses_ordenados = [meses[k] for k in sorted(meses, reverse=True)]
    movil = [c for c in comprobantes if c["fecha"] >= fecha_movil]
    return {
        "meses": meses_ordenados,
        "movil": {"total": sum(c["importe"] for c in movil), "cantidad": len(movil)},
    }


def info_mis(entorno):
    """Rango de fechas y cantidad de comprobantes importados (o None si no hay)."""
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) cant, MIN(fecha) desde, MAX(fecha) hasta, "
            "COUNT(DISTINCT punto_venta) pvs FROM mis_comprobantes WHERE entorno=?",
            (entorno,),
        ).fetchone()
        if not row["cant"]:
            return None
        return {"cant": row["cant"], "desde": row["desde"], "hasta": row["hasta"], "pvs": row["pvs"]}


def listar(limite: int = 200):
    """Devuelve los intentos más recientes primero (emitidos y con error)."""
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM facturas ORDER BY id DESC LIMIT ?", (limite,)
        ).fetchall()
        return [dict(r) for r in rows]


def totales():
    """Resumen por entorno: facturas emitidas, monto total y cantidad de errores."""
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT entorno,
                   SUM(estado = 'emitida')                      AS emitidas,
                   COALESCE(SUM(CASE WHEN estado='emitida' THEN importe END), 0) AS total,
                   SUM(estado = 'error')                        AS errores
            FROM facturas GROUP BY entorno
            """
        ).fetchall()
        return {
            r["entorno"]: {
                "emitidas": r["emitidas"],
                "total": r["total"],
                "errores": r["errores"],
            }
            for r in rows
        }
