# -*- coding: utf-8 -*-
"""
================================================================================
 EcoGestor Local - Generador de plantillas institucionales .docx
================================================================================
 Crea (o recrea) los 7 documentos base en la carpeta word_templates/.

 Cada plantilla contiene marcadores {{variable}} que la aplicacion reemplaza
 con python-docx conservando intactas las tablas, margenes y estilos.

 USO:  python tools/crear_plantillas.py
================================================================================
"""

import os

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DESTINO = os.path.join(BASE_DIR, "word_templates")

AZUL = RGBColor(0x1F, 0x3B, 0x73)
GRIS = RGBColor(0x59, 0x59, 0x59)


# ----------------------------------------------------------------------
# Configuracion de cada tipo de informe
# ----------------------------------------------------------------------
PLANTILLAS = {
    "ginecologico": {
        "titulo": "INFORME ECOGRÁFICO GINECOLÓGICO",
        "subtitulo": "Ecografía transvaginal / pélvica",
        "campos_extra": [],
    },
    "eco_precoz": {
        "titulo": "INFORME ECOGRÁFICO PRECOZ",
        "subtitulo": "Control de gestación temprana (1er trimestre)",
        "campos_extra": [],
    },
    "screening_11_14": {
        "titulo": "INFORME ECOGRÁFICO - SCREENING 11 - 14 SEMANAS",
        "subtitulo": "Cálculo de translucencia nucal y marcadores del primer trimestre",
        "campos_extra": [
            ("Edad gestacional", "{{edad_gestacional}}"),
            ("Translucencia nucal (TN)", "{{translucencia_nucal}}"),
        ],
    },
    "obstetrica_mas_14": {
        "titulo": "INFORME ECOGRÁFICO OBSTÉTRICO",
        "subtitulo": "Control obstétrico (gestación > 14 semanas)",
        "campos_extra": [
            ("Edad gestacional", "{{edad_gestacional}}"),
        ],
    },
    "eco_obs_2t_sin_doppler": {
        "titulo": "INFORME ECOGRÁFICO OBSTÉTRICO - 2DO TRIMESTRE",
        "subtitulo": "Control morfológico / crecimiento SIN estudio Doppler",
        "campos_extra": [
            ("Edad gestacional", "{{edad_gestacional}}"),
        ],
    },
    "gemelar_precoz": {
        "titulo": "INFORME ECOGRÁFICO - GESTACIÓN GEMELAR PRECOZ",
        "subtitulo": "Control gestacional gemelar (1er trimestre)",
        "campos_extra": [
            ("Edad gestacional", "{{edad_gestacional}}"),
        ],
    },
    "gemelar_2t_doppler": {
        "titulo": "INFORME ECOGRÁFICO - GESTACIÓN GEMELAR 2DO TRIMESTRE CON DOPPLER",
        "subtitulo": "Control gemelar con estudio Doppler",
        "campos_extra": [
            ("Edad gestacional", "{{edad_gestacional}}"),
        ],
    },
}


def _estilo_base(doc: Document) -> None:
    """Tipografía general del documento."""
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)


def _encabezado(doc: Document, titulo: str, subtitulo: str) -> None:
    """Institución + título centrado (conserva formato tras el reemplazo)."""
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("{{centro}}")
    run.bold = True
    run.font.size = Pt(12)
    run.font.color.rgb = AZUL

    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run2 = p2.add_run(titulo)
    run2.bold = True
    run2.font.size = Pt(14)
    run2.font.color.rgb = AZUL

    p3 = doc.add_paragraph()
    p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run3 = p3.add_run(subtitulo)
    run3.italic = True
    run3.font.size = Pt(9.5)
    run3.font.color.rgb = GRIS

    # Línea separadora
    sep = doc.add_paragraph()
    sep_run = sep.add_run("-" * 74)
    sep_run.font.size = Pt(8)
    sep_run.font.color.rgb = GRIS


def _tabla_identificacion(doc: Document, campos_extra) -> None:
    """Tabla 2 columnas con los datos de identificación (marcadores intactos)."""
    filas = [
        ("Nombre de la paciente", "{{nombre}}"),
        ("RUN", "{{run}}"),
        ("Edad", "{{edad}} años"),
        ("FUR", "{{fur}}"),
        ("Fecha de exploración", "{{fecha_exploracion}}"),
        ("Profesional solicitante", "{{profesional_solicitante}}"),
        ("Diagnóstico de referencia", "{{diagnostico_referencia}}"),
    ] + list(campos_extra)

    tabla = doc.add_table(rows=0, cols=2)
    tabla.style = "Table Grid"
    tabla.alignment = WD_TABLE_ALIGNMENT.CENTER

    for etiqueta, valor in filas:
        row = tabla.add_row()
        celda_etq = row.cells[0]
        celda_val = row.cells[1]

        r1 = celda_etq.paragraphs[0].add_run(etiqueta)
        r1.bold = True
        r1.font.size = Pt(10)

        r2 = celda_val.paragraphs[0].add_run(valor)
        r2.font.size = Pt(10)

        celda_etq.width = Cm(5.2)
        celda_val.width = Cm(10.8)

    doc.add_paragraph()


def _seccion(doc: Document, titulo: str, marcador: str) -> None:
    """Título de sección + marcador de párrafo (soporta texto multilínea)."""
    p = doc.add_paragraph()
    run = p.add_run(titulo)
    run.bold = True
    run.font.size = Pt(11)
    run.font.color.rgb = AZUL

    cuerpo = doc.add_paragraph()
    cuerpo.add_run(marcador)
    cuerpo.paragraph_format.space_after = Pt(8)


def _firma(doc: Document) -> None:
    """Bloque de firma institucional (NO incluye datos estadísticos)."""
    doc.add_paragraph()

    for texto in [
        "_________________________________",
        "Firma profesional: Matrón Ecografista",
        "{{centro}}",
        "Este informe no incluye clasificación trimestral ni indicador Doppler.",
    ]:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(texto)
        run.font.size = Pt(9)
        if texto.startswith("Este informe"):
            run.italic = True
            run.font.color.rgb = GRIS


def crear_plantilla(slug: str, cfg: dict) -> str:
    """Genera una plantilla .docx y devuelve su ruta."""
    doc = Document()

    # Márgenes institucionales
    for seccion in doc.sections:
        seccion.top_margin = Cm(1.8)
        seccion.bottom_margin = Cm(1.8)
        seccion.left_margin = Cm(2.0)
        seccion.right_margin = Cm(2.0)

    _estilo_base(doc)
    _encabezado(doc, cfg["titulo"], cfg["subtitulo"])
    _tabla_identificacion(doc, cfg.get("campos_extra", []))

    _seccion(doc, "INFORMACIÓN TÉCNICA", "{{informacion_tecnica}}")
    _seccion(doc, "EXPLORACIÓN", "{{exploracion}}")
    _seccion(doc, "BIOMETRÍA", "{{biometria}}")
    _seccion(doc, "IMPRESIÓN ECOGRÁFICA", "{{impresion_ecografica}}")
    _seccion(doc, "SUGERENCIAS", "{{sugerencias}}")

    _firma(doc)

    ruta = os.path.join(DESTINO, f"{slug}.docx")
    doc.save(ruta)
    return ruta


def main() -> None:
    os.makedirs(DESTINO, exist_ok=True)
    for slug, cfg in PLANTILLAS.items():
        ruta = crear_plantilla(slug, cfg)
        print(f"[OK] {os.path.basename(ruta)}  <-  {cfg['titulo']}")
    print(f"\nPlantillas generadas en: {DESTINO}")


if __name__ == "__main__":
    main()
