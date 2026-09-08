"""
Topes de ingresos brutos anuales del Monotributo, por categoría.

Fuente: https://www.afip.gob.ar/monotributo/categorias.asp
ARCA actualiza estos montos cada ~6 meses. Cuando cambien, actualizá este
diccionario (y VIGENTE_DESDE) con los valores nuevos de esa página.
"""

import re

import requests

VIGENTE_DESDE = "01/08/2026"
FUENTE = "https://www.afip.gob.ar/monotributo/categorias.asp"

CATS = list("ABCDEFGHIJK")

# categoría -> ingresos brutos anuales máximos (ARS)
TOPES = {
    "A": 12_009_410.45,
    "B": 17_595_182.74,
    "C": 24_670_494.31,
    "D": 30_628_651.43,
    "E": 36_028_231.33,
    "F": 45_151_659.41,
    "G": 53_995_798.87,
    "H": 81_924_660.37,
    "I": 91_699_761.90,
    "J": 105_012_519.20,
    "K": 126_610_838.75,
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
    if m:
        d, mes, anio = m.group(1).split("/")
        vigente = f"{int(d):02d}/{int(mes):02d}/{anio}"
    else:
        vigente = VIGENTE_DESDE
    return topes, vigente
