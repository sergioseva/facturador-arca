"""Persistencia de los intentos de facturación en SQLite (data/facturas.db).

Guarda TODOS los intentos: los emitidos con éxito y los que ARCA (u otro
problema) rechazó, con su mensaje de error, para tener un historial completo.
"""

import sqlite3
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


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
