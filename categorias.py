"""
Topes de ingresos brutos anuales del Monotributo, por categoría.

Fuente: https://www.afip.gob.ar/monotributo/categorias.asp
ARCA actualiza estos montos cada ~6 meses. Cuando cambien, actualizá este
diccionario (y VIGENTE_DESDE) con los valores nuevos de esa página.
"""

VIGENTE_DESDE = "01/02/2026"
FUENTE = "https://www.afip.gob.ar/monotributo/categorias.asp"

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


def categoria_para(total):
    """
    Devuelve la categoría mínima cuyo tope alcanza `total`, o None si lo supera
    incluso a la categoría más alta (quedaría excluido del monotributo).
    """
    for cat in TOPES:  # dict ordenado A..K
        if total <= TOPES[cat]:
            return cat
    return None
