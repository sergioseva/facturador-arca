"""
Topes de ingresos brutos anuales del Monotributo, por categoría.

Fuente: https://www.afip.gob.ar/monotributo/categorias.asp
ARCA actualiza estos montos cada ~6 meses. Cuando cambien, actualizá este
diccionario (y VIGENTE_DESDE) con los valores nuevos de esa página.
"""

import re

import requests

VIGENTE_DESDE = "01/02/2026"
FUENTE = "https://www.afip.gob.ar/monotributo/categorias.asp"

CATS = list("ABCDEFGHIJK")

# categoría -> ingresos brutos anuales máximos (ARS)
TOPES = {
    "A": 10_277_988.13,
    "B": 15_058_447.71,
    "C": 21_113_696.52,
    "D": 26_212_853.42,
    "E": 30_833_964.37,
    "F": 38_642_048.36,
    "G": 46_211_109.37,
    "H": 70_113_407.33,
    "I": 78_479_211.62,
    "J": 89_872_640.30,
    "K": 108_357_084.05,
}


def categoria_para(total, topes=None):
    """
    Devuelve la categoría mínima cuyo tope alcanza `total`, o None si lo supera
    incluso a la categoría más alta (quedaría excluido del monotributo).
    """
    topes = topes or TOPES
    for cat in topes:  # dict ordenado A..K
        if total <= topes[cat]:
            return cat
    return None


def fetch_topes_arca(timeout=20):
    """
    Descarga la tabla de categorías de ARCA y devuelve (topes, vigente_desde).
    Parsea la columna 'Ingresos brutos' (la primera de cada fila) y valida que
    sea coherente. Lanza una excepción si algo no cierra (para no pisar los
    valores buenos con basura si ARCA cambia la página).
    """
    r = requests.get(FUENTE, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    html = r.text

    montos = re.findall(r"\$\s*([0-9][0-9.]*,[0-9]{2})", html)
    if not montos or len(montos) % len(CATS) != 0:
        raise ValueError(f"No pude leer la tabla (encontré {len(montos)} importes).")

    cols = len(montos) // len(CATS)  # ingresos brutos = primera columna de cada fila

    def _num(s):
        return float(s.replace(".", "").replace(",", "."))

    topes = {CATS[i]: _num(montos[i * cols]) for i in range(len(CATS))}

    valores = list(topes.values())
    if not all(valores[i] < valores[i + 1] for i in range(len(valores) - 1)):
        raise ValueError("Los topes no quedaron crecientes; la página pudo cambiar de formato.")
    if valores[0] < 1_000_000:
        raise ValueError("Los valores no parecen ingresos brutos; revisá la página de ARCA.")

    m = re.search(r"desde el\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})", html)
    vigente = m.group(1) if m else VIGENTE_DESDE
    return topes, vigente
