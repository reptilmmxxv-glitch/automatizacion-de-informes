# -*- coding: utf-8 -*-
"""
================================================================================
 EcoGestor Local  -  v1.0
 Aplicacion web OFFLINE y PORTABLE para Matron Ecografista (Chile)
================================================================================

 ARQUITECTURA (100% portable: se ejecuta desde un pendrive)

    <PENDRIVE>/
        app.py                  <- Este archivo. Backend Flask + SQLite + Word + Excel
        requirements.txt        <- Dependencias (pip install -r requirements.txt)
        database.db             <- Base de datos SQLite local (se crea sola)
        templates/
            index.html          <- Interfaz unica (Bootstrap 5 vendorizado, sin CDN)
        static/
            vendor/             <- bootstrap.min.css / bootstrap.bundle.min.js (offline)
            css/app.css         <- Estilos propios de la aplicacion
            js/app.js           <- Logica del formulario, macros, clipboard
        word_templates/         <- Plantillas institucionales .docx con marcadores {{variable}}
        outputs/                <- Informes .docx generados por paciente
        tools/
            crear_plantillas.py <- Generador de las plantillas .docx (ejecutar 1 vez)

 REGLAS DE NEGOCIO CRITICAS (separacion estricta)
 ---------------------------------------------------------------------------
 * DATO CLINICO   -> Word + portapapeles (ABIS).  Campos {{clinico_*}}
 * DATO ESTADISTICO-> Solo SQLite. NUNCA se imprime.
                      - clasificacion (1T/2T/3T/DIU/Climaterio/Ginecologica)
                      - es_doppler (Si/No) -> vital para el cobro mensual
 ---------------------------------------------------------------------------

 EJECUCION:  python app.py   ->  http://127.0.0.1:5000
================================================================================
"""

from __future__ import annotations

import io
import os
import re
import sqlite3
import unicodedata
from copy import deepcopy
from datetime import datetime, date
from functools import wraps

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    session,
    send_from_directory,
    send_file,
    g,
)

# ==============================================================================
# 1) CONFIGURACION Y RUTAS PORTABLES
# ==============================================================================

# Carpeta raiz de la aplicacion (donde vive app.py). Todo es relativo a esta,
# de modo que la aplicacion funciona desde cualquier pendrive (D:\, E:\, etc.).
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
WORD_TEMPLATES_DIR = os.path.join(BASE_DIR, "word_templates")
OUTPUTS_DIR = os.path.join(BASE_DIR, "outputs")
TOOLS_DIR = os.path.join(BASE_DIR, "tools")
DB_PATH = os.path.join(BASE_DIR, "database.db")

for _d in (TEMPLATES_DIR, STATIC_DIR, WORD_TEMPLATES_DIR, OUTPUTS_DIR):
    os.makedirs(_d, exist_ok=True)

app = Flask(__name__, template_folder=TEMPLATES_DIR, static_folder=STATIC_DIR)

# --- Clave secreta persistente en el propio pendrive (para la sesion) --------
_SECRET_FILE = os.path.join(BASE_DIR, ".secret_key")


def _cargar_o_crear_secret_key() -> str:
    """Crea una clave aleatoria una sola vez y la guarda en el pendrive."""
    if os.path.exists(_SECRET_FILE):
        with open(_SECRET_FILE, "r", encoding="utf-8") as fh:
            data = fh.read().strip()
            if data:
                return data
    key = os.urandom(32).hex()
    with open(_SECRET_FILE, "w", encoding="utf-8") as fh:
        fh.write(key)
    return key


app.secret_key = _cargar_o_crear_secret_key()
app.config["JSON_AS_ASCII"] = False
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# --- Constantes de negocio ---------------------------------------------------
CENTROS = {
    "Nogales": "Departamento de Salud Nogales",
    "Melon": "Departamento de Salud Melón",
}

# Etiquetas visibles -> clave interna en la base de datos
CENTROS_UI = {
    "Nogales": "Nogales",
    "Melón": "Melon",
}

CLASIFICACIONES = [
    "1er Trimestre",
    "2do Trimestre",
    "3er Trimestre",
    "DIU",
    "Climaterio",
    "Ginecológica",
]

TIPOS_INFORME = [
    {"valor": "ginecologico", "label": "Ginecológico"},
    {"valor": "eco_precoz", "label": "Eco Precoz"},
    {"valor": "screening_11_14", "label": "Screening 11-14"},
    {"valor": "obstetrica_mas_14", "label": "Obstétrica >14 sem"},
    {"valor": "eco_obs_2t_sin_doppler", "label": "Eco Obs 2T sin Doppler"},
    {"valor": "gemelar_precoz", "label": "Gemelar Precoz"},
    {"valor": "gemelar_2t_doppler", "label": "Gemelar 2T Doppler"},
]

TIPO_INFORME_MAP = {t["valor"]: t["label"] for t in TIPOS_INFORME}

# Campos clinicos (van al Word y al portapapeles ABIS)
CAMPOS_CLINICOS = [
    "nombre",
    "run",
    "edad",
    "fur",
    "fecha_exploracion",
    "profesional_solicitante",
    "diagnostico_referencia",
    "informacion_tecnica",
    "exploracion",
    "biometria",
    "impresion_ecografica",
    "sugerencias",
    "centro",
]

# Campos estadisticos (SOLO SQLite, nunca impresos)
CAMPOS_ESTADISTICOS = ["clasificacion", "es_doppler"]

# ---------------------------------------------------------------------------
# Esquema de base de datos
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- PACIENTES: una fila por persona (clave = RUN,_RUNK)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pacientes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run               TEXT    NOT NULL UNIQUE,          -- RUN normalizado (solo digitos y K)
    nombre            TEXT    NOT NULL,
    fecha_nacimiento  TEXT,                            -- ISO: YYYY-MM-DD (opcional)
    telefono          TEXT,
    centro_creacion   TEXT,                            -- centro donde se creo el registro
    creado_en         TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    actualizado_en    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------------------------------------------------------------------
-- ATENCIONES: una fila por ecografia realizada (clinico + estadistico)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS atenciones (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    paciente_id             INTEGER NOT NULL REFERENCES pacientes(id) ON DELETE CASCADE,
    centro                  TEXT    NOT NULL,          -- 'Nogales' | 'Melon'
    fecha_exploracion       TEXT    NOT NULL,          -- ISO: YYYY-MM-DD
    fur                     TEXT,                      -- fecha ultima menstrual (ISO o texto)
    edad                    INTEGER,
    -- ---- DATOS CLINICOS (imprimibles) ----
    profesional_solicitante TEXT,
    diagnostico_referencia  TEXT,
    informacion_tecnica     TEXT,
    exploracion             TEXT,
    biometria               TEXT,
    impresion_ecografica    TEXT,
    sugerencias             TEXT,
    texto_abis              TEXT,                      -- copia de lo enviado al portapapeles
    archivo_docx            TEXT,                      -- nombre del archivo generado
    -- ---- DATOS ESTADISTICOS (NUNCA se imprimen) ----
    clasificacion           TEXT    NOT NULL,          -- 1T|2T|3T|DIU|Climaterio|Ginecologica
    es_doppler              INTEGER NOT NULL DEFAULT 0,-- 1 = Si (SIEMPRE numerico 0/1)
    tipo_informe            TEXT    NOT NULL,          -- slug del tipo de informe
    creado_en               TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (paciente_id) REFERENCES pacientes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_atenciones_fecha    ON atenciones(fecha_exploracion);
CREATE INDEX IF NOT EXISTS idx_atenciones_centro   ON atenciones(centro, fecha_exploracion);
CREATE INDEX IF NOT EXISTS idx_atenciones_paciente ON atenciones(paciente_id, fecha_exploracion DESC);
CREATE INDEX IF NOT EXISTS idx_atenciones_clasif   ON atenciones(clasificacion);

-- ---------------------------------------------------------------------
-- MACROS: textos clinicos rapidos, editables desde la interfaz
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS macros (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre       TEXT    NOT NULL,           -- etiqueta visible del boton
    texto        TEXT    NOT NULL,           -- texto que se inserta
    campo_destino TEXT   NOT NULL,           -- campo destino en el formulario
    orden        INTEGER NOT NULL DEFAULT 0,
    activo       INTEGER NOT NULL DEFAULT 1,
    creado_en    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_macros_campo ON macros(campo_destino, orden);
"""

# Macros precargadas con los ejemplos clinicos solicitados
MACROS_INICIALES = [
    # --- EXPLORACION ---
    ("Ovarios poliquísticos", "exploracion",
     "Ovarios poliquísticos: >12 folículos y/o volumen ovárico >10 ml."),
    ("DIU normainserto", "exploracion",
     "DIU normoinserto a X mm del fondo uterino."),
    ("DIU descendido", "exploracion",
     "DIU descendido."),
    ("Mioma intramural", "exploracion",
     "Mioma intramural."),
    ("Mioma subseroso", "exploracion",
     "Mioma subseroso."),
    ("Mioma submucoso", "exploracion",
     "Mioma submucoso."),
    ("Útero en anteversión", "exploracion",
     "Útero en anteversión."),
    ("Útero en retroversión", "exploracion",
     "Útero en retroversión."),
    ("Ovarios de aspecto normal", "exploracion",
     "Ovarios de aspecto normal."),
    ("Endometrio proliferativo", "exploracion",
     "Endometrio proliferativo."),
    ("Endometrio en SECRETION", "exploracion",
     "Endometrio en fase secretora."),
    ("Cérvix sin alteraciones", "exploracion",
     "Cérvix sin alteraciones Evans."),
    ("Saco gestacional", "exploracion",
     "Saco gestacional intrauterino."),
    ("Feto único", "exploracion",
     "Gestación única."),
    ("Feto viable", "exploracion",
     "Feto viable con actividad cardiaca presente."),
    ("Sin alteraciones", "exploracion",
     "Sin alteraciones ecográficas."),
    ("Dolor pélvico", "exploracion",
     "Dolor pélvico de predominio inferior."),
    ("Hallazgo incidental", "exploracion",
     "Hallazgo incidental."),

    # --- BIOMETRIA ---
    ("EGB 10x6x7 mm", "biometria",
     "EGB: 10 x 6 x 7 mm."),
    ("EGB 15x9x10 mm", "biometria",
     "EGB: 15 x 9 x 10 mm."),
    ("LCCFET 30 mm", "biometria",
     "LCC-FET: 30 mm."),
    ("EVA 8 cm", "biometria",
     "EVA: 8 cm."),
    ("DP 45 mm", "biometria",
     "Diámetro biparietal (DP): 45 mm."),
    ("CA 190 mm", "biometria",
     "Circunferencia abdominal (CA): 190 mm."),
    ("Longitud femur 32 mm", "biometria",
     "Longitud femoral: 32 mm."),
    ("Peso estimado 2400 g", "biometria",
     "Peso estimado: 2400 g."),
    ("Cartero de 16 semanas", "biometria",
     "Corresponde a cartografía de 16 semanas."),
    ("Doppler color presente", "biometria",
     "Doppler color presente con flujo vascular fetoplacentario."),

    # --- IMPRESION ECOGRAFICA ---
    ("Gestación normal", "impresion_ecografica",
     "Gestación evolutiva normal, única, de edad ecográfica concordante con FUR."),
    ("Embarazo no viable", "impresion_ecografica",
     "Gestación no viable."),
    ("Embarazo ectopico", "impresion_ecografica",
     "Sospecha de embarazo ectopico."),
    ("Oligoamnios", "impresion_ecografica",
     "Oligoamnios."),
    ("Polihidramnios", "impresion_ecografica",
     "Polihidramnios."),
    ("Restricción del crecimiento", "impresion_ecografica",
     "Restricción del crecimiento fetal."),
    ("Pre-eclampsia", "impresion_ecografica",
     "Signos de pre-eclampsia."),
    ("Miomatosis uterina", "impresion_ecografica",
     "Miomatosis uterina."),
    ("Endometriosis", "impresion_ecografica",
     "Endometriosis pélvica."),
    ("Quiste ovárico simple", "impresion_ecografica",
     "Quiste ovárico simple."),
    ("Quiste ovárico complejo", "impresion_ecografica",
     "Quiste ovárico de aspecto complejo."),
    ("Pólipos endometriales", "impresion_ecografica",
     "Pólipos endometriales."),
    ("DIU permeable", "impresion_ecografica",
     "DIU permeable y en posición."),

    # --- SUGERENCIAS ---
    ("Control en 4 semanas", "sugerencias",
     "Control ecográfico en 4 semanas."),
    ("Control en 2 semanas", "sugerencias",
     "Control ecográfico en 2 semanas."),
    ("Derivar a medicina fetal", "sugerencias",
     "Derivar a Medicina Fetal."),
    ("Derivar a alto riesgo", "sugerencias",
     "Derivar a Alto Riesgo."),
    ("Correlacionar con examen clinico", "sugerencias",
     "Correlacionar hallazgos con examen clínico."),

    # --- INFORMACION TECNICA ---
    ("Transvaginal", "informacion_tecnica",
     "Ecografía transvaginal."),
    ("Obstétrica transabdominal", "informacion_tecnica",
     "Ecografía obstétrica transabdominal."),
    ("Transabdominal y transvaginal", "informacion_tecnica",
     "Ecografía transabdominal y transvaginal."),
    ("Translabial", "informacion_tecnica",
     "Ecografía translabial."),
    ("Con Doppler color", "informacion_tecnica",
     "Estudio con Doppler color pulsado."),
]

# Quitamos cualquier macro de ejemplo mal formada (debe quedar solo texto real)
MACROS_INICIALES = [m for m in MACROS_INICIALES if m[2].strip()]


# ==============================================================================
# 2) CAPA DE BASE DE DATOS (SQLite)
# ==============================================================================

def get_db() -> sqlite3.Connection:
    """Conexion SQLite por request (patron Flask recomendado)."""
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    """Cierra la conexion al terminar cada peticion."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    """Crea el esquema y carga las macros precargadas (solo si la tabla esta vacia)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA_SQL)
        n = conn.execute("SELECT COUNT(*) AS c FROM macros").fetchone()["c"]
        if n == 0:
            for orden, (nombre, campo, texto) in enumerate(MACROS_INICIALES):
                conn.execute(
                    "INSERT INTO macros (nombre, texto, campo_destino, orden, activo) "
                    "VALUES (?,?,?,?,1)",
                    (nombre, texto, campo, orden),
                )
            conn.commit()
            print(f"[EcoGestor] {len(MACROS_INICIALES)} macros clinicas precargadas.")
    finally:
        conn.close()


# ==============================================================================
# 3) UTILIDADES
# ==============================================================================

def normalizar_run(run: str) -> str:
    """'12.345.678-9' -> '123456789' ; '12.345.678-K' -> '12345678K'."""
    if not run:
        return ""
    run = run.upper().replace(".", "").replace("-", "").replace(" ", "")
    return run


def formatear_run(run: str) -> str:
    """'123456789' -> '12.345.678-9' ; '12345678K' -> '12.345.678-K' (formato chileno)."""
    run = normalizar_run(run)
    if not run:
        return ""
    cuerpo, dv = run[:-1], run[-1]
    if not cuerpo:
        return run
    grupos = []
    while len(cuerpo) > 3:
        grupos.insert(0, cuerpo[-3:])
        cuerpo = cuerpo[:-3]
    grupos.insert(0, cuerpo)
    return ".".join(grupos) + "-" + dv


def limpiar_texto(valor) -> str:
    """Normaliza texto de entrada: recorta y colapsa espacios/newlines triples."""
    if valor is None:
        return ""
    texto = str(valor).replace("\r\n", "\n").replace("\r", "\n")
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def slugify(texto: str) -> str:
    """Convierte un texto en un nombre de archivo seguro (sin acentos, sin espacios)."""
    texto = unicodedata.normalize("NFKD", str(texto))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^A-Za-z0-9]+", "_", texto).strip("_")
    return texto or "sin_nombre"


def calcular_edad(fecha_nacimiento: str, fecha_ref: str = None) -> int | None:
    """Edad cumplida en anos a la fecha de exploracion (o hoy si no se indica)."""
    if not fecha_nacimiento:
        return None
    try:
        nac = datetime.strptime(str(fecha_nacimiento)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    if fecha_ref:
        try:
            ref = datetime.strptime(str(fecha_ref)[:10], "%Y-%m-%d").date()
        except ValueError:
            ref = date.today()
    else:
        ref = date.today()
    edad = ref.year - nac.year - ((ref.month, ref.day) < (nac.month, nac.day))
    return max(edad, 0)


def formatear_fecha_es(iso: str) -> str:
    """'2026-11-16' -> '16/11/2026' (formato chileno)."""
    if not iso:
        return ""
    try:
        return datetime.strptime(str(iso)[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return str(iso)


def centro_activo() -> str | None:
    """Centro elegido en la sesion (None si el usuario aun no elige)."""
    return session.get("centro")


def centro_ui(clave: str | None) -> str:
    """Convierte la clave interna ('Melon') en la etiqueta visible ('Melón')."""
    if not clave:
        return ""
    for ui, interna in CENTROS_UI.items():
        if interna == clave:
            return ui
    return clave


def requiere_centro(fn):
    """Decorador: bloquea cualquier peticion que no tenga centro activo."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not centro_activo():
            return jsonify({
                "ok": False,
                "error": "Debe seleccionar el centro de salud activo (Nogales o Melón).",
                "codigo": "SIN_CENTRO",
            }), 409
        return fn(*args, **kwargs)
    return wrapper


# ==============================================================================
# 4) MANIPULACION DE WORD (python-docx)
# ==============================================================================

PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_\-]+)\s*\}\}")


def reemplazar_placeholders(texto: str, mapa: dict) -> str:
    """Sustituye {{variable}} por su valor. Las variables desconocidas quedan vacias."""
    def _sub(match):
        clave = match.group(1)
        valor = mapa.get(clave, "")
        return "" if valor is None else str(valor)
    return PLACEHOLDER_RE.sub(_sub, texto)


def _reemplazar_en_parrafo(parrafo, mapa: dict) -> bool:
    """
    Reemplaza los marcadores de un parrafo conservando el formato del primer run.
    Si el parrafo es un unico marcador con texto multilinea, se expande en varios
    parrafos heredando el estilo original.
    """
    if not parrafo.runs:
        return False

    texto_completo = "".join(r.text for r in parrafo.runs)
    if "{{" not in texto_completo:
        return False

    # --- Caso especial: parrafo = un solo marcador con saltos de linea --------
    matches = PLACEHOLDER_RE.findall(texto_completo)
    if len(matches) == 1 and texto_completo.strip() == "{{%s}}" % matches[0]:
        valor = mapa.get(matches[0], "")
        lineas = [l for l in str(valor).split("\n")] if valor != "" else [""]
        # Escribimos la primera linea en el parrafo actual
        _escribir_en_parrafo(parrafo, lineas[0])
        # Clonamos el parrafo para las lineas adicionales
        p_element = parrafo._p
        for linea in lineas[1:]:
            nuevo_p = deepcopy(p_element)
            p_element.addnext(nuevo_p)
            # Escribimos la nueva linea en el parrafo clonado
            from docx.text.paragraph import Paragraph
            nuevo_parrafo = Paragraph(nuevo_p, parrafo._parent)
            _escribir_en_parrafo(nuevo_parrafo, linea)
            p_element = nuevo_p
        return True

    texto_nuevo = reemplazar_placeholders(texto_completo, mapa)
    if texto_nuevo == texto_completo:
        return False
    _escribir_en_parrafo(parrafo, texto_nuevo)
    return True


def _escribir_en_parrafo(parrafo, texto: str) -> None:
    """Vuelca el texto en el primer run (conserva formato) y limpia los demas."""
    if not parrafo.runs:
        parrafo.add_run(texto)
        return
    parrafo.runs[0].text = texto
    for r in parrafo.runs[1:]:
        r.text = ""


def _recorrer_celdas(tabla, mapa: dict) -> None:
    for fila in tabla.rows:
        for celda in fila.cells:
            for p in celda.paragraphs:
                _reemplazar_en_parrafo(p, mapa)
            for t in celda.tables:  # tablas anidadas
                _recorrer_celdas(t, mapa)


def rellenar_docx(ruta_plantilla: str, mapa: dict, ruta_destino: str) -> None:
    """
    Rellena una plantilla .docx conservando intactas tablas, estilos y formatos.
    Recorre: cuerpo, tablas, encabezados y pies de pagina.
    """
    from docx import Document

    doc = Document(ruta_plantilla)

    # 1) Parrafos del cuerpo (incluye los que estan dentro de tablas)
    for p in doc.paragraphs:
        _reemplazar_en_parrafo(p, mapa)

    # 2) Tablas
    for tabla in doc.tables:
        _recorrer_celdas(tabla, mapa)

    # 3) Encabezados y pies de pagina (por seccion)
    for seccion in doc.sections:
        for contenedor in (seccion.header, seccion.footer):
            for p in contenedor.paragraphs:
                _reemplazar_en_parrafo(p, mapa)
            for tabla in contenedor.tables:
                _recorrer_celdas(tabla, mapa)

    doc.save(ruta_destino)


# ==============================================================================
# 5) TEXTO PARA EL PORTAPAPELES (Ficha ABIS)
# ==============================================================================

def construir_texto_abis(datos: dict) -> str:
    """
    Arma el texto clinico estructurado listo para Ctrl+V en la ficha ABIS.
    IMPORTANTE: no incluye clasificacion ni es_doppler (datos estadisticos).
    """
    def bloque(titulo, valor):
        valor = limpiar_texto(valor)
        if not valor:
            return ""
        return f"{titulo}:\n{valor}\n"

    # Biometria: texto libre + marcadores obstétricos opcionales (dato clínico)
    bio = limpiar_texto(datos.get("biometria"))
    extras_bio = []
    if datos.get("edad_gestacional"):
        extras_bio.append(f"Edad gestacional: {datos['edad_gestacional']}")
    if datos.get("translucencia_nucal"):
        extras_bio.append(f"Translucencia nucal (TN): {datos['translucencia_nucal']}")
    biometria_texto = "\n".join([p for p in ([bio] + extras_bio) if p])

    partes = []
    partes.append("=== INFORME ECOGRÁFICO ===")
    partes.append(f"Centro: {datos.get('centro', '')}")
    partes.append(f"Paciente: {datos.get('nombre', '')}")
    partes.append(f"RUN: {datos.get('run', '')}")
    partes.append(f"Edad: {datos.get('edad', '')} años")
    if datos.get("fur"):
        partes.append(f"FUR: {datos.get('fur')}")
    partes.append(f"Fecha de exploración: {datos.get('fecha_exploracion', '')}")
    partes.append(f"Profesional solicitante: {datos.get('profesional_solicitante', '')}")
    partes.append(f"Diagnóstico de referencia: {datos.get('diagnostico_referencia', '')}")
    partes.append("")
    partes.append(bloque("INFORMACIÓN TÉCNICA", datos.get("informacion_tecnica")))
    partes.append(bloque("EXPLORACIÓN", datos.get("exploracion")))
    partes.append(bloque("BIOMETRÍA", biometria_texto))
    partes.append(bloque("IMPRESIÓN ECOGRÁFICA", datos.get("impresion_ecografica")))
    partes.append(bloque("SUGERENCIAS", datos.get("sugerencias")))

    texto = "\n".join(partes)
    # Limpieza final: sin lineas en blanco duplicadas
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


# ==============================================================================
# 6) RUTAS DE LA APLICACION
# ==============================================================================

@app.route("/")
def index():
    """Interfaz unica de la aplicacion."""
    return render_template(
        "index.html",
        centros=CENTROS_UI,
        clasificaciones=CLASIFICACIONES,
        tipos_informe=TIPOS_INFORME,
        centro_activo=centro_activo(),
    )


# ---------------------------------------------------------------- CONTEXTO ---
@app.route("/api/contexto", methods=["GET"])
def api_contexto():
    """Devuelve el estado del contexto activo (centro) y catalogos."""
    return jsonify({
        "ok": True,
        "centro": centro_activo(),
        "centro_ui": centro_ui(centro_activo()),
        "centro_nombre": CENTROS.get(centro_activo(), ""),
        "catalogos": {
            "centros": CENTROS_UI,
            "clasificaciones": CLASIFICACIONES,
            "tipos_informe": TIPOS_INFORME,
        },
    })


@app.route("/api/contexto", methods=["POST"])
def api_set_contexto():
    """Selecciona (o cambia) el centro de salud activo. Es OBLIGATORIO."""
    data = request.get_json(silent=True) or {}
    clave = (data.get("centro") or "").strip()
    # Acepta tanto la clave interna ('Melon') como la etiqueta visible ('Melón')
    if clave in CENTROS_UI:
        clave = CENTROS_UI[clave]
    if clave not in CENTROS:
        return jsonify({
            "ok": False,
            "error": "Centro no válido. Debe ser 'Nogales' o 'Melón'.",
        }), 400
    session["centro"] = clave
    return jsonify({
        "ok": True,
        "centro": clave,
        "centro_ui": centro_ui(clave),
        "centro_nombre": CENTROS[clave],
    })


# --------------------------------------------------------------- PACIENTES ---
@app.route("/api/pacientes", methods=["GET"])
def api_buscar_pacientes():
    """Busqueda de pacientes recurrentes por RUN (o por nombre)."""
    q = (request.args.get("q") or "").strip()
    db = get_db()
    if not q:
        return jsonify({"ok": True, "pacientes": []})

    run_norm = normalizar_run(q)
    if run_norm:
        filas = db.execute(
            "SELECT * FROM pacientes WHERE run = ? OR run LIKE ? OR nombre LIKE ? "
            "ORDER BY nombre COLLATE NOCASE LIMIT 20",
            (run_norm, f"%{run_norm}%", f"%{q}%"),
        ).fetchall()
    else:
        filas = db.execute(
            "SELECT * FROM pacientes WHERE nombre LIKE ? ORDER BY nombre COLLATE NOCASE LIMIT 20",
            (f"%{q}%",),
        ).fetchall()

    pacientes = []
    for f in filas:
        ult = db.execute(
            "SELECT * FROM atenciones WHERE paciente_id = ? "
            "ORDER BY fecha_exploracion DESC, id DESC LIMIT 1",
            (f["id"],),
        ).fetchone()
        pacientes.append({
            "id": f["id"],
            "run": f["run"],
            "run_formateado": formatear_run(f["run"]),
            "nombre": f["nombre"],
            "fecha_nacimiento": f["fecha_nacimiento"],
            "telefono": f["telefono"],
            "centro_creacion": f["centro_creacion"],
            "ultimo_informe": dict(ult) if ult else None,
        })
    return jsonify({"ok": True, "pacientes": pacientes})


@app.route("/api/paciente/<run>", methods=["GET"])
def api_paciente_por_run(run):
    """Detalle de una paciente por RUN + historial de atenciones."""
    db = get_db()
    run_norm = normalizar_run(run)
    f = db.execute("SELECT * FROM pacientes WHERE run = ?", (run_norm,)).fetchone()
    if not f:
        return jsonify({"ok": False, "error": "Paciente no encontrada."}), 404

    atenciones = db.execute(
        "SELECT * FROM atenciones WHERE paciente_id = ? "
        "ORDER BY fecha_exploracion DESC, id DESC LIMIT 20",
        (f["id"],),
    ).fetchall()

    return jsonify({
        "ok": True,
        "paciente": {
            "id": f["id"],
            "run": f["run"],
            "run_formateado": formatear_run(f["run"]),
            "nombre": f["nombre"],
            "fecha_nacimiento": f["fecha_nacimiento"],
            "telefono": f["telefono"],
        },
        "atenciones": [dict(a) for a in atenciones],
    })


# ------------------------------------------------------------------ MACROS ---
@app.route("/api/macros", methods=["GET"])
def api_listar_macros():
    """Lista las macros clinicas, agrupadas por campo destino."""
    db = get_db()
    filas = db.execute(
        "SELECT * FROM macros WHERE activo = 1 ORDER BY campo_destino, orden, id"
    ).fetchall()
    macros = [dict(m) for m in filas]
    return jsonify({"ok": True, "macros": macros})


@app.route("/api/macros", methods=["POST"])
def api_crear_macro():
    """Crea una nueva macro clinica (editable desde la interfaz)."""
    data = request.get_json(silent=True) or {}
    nombre = limpiar_texto(data.get("nombre"))
    texto = limpiar_texto(data.get("texto"))
    campo = (data.get("campo_destino") or "").strip()
    if not nombre or not texto or not campo:
        return jsonify({"ok": False, "error": "Nombre, texto y campo destino son obligatorios."}), 400

    db = get_db()
    orden = db.execute(
        "SELECT COALESCE(MAX(orden), 0) + 1 FROM macros WHERE campo_destino = ?", (campo,)
    ).fetchone()[0]
    cur = db.execute(
        "INSERT INTO macros (nombre, texto, campo_destino, orden, activo) VALUES (?,?,?,?,1)",
        (nombre, texto, campo, orden),
    )
    db.commit()
    nuevo = db.execute("SELECT * FROM macros WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify({"ok": True, "macro": dict(nuevo)})


@app.route("/api/macros/<int:macro_id>", methods=["PUT"])
def api_actualizar_macro(macro_id: int):
    """Edita una macro existente."""
    data = request.get_json(silent=True) or {}
    db = get_db()
    actual = db.execute("SELECT * FROM macros WHERE id = ?", (macro_id,)).fetchone()
    if not actual:
        return jsonify({"ok": False, "error": "Macro no encontrada."}), 404

    nombre = limpiar_texto(data.get("nombre", actual["nombre"]))
    texto = limpiar_texto(data.get("texto", actual["texto"]))
    campo = (data.get("campo_destino") or actual["campo_destino"]).strip()
    activo = 1 if data.get("activo", bool(actual["activo"])) else 0

    db.execute(
        "UPDATE macros SET nombre = ?, texto = ?, campo_destino = ?, activo = ? WHERE id = ?",
        (nombre, texto, campo, activo, macro_id),
    )
    db.commit()
    nuevo = db.execute("SELECT * FROM macros WHERE id = ?", (macro_id,)).fetchone()
    return jsonify({"ok": True, "macro": dict(nuevo)})


@app.route("/api/macros/<int:macro_id>", methods=["DELETE"])
def api_eliminar_macro(macro_id: int):
    """Elimina (desactiva) una macro."""
    db = get_db()
    db.execute("UPDATE macros SET activo = 0 WHERE id = ?", (macro_id,))
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- INFORMES ---
@app.route("/api/informes", methods=["POST"])
@requiere_centro
def api_crear_informe():
    """
    Genera el informe completo en un solo clic:
      1) Guarda el registro en SQLite (clinico + estadistico).
      2) Rellena la plantilla .docx y la guarda en outputs/.
      3) Devuelve el texto clinico estructurado para el portapapeles (ABIS).
    """
    data = request.get_json(silent=True) or {}
    centro = centro_activo()

    # ---- Validaciones ------------------------------------------------------
    run = normalizar_run(data.get("run"))
    nombre = limpiar_texto(data.get("nombre"))
    if not run:
        return jsonify({"ok": False, "error": "El RUN es obligatorio."}), 400
    if not nombre:
        return jsonify({"ok": False, "error": "El nombre de la paciente es obligatorio."}), 400

    clasificacion = (data.get("clasificacion") or "").strip()
    if clasificacion not in CLASIFICACIONES:
        return jsonify({
            "ok": False,
            "error": "Debe seleccionar la clasificación estadística (dato obligatorio para reportes).",
        }), 400

    if "es_doppler" not in data:
        return jsonify({
            "ok": False,
            "error": "Debe indicar ¿Es Doppler? (Sí/No).",
        }), 400
    es_doppler = 1 if str(data.get("es_doppler")).lower() in ("1", "true", "si", "sí", "yes") else 0

    tipo_informe = (data.get("tipo_informe") or "").strip()
    if tipo_informe not in TIPO_INFORME_MAP:
        return jsonify({"ok": False, "error": "Debe seleccionar el tipo de informe."}), 400

    fecha_exploracion = (data.get("fecha_exploracion") or date.today().isoformat()).strip()
    fecha_nacimiento = (data.get("fecha_nacimiento") or "").strip() or None

    # ---- Datos clinicos ----------------------------------------------------
    clinico = {
        "profesional_solicitante": limpiar_texto(data.get("profesional_solicitante")),
        "diagnostico_referencia": limpiar_texto(data.get("diagnostico_referencia")),
        "informacion_tecnica": limpiar_texto(data.get("informacion_tecnica")),
        "exploracion": limpiar_texto(data.get("exploracion")),
        "biometria": limpiar_texto(data.get("biometria")),
        "impresion_ecografica": limpiar_texto(data.get("impresion_ecografica")),
        "sugerencias": limpiar_texto(data.get("sugerencias")),
        "edad_gestacional": limpiar_texto(data.get("edad_gestacional")),
        "translucencia_nucal": limpiar_texto(data.get("translucencia_nucal")),
    }
    fur = limpiar_texto(data.get("fur"))

    # ---- Paciente (upsert) -------------------------------------------------
    db = get_db()
    paciente = db.execute("SELECT * FROM pacientes WHERE run = ?", (run,)).fetchone()
    if paciente:
        db.execute(
            "UPDATE pacientes SET nombre = ?, fecha_nacimiento = ?, actualizado_en = "
            "datetime('now','localtime') WHERE id = ?",
            (nombre, fecha_nacimiento or paciente["fecha_nacimiento"], paciente["id"]),
        )
        paciente_id = paciente["id"]
    else:
        cur = db.execute(
            "INSERT INTO pacientes (run, nombre, fecha_nacimiento, telefono, centro_creacion) "
            "VALUES (?,?,?,?,?)",
            (run, nombre, fecha_nacimiento, limpiar_texto(data.get("telefono")) or None, centro),
        )
        paciente_id = cur.lastrowid

    edad = calcular_edad(fecha_nacimiento, fecha_exploracion)
    if edad is None:
        edad = data.get("edad")
        edad = int(edad) if str(edad or "").isdigit() else None

    # ---- Datos estadisticos (solo BD) --------------------------------------
    mapa_word = {
        "nombre": nombre,
        "run": formatear_run(run),
        "edad": str(edad) if edad is not None else "",
        "fur": formatear_fecha_es(fur) if fur and fur.count("-") == 2 else fur,
        "fecha_exploracion": formatear_fecha_es(fecha_exploracion),
        "centro": CENTROS[centro],
        "profesional_solicitante": clinico["profesional_solicitante"],
        "diagnostico_referencia": clinico["diagnostico_referencia"],
        "informacion_tecnica": clinico["informacion_tecnica"],
        "exploracion": clinico["exploracion"],
        "biometria": clinico["biometria"],
        "impresion_ecografica": clinico["impresion_ecografica"],
        "sugerencias": clinico["sugerencias"],
        "edad_gestacional": clinico["edad_gestacional"],
        "translucencia_nucal": clinico["translucencia_nucal"],
        # Firmas / encabezados alternativos
        "centro_corto": centro,
        "firma_profesional": "Matrón Ecografista",
    }
    mapa_abis = dict(mapa_word)
    mapa_abis["edad"] = str(edad) if edad is not None else ""

    texto_abis = construir_texto_abis(mapa_abis)

    # ---- Generacion del .docx ----------------------------------------------
    nombre_archivo = ""
    ruta_destino = ""
    aviso_docx = ""
    nombre_plantilla = f"{tipo_informe}.docx"
    ruta_plantilla = os.path.join(WORD_TEMPLATES_DIR, nombre_plantilla)

    if os.path.exists(ruta_plantilla):
        nombre_archivo = f"{fecha_exploracion}_{slugify(nombre)}.docx"
        # Evita sobrescribir si ya existe un informe de la misma paciente y fecha
        if os.path.exists(os.path.join(OUTPUTS_DIR, nombre_archivo)):
            contador = 2
            while os.path.exists(os.path.join(
                    OUTPUTS_DIR, f"{fecha_exploracion}_{slugify(nombre)}_{contador}.docx")):
                contador += 1
            nombre_archivo = f"{fecha_exploracion}_{slugify(nombre)}_{contador}.docx"
        ruta_destino = os.path.join(OUTPUTS_DIR, nombre_archivo)
        try:
            rellenar_docx(ruta_plantilla, mapa_word, ruta_destino)
        except Exception as exc:  # pragma: no cover - defensivo
            aviso_docx = f"No se pudo generar el Word ({exc})."
            nombre_archivo = ""
            ruta_destino = ""
    else:
        aviso_docx = (
            f"Plantilla '{nombre_plantilla}' no encontrada en word_templates/. "
            "Ejecuta: python tools/crear_plantillas.py"
        )

    # ---- Guarda la atencion ------------------------------------------------
    cur = db.execute(
        """
        INSERT INTO atenciones (
            paciente_id, centro, fecha_exploracion, fur, edad,
            profesional_solicitante, diagnostico_referencia,
            informacion_tecnica, exploracion, biometria, impresion_ecografica, sugerencias,
            texto_abis, archivo_docx,
            clasificacion, es_doppler, tipo_informe
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            paciente_id, centro, fecha_exploracion, fur or None, edad,
            clinico["profesional_solicitante"], clinico["diagnostico_referencia"],
            clinico["informacion_tecnica"], clinico["exploracion"], clinico["biometria"],
            clinico["impresion_ecografica"], clinico["sugerencias"],
            texto_abis, nombre_archivo or None,
            clasificacion, es_doppler, tipo_informe,
        ),
    )
    db.commit()
    atencion_id = cur.lastrowid

    return jsonify({
        "ok": True,
        "atencion_id": atencion_id,
        "paciente_id": paciente_id,
        "docx": f"/outputs/{nombre_archivo}" if nombre_archivo else None,
        "docx_nombre": nombre_archivo,
        "aviso": aviso_docx,
        "abis": texto_abis,
        "edad": edad,
        "centro": centro,
    })


@app.route("/outputs/<path:nombre_archivo>")
def api_descargar_docx(nombre_archivo):
    """Descarga / abre el informe .docx generado."""
    return send_from_directory(OUTPUTS_DIR, nombre_archivo, as_attachment=False)


# --------------------------------------------------------------- REPORTES ---
@app.route("/api/reportes/resumen", methods=["GET"])
def api_reportes_resumen():
    """Resumen mensual (JSON) para previsualizar antes de exportar a Excel."""
    centro = (request.args.get("centro") or centro_activo() or "").strip()
    anio = int(request.args.get("anio") or date.today().year)
    mes = int(request.args.get("mes") or date.today().month)

    db = get_db()
    condiciones = ["strftime('%Y', fecha_exploracion) = ?", "strftime('%m', fecha_exploracion) = ?"]
    params = [str(anio), f"{mes:02d}"]
    if centro and centro != "Todos":
        condiciones.append("centro = ?")
        params.append(centro)

    where = " AND ".join(condiciones)

    filas = db.execute(
        f"""
        SELECT fecha_exploracion, centro, clasificacion, es_doppler, tipo_informe
        FROM atenciones WHERE {where}
        ORDER BY fecha_exploracion, id
        """,
        params,
    ).fetchall()

    doppler = sum(1 for f in filas if f["es_doppler"] == 1)
    total = len(filas)
    por_clasif = {c: 0 for c in CLASIFICACIONES}
    por_fecha = {}
    for f in filas:
        por_clasif[f["clasificacion"]] = por_clasif.get(f["clasificacion"], 0) + 1
        clave = f["fecha_exploracion"]
        d = por_fecha.setdefault(clave, {
            "fecha": clave,
            "fecha_es": formatear_fecha_es(clave),
            "centro": centro_ui(f["centro"]),
            "doppler": 0,
            "no_doppler": 0,
            "total": 0,
            **{c: 0 for c in CLASIFICACIONES},
        })
        d["total"] += 1
        if f["es_doppler"] == 1:
            d["doppler"] += 1
        else:
            d["no_doppler"] += 1
        d[f["clasificacion"]] += 1

    return jsonify({
        "ok": True,
        "anio": anio,
        "mes": mes,
        "centro": centro or "Todos",
        "total": total,
        "doppler": doppler,
        "no_doppler": total - doppler,
        "por_clasificacion": por_clasif,
        "por_fecha": sorted(por_fecha.values(), key=lambda x: x["fecha"]),
    })


@app.route("/api/reportes/excel", methods=["GET"])
def api_reportes_excel():
    """Exporta el reporte mensual a Excel (2 hojas con formulas de suma)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    centro = (request.args.get("centro") or centro_activo() or "Todos").strip()
    anio = int(request.args.get("anio") or date.today().year)
    mes = int(request.args.get("mes") or date.today().month)

    db = get_db()
    condiciones = ["strftime('%Y', fecha_exploracion) = ?", "strftime('%m', fecha_exploracion) = ?"]
    params = [str(anio), f"{mes:02d}"]
    if centro and centro != "Todos":
        condiciones.append("centro = ?")
        params.append(centro)
    where = " AND ".join(condiciones)

    filas = db.execute(
        f"""
        SELECT a.fecha_exploracion, a.centro, a.clasificacion, a.es_doppler,
               a.tipo_informe, p.nombre, p.run
        FROM atenciones a
        JOIN pacientes p ON p.id = a.paciente_id
        WHERE {where}
        ORDER BY a.fecha_exploracion, a.id
        """,
        params,
    ).fetchall()

    # --- Agrupacion por fecha ---------------------------------------------
    por_fecha = {}
    for f in filas:
        clave = f["fecha_exploracion"]
        d = por_fecha.setdefault(clave, {
            "fecha_es": formatear_fecha_es(clave),
            "centro": centro_ui(f["centro"]),
            "doppler": 0,
            "no_doppler": 0,
            "total": 0,
            **{c: 0 for c in CLASIFICACIONES},
        })
        d["total"] += 1
        if f["es_doppler"] == 1:
            d["doppler"] += 1
        else:
            d["no_doppler"] += 1
        d[f["clasificacion"]] += 1

    datos = sorted(por_fecha.values(), key=lambda x: x["fecha_es"])

    wb = Workbook()

    # ======================= HOJA 1: COBRO / SUELDO =========================
    ws = wb.active
    ws.title = "Cobro - Sueldo"
    ws["A1"] = "REPORTE MENSUAL DE ECOGRAFIAS - COBRO / SUELDO"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Periodo: {mes:02d}/{anio}    |    Centro: {centro}    |    Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    ws["A2"].font = Font(italic=True, size=10)

    encabezados1 = ["Fecha", "Centro", "Ecografias Doppler", "Ecografias No Doppler", "Total"]
    for i, h in enumerate(encabezados1, start=1):
        celda = ws.cell(row=4, column=i, value=h)
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = PatternFill("solid", fgColor="1F6FEB")
        celda.alignment = Alignment(horizontal="center")

    fila_ini = 5
    for idx, d in enumerate(datos):
        r = fila_ini + idx
        ws.cell(row=r, column=1, value=d["fecha_es"])
        ws.cell(row=r, column=2, value=d["centro"])
        ws.cell(row=r, column=3, value=d["doppler"])
        ws.cell(row=r, column=4, value=d["no_doppler"])
        ws.cell(row=r, column=5, value=f"=C{r}+D{r}")
    fila_fin = fila_ini + len(datos) - 1

    r_tot = fila_fin + 1 if datos else fila_ini
    ws.cell(row=r_tot, column=1, value="TOTALES").font = Font(bold=True)
    ws.cell(row=r_tot, column=2, value="").font = Font(bold=True)
    for col in (3, 4, 5):
        letra = get_column_letter(col)
        ws.cell(row=r_tot, column=col,
                value=f"=SUM({letra}{fila_ini}:{letra}{fila_fin})" if datos else 0).font = Font(bold=True)
        ws.cell(row=r_tot, column=col).fill = PatternFill("solid", fgColor="E7F0FF")

    # Resumen de cobro por valor referencial
    r = r_tot + 2
    ws.cell(row=r, column=1, value="RESUMEN PARA COBRO").font = Font(bold=True, size=12)
    r += 1
    for etiqueta, formula in [
        ("Total Doppler", f"=SUM(C{fila_ini}:C{fila_fin})" if datos else 0),
        ("Total No Doppler", f"=SUM(D{fila_ini}:D{fila_fin})" if datos else 0),
        ("Total general de ecografias", f"=SUM(E{fila_ini}:E{fila_fin})" if datos else 0),
    ]:
        ws.cell(row=r, column=1, value=etiqueta).font = Font(bold=True)
        ws.cell(row=r, column=3, value=formula).font = Font(bold=True)
        r += 1

    for col, ancho in zip("ABCDE", [14, 16, 20, 22, 12]):
        ws.column_dimensions[col].width = ancho

    # ==================== HOJA 2: ESTADISTICA MUNICIPAL =======================
    ws2 = wb.create_sheet("Estadistica Municipal")
    ws2["A1"] = "REPORTE MENSUAL - ESTADISTICA MUNICIPAL DE ECOGRAFIAS"
    ws2["A1"].font = Font(bold=True, size=14)
    ws2["A2"] = f"Periodo: {mes:02d}/{anio}    |    Centro: {centro}    |    Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    ws2["A2"].font = Font(italic=True, size=10)

    encabezados2 = ["Fecha", "Centro"] + CLASIFICACIONES + ["Total"]
    for i, h in enumerate(encabezados2, start=1):
        celda = ws2.cell(row=4, column=i, value=h)
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = PatternFill("solid", fgColor="0F9D58")
        celda.alignment = Alignment(horizontal="center", wrap_text=True)

    for idx, d in enumerate(datos):
        r = 5 + idx
        ws2.cell(row=r, column=1, value=d["fecha_es"])
        ws2.cell(row=r, column=2, value=d["centro"])
        for j, c in enumerate(CLASIFICACIONES, start=3):
            ws2.cell(row=r, column=j, value=d[c])
        ws2.cell(row=r, column=3 + len(CLASIFICACIONES),
                 value=f"=SUM(C{r}:{get_column_letter(2 + len(CLASIFICACIONES))}{r})")

    fila_fin2 = 5 + len(datos) - 1
    r_tot2 = 5 + len(datos)
    ws2.cell(row=r_tot2, column=1, value="TOTALES").font = Font(bold=True)
    ws2.cell(row=r_tot2, column=2, value="").font = Font(bold=True)
    for j in range(3, 4 + len(CLASIFICACIONES)):
        letra = get_column_letter(j)
        ws2.cell(row=r_tot2, column=j,
                 value=f"=SUM({letra}5:{letra}{fila_fin2})" if datos else 0).font = Font(bold=True)
        ws2.cell(row=r_tot2, column=j).fill = PatternFill("solid", fgColor="E8F5EC")

    ws2.column_dimensions["A"].width = 14
    ws2.column_dimensions["B"].width = 16
    for j in range(3, 4 + len(CLASIFICACIONES)):
        ws2.column_dimensions[get_column_letter(j)].width = 15

    # --- Hoja 3 (extra): detalle por atencion (trazabilidad) ---------------
    ws3 = wb.create_sheet("Detalle")
    det_enc = ["Fecha", "Centro", "RUN", "Nombre", "Clasificacion", "Doppler", "Tipo Informe"]
    for i, h in enumerate(det_enc, start=1):
        celda = ws3.cell(row=1, column=i, value=h)
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = PatternFill("solid", fgColor="6C757D")
    for idx, f in enumerate(filas):
        r = 2 + idx
        ws3.cell(row=r, column=1, value=formatear_fecha_es(f["fecha_exploracion"]))
        ws3.cell(row=r, column=2, value=centro_ui(f["centro"]))
        ws3.cell(row=r, column=3, value=formatear_run(f["run"]))
        ws3.cell(row=r, column=4, value=f["nombre"])
        ws3.cell(row=r, column=5, value=f["clasificacion"])
        ws3.cell(row=r, column=6, value="SI" if f["es_doppler"] == 1 else "NO")
        ws3.cell(row=r, column=7, value=TIPO_INFORME_MAP.get(f["tipo_informe"], f["tipo_informe"]))
    for col, ancho in zip("ABCDEFG", [12, 12, 14, 30, 18, 10, 26]):
        ws3.column_dimensions[col].width = ancho

    # Bordes basicos
    borde = Border(*[Side(style="thin", color="D0D7DE")] * 4)
    for hoja, rango in ((ws, (4, r_tot, 1, 5)), (ws2, (4, r_tot2, 1, 4 + len(CLASIFICACIONES)))):
        for row in hoja.iter_rows(min_row=rango[0], max_row=rango[1],
                                  min_col=rango[2], max_col=rango[3]):
            for celda in row:
                celda.border = borde

    # --- Respuesta como descarga ------------------------------------------
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    nombre = f"Reporte_Ecografias_{centro}_{anio}-{mes:02d}.xlsx"
    return send_file(
        buffer,
        as_attachment=True,
        download_name=nombre,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ==============================================================================
# 7) ARRANQUE
# ==============================================================================

if __name__ == "__main__":
    init_db()
    print("=" * 70)
    print("  EcoGestor Local - Aplicacion OFFLINE lista")
    print(f"  Base de datos : {DB_PATH}")
    print(f"  Plantillas    : {WORD_TEMPLATES_DIR}")
    print(f"  Salidas       : {OUTPUTS_DIR}")
    print("  Abre en el navegador: http://127.0.0.1:5000")
    print("=" * 70)
    app.run(host="127.0.0.1", port=5000, debug=False)
