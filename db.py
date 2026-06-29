"""Persistencia en SQLite (data/facturas.db) — multiusuario.

Cada usuario (tenant) tiene su cuenta, su configuración fiscal (tenant_config) y
sus datos aislados por `user_id`: facturas (intentos emitidos/con error),
comprobantes leídos de ARCA y los importados del CSV de Mis Comprobantes.
Los topes de categoría (`config`) son globales (iguales para todos).
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

# --- Esquemas ---------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facturas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER,
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
    error         TEXT,
    receptor      TEXT
)
"""

# Receptores recientes por tenant (para reusar al facturar a los mismos).
_SCHEMA_RECEPTORES = """
CREATE TABLE IF NOT EXISTS receptores (
    user_id    INTEGER NOT NULL,
    doc_tipo   INTEGER NOT NULL,
    doc_nro    TEXT NOT NULL,
    nombre     TEXT,
    cond_iva   INTEGER,
    usos       INTEGER NOT NULL DEFAULT 0,
    ultimo_uso TEXT,
    PRIMARY KEY (user_id, doc_tipo, doc_nro)
)
"""

# Espejo de los comprobantes leídos de ARCA por web service (por tenant).
_SCHEMA_ARCA = """
CREATE TABLE IF NOT EXISTS arca_comprobantes (
    user_id     INTEGER NOT NULL,
    entorno     TEXT NOT NULL,
    punto_venta INTEGER NOT NULL,
    cbte_tipo   INTEGER NOT NULL,
    numero      INTEGER NOT NULL,
    fecha       TEXT NOT NULL,       -- yyyymmdd
    importe     REAL NOT NULL,
    PRIMARY KEY (user_id, entorno, punto_venta, cbte_tipo, numero)
)
"""

# Comprobantes importados del CSV de "Mis Comprobantes" (todos los PV del tenant).
_SCHEMA_MIS = """
CREATE TABLE IF NOT EXISTS mis_comprobantes (
    user_id     INTEGER NOT NULL,
    entorno     TEXT NOT NULL,
    punto_venta INTEGER NOT NULL,
    tipo        TEXT NOT NULL,
    numero      INTEGER NOT NULL,
    fecha       TEXT NOT NULL,       -- yyyymmdd
    importe     REAL NOT NULL,       -- con signo (NC negativas)
    PRIMARY KEY (user_id, entorno, punto_venta, tipo, numero)
)
"""

# Config GLOBAL de la plataforma (topes de categoría, iguales para todos).
_SCHEMA_CONFIG = """
CREATE TABLE IF NOT EXISTS config (
    clave TEXT PRIMARY KEY,
    valor TEXT
)
"""

# Cuentas de usuario.
_SCHEMA_USERS = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',   -- 'admin' | 'user'
    activo        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL
)
"""

# Config fiscal por tenant (reemplaza la parte "por usuario" del .env).
_SCHEMA_TENANT = """
CREATE TABLE IF NOT EXISTS tenant_config (
    user_id         INTEGER PRIMARY KEY,
    cuit            TEXT,
    punto_venta     INTEGER,
    entorno         TEXT NOT NULL DEFAULT 'homologacion',
    concepto        INTEGER NOT NULL DEFAULT 2,
    actividad       TEXT,
    razon_social    TEXT,
    categoria       TEXT DEFAULT '',
    delegacion_ok   INTEGER NOT NULL DEFAULT 0,
    onboarding_step INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
)
"""

_TENANT_COLS = {
    "cuit", "punto_venta", "entorno", "concepto", "actividad",
    "razon_social", "categoria", "delegacion_ok", "onboarding_step",
}


def _now():
    return datetime.now().isoformat()


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _cols(conn, table):
    existe = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not existe:
        return []
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def _migrar_user_id_pk(conn, tabla, schema, columnas):
    """
    Para tablas cuyo PK incluye user_id (arca_comprobantes, mis_comprobantes):
    si la tabla existe sin columna user_id, rename-create-copy asignando todas
    las filas viejas al user_id=1 (el dueño/admin). Si no existe o ya migró,
    simplemente crea el schema nuevo (idempotente).
    """
    cols = _cols(conn, tabla)
    if cols and "user_id" not in cols:
        conn.execute(f"ALTER TABLE {tabla} RENAME TO {tabla}_old")
        conn.execute(schema)
        conn.execute(
            f"INSERT INTO {tabla} (user_id, {columnas}) "
            f"SELECT 1, {columnas} FROM {tabla}_old"
        )
        conn.execute(f"DROP TABLE {tabla}_old")
    else:
        conn.execute(schema)


def init_db():
    """Crea/migra el esquema. Idempotente."""
    with _conn() as conn:
        # --- facturas: migrar a 'estado' (esquema muy viejo) y a 'user_id' ---
        cols_f = _cols(conn, "facturas")
        if cols_f and "estado" not in cols_f:
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
        cols_f = _cols(conn, "facturas")
        if "user_id" not in cols_f:
            conn.execute("ALTER TABLE facturas ADD COLUMN user_id INTEGER")
            conn.execute("UPDATE facturas SET user_id=1 WHERE user_id IS NULL")
        if "receptor" not in cols_f:
            conn.execute("ALTER TABLE facturas ADD COLUMN receptor TEXT")

        # --- tablas con user_id en el PK ---
        _migrar_user_id_pk(
            conn, "arca_comprobantes", _SCHEMA_ARCA,
            "entorno, punto_venta, cbte_tipo, numero, fecha, importe",
        )
        _migrar_user_id_pk(
            conn, "mis_comprobantes", _SCHEMA_MIS,
            "entorno, punto_venta, tipo, numero, fecha, importe",
        )

        conn.execute(_SCHEMA_CONFIG)
        conn.execute(_SCHEMA_USERS)
        conn.execute(_SCHEMA_TENANT)
        conn.execute(_SCHEMA_RECEPTORES)


# --- Config global (topes de categoría) -------------------------------------


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


# --- Usuarios ---------------------------------------------------------------


def create_user(email, password_hash, role="user", activo=1):
    init_db()
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, role, activo, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (email.strip().lower(), password_hash, role, 1 if activo else 0, _now()),
        )
        return cur.lastrowid


def get_user_by_email(email):
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email=?", (email.strip().lower(),)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(uid):
    init_db()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return dict(row) if row else None


def list_users():
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, email, role, activo, created_at FROM users ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def count_users():
    init_db()
    with _conn() as conn:
        return conn.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]


def set_user_active(uid, activo):
    init_db()
    with _conn() as conn:
        conn.execute("UPDATE users SET activo=? WHERE id=?", (1 if activo else 0, uid))


def set_user_password(uid, password_hash):
    init_db()
    with _conn() as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, uid))


# --- Config por tenant ------------------------------------------------------


def get_tenant_config(user_id):
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM tenant_config WHERE user_id=?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def upsert_tenant_config(user_id, **fields):
    """Crea la fila si no existe y actualiza los campos permitidos pasados."""
    init_db()
    fields = {k: v for k, v in fields.items() if k in _TENANT_COLS}
    with _conn() as conn:
        existe = conn.execute(
            "SELECT 1 FROM tenant_config WHERE user_id=?", (user_id,)
        ).fetchone()
        if not existe:
            conn.execute(
                "INSERT INTO tenant_config (user_id, created_at) VALUES (?, ?)",
                (user_id, _now()),
            )
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            conn.execute(
                f"UPDATE tenant_config SET {sets} WHERE user_id=?",
                (*fields.values(), user_id),
            )


def set_delegacion_ok(user_id, ok):
    upsert_tenant_config(user_id, delegacion_ok=1 if ok else 0)


# --- CSV helpers ------------------------------------------------------------


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


# --- Facturas (intentos) ----------------------------------------------------


def guardar(user_id, resultado: dict) -> int:
    """Guarda una factura emitida con éxito y devuelve su id."""
    init_db()
    with _conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO facturas
                (user_id, emitido_en, entorno, estado, tipo, punto_venta, numero,
                 fecha, importe, cae, cae_vto, observaciones, error, receptor)
            VALUES (?, ?, ?, 'emitida', ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                user_id,
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
                resultado.get("receptor"),
            ),
        )
        return cur.lastrowid


def guardar_receptor(user_id, doc_tipo, doc_nro, nombre=None, cond_iva=None):
    """Registra/actualiza un receptor reciente del usuario (suma 1 uso)."""
    init_db()
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO receptores (user_id, doc_tipo, doc_nro, nombre, cond_iva, usos, ultimo_uso)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(user_id, doc_tipo, doc_nro) DO UPDATE SET
                usos = usos + 1,
                ultimo_uso = excluded.ultimo_uso,
                nombre = COALESCE(NULLIF(excluded.nombre, ''), receptores.nombre),
                cond_iva = COALESCE(excluded.cond_iva, receptores.cond_iva)
            """,
            (user_id, int(doc_tipo), str(doc_nro), nombre or None, cond_iva, _now()),
        )


def listar_receptores(user_id, limite=8):
    """Receptores del usuario, más usados recientemente primero."""
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT doc_tipo, doc_nro, nombre, cond_iva, usos FROM receptores "
            "WHERE user_id=? ORDER BY ultimo_uso DESC LIMIT ?",
            (user_id, limite),
        ).fetchall()
        return [dict(r) for r in rows]


def registrar_error(user_id, emitido_en, entorno, importe, error) -> int:
    """Guarda un intento que falló (rechazo de ARCA, monto inválido, red, etc.)."""
    init_db()
    with _conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO facturas
                (user_id, emitido_en, entorno, estado, importe, error)
            VALUES (?, ?, ?, 'error', ?, ?)
            """,
            (user_id, emitido_en, entorno, importe, error),
        )
        return cur.lastrowid


def listar(user_id, limite: int = 200):
    """Intentos del usuario, más recientes primero."""
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM facturas WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limite),
        ).fetchall()
        return [dict(r) for r in rows]


def totales(user_id):
    """Resumen por entorno del usuario: emitidas, monto total y errores."""
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT entorno,
                   SUM(estado = 'emitida') AS emitidas,
                   COALESCE(SUM(CASE WHEN estado='emitida' THEN importe END), 0) AS total,
                   SUM(estado = 'error') AS errores
            FROM facturas WHERE user_id=? GROUP BY entorno
            """,
            (user_id,),
        ).fetchall()
        return {
            r["entorno"]: {
                "emitidas": r["emitidas"],
                "total": r["total"],
                "errores": r["errores"],
            }
            for r in rows
        }


# --- Comprobantes leídos de ARCA (web service) ------------------------------


def max_numero_arca(user_id, entorno, punto_venta, cbte_tipo) -> int:
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(numero), 0) m FROM arca_comprobantes "
            "WHERE user_id=? AND entorno=? AND punto_venta=? AND cbte_tipo=?",
            (user_id, entorno, punto_venta, cbte_tipo),
        ).fetchone()
        return row["m"]


def guardar_comprobantes_arca(user_id, filas):
    """`filas` = lista de tuplas (entorno, pv, cbte_tipo, numero, fecha, importe)."""
    if not filas:
        return
    init_db()
    with _conn() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO arca_comprobantes "
            "(user_id, entorno, punto_venta, cbte_tipo, numero, fecha, importe) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(user_id, *f) for f in filas],
        )


def resumen_mensual(user_id, entorno):
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT substr(fecha,1,6) mes, COUNT(*) cant, SUM(importe) total "
            "FROM arca_comprobantes WHERE user_id=? AND entorno=? "
            "GROUP BY mes ORDER BY mes DESC",
            (user_id, entorno),
        ).fetchall()
        return [dict(r) for r in rows]


def acumulado_desde(user_id, entorno, fecha_desde):
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(importe),0) total, COUNT(*) cant "
            "FROM arca_comprobantes WHERE user_id=? AND entorno=? AND fecha>=?",
            (user_id, entorno, fecha_desde),
        ).fetchone()
        return {"total": row["total"], "cantidad": row["cant"]}


# --- Import del CSV de Mis Comprobantes -------------------------------------


def importar_mis_comprobantes(user_id, contenido: bytes, entorno: str):
    """
    Parsea el ZIP/CSV de 'Mis Comprobantes' (emitidos) del usuario y lo guarda.
    Matchea columnas por nombre, detecta separador, y suma con signo (NC restan).
    """
    init_db()

    if contenido[:2] == b"PK":  # ZIP de AFIP
        try:
            with zipfile.ZipFile(io.BytesIO(contenido)) as z:
                csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
                if not csvs:
                    return {"error": "El ZIP no contiene ningún archivo .csv."}
                contenido = z.read(csvs[0])
        except zipfile.BadZipFile:
            return {"error": "El archivo parece un ZIP pero está dañado."}

    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            texto = contenido.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return {"error": "No pude leer el archivo (codificación desconocida)."}

    primera = texto.splitlines()[0] if texto.strip() else ""
    sep = ";" if primera.count(";") >= primera.count(",") else ","
    filas = list(csv.reader(io.StringIO(texto), delimiter=sep))
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
        if not fecha:
            ignoradas += 1
            continue
        tipo = fila[i_tipo].strip() if i_tipo is not None else "Comprobante"
        pv = int(_parse_num(fila[i_pv])) if i_pv is not None else 0
        numero = int(_parse_num(fila[i_num])) if i_num is not None else leidas + 1
        importe = _parse_num(fila[i_imp])
        if "credito" in _norm(tipo):
            importe = -abs(importe)
        registros.append((user_id, entorno, pv, tipo, numero, fecha, importe))
        leidas += 1

    if registros:
        with _conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO mis_comprobantes "
                "(user_id, entorno, punto_venta, tipo, numero, fecha, importe) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                registros,
            )

    fechas = [r[5] for r in registros]
    return {
        "importados": leidas,
        "ignoradas": ignoradas,
        "desde": min(fechas) if fechas else None,
        "hasta": max(fechas) if fechas else None,
    }


def info_mis(user_id, entorno):
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) cant, MIN(fecha) desde, MAX(fecha) hasta, "
            "COUNT(DISTINCT punto_venta) pvs FROM mis_comprobantes "
            "WHERE user_id=? AND entorno=?",
            (user_id, entorno),
        ).fetchone()
        if not row["cant"]:
            return None
        return {"cant": row["cant"], "desde": row["desde"], "hasta": row["hasta"], "pvs": row["pvs"]}


def resumen_detallado(user_id, entorno, fecha_movil):
    """
    Unifica facturas emitidas por la app ('facturador') con el CSV de Mis
    Comprobantes ('importada') del usuario, sin doble conteo, agrupado por mes,
    más el acumulado móvil de 12 meses.
    """
    init_db()
    with _conn() as conn:
        app_rows = conn.execute(
            "SELECT punto_venta, numero, fecha, importe FROM facturas "
            "WHERE user_id=? AND entorno=? AND estado='emitida'",
            (user_id, entorno),
        ).fetchall()
        mis_rows = conn.execute(
            "SELECT punto_venta, numero, fecha, importe, tipo FROM mis_comprobantes "
            "WHERE user_id=? AND entorno=?",
            (user_id, entorno),
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
            continue
        comprobantes.append(
            {
                "fecha": r["fecha"], "tipo": r["tipo"], "punto_venta": r["punto_venta"],
                "numero": r["numero"], "importe": r["importe"], "origen": "importada",
            }
        )

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
