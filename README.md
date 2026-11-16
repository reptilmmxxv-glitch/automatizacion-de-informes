# EcoGestor Local

Aplicación web **offline y portable** (corre desde un pendrive) para **Matrón Ecografista**
que trabaja en dos centros municipales: **Nogales** y **Melón**.

Genera informes ecográficos en **Word** (plantillas institucionales), copia el texto clínico
al **portapapeles** para la ficha **ABIS**, guarda todo en **SQLite** y exporta **reportes
mensuales en Excel**.

---

## 1. Estructura del pendrive

```
<PENDRIVE>/
├── app.py                  # Backend Flask + SQLite + Word + Excel
├── requirements.txt
├── README.md
├── database.db              # Base de datos local (se crea sola al iniciar)
├── templates/
│   └── index.html           # Interfaz única (Bootstrap 5 vendorizado)
├── static/
│   ├── css/app.css
│   ├── js/app.js
│   └── vendor/              # bootstrap 5.3.3 (min.css / bundle.min.js) -> OFFLINE
├── word_templates/          # 7 plantillas .docx con marcadores {{variable}}
│   ├── ginecologico.docx
│   ├── eco_precoz.docx
│   ├── screening_11_14.docx
│   ├── obstetrica_mas_14.docx
│   ├── eco_obs_2t_sin_doppler.docx
│   ├── gemelar_precoz.docx
│   └── gemelar_2t_doppler.docx
├── outputs/                 # Informes generados: [Fecha]_[Nombre].docx
└── tools/
    └── crear_plantillas.py  # Regenerador de las plantillas .docx
```

## 2. Instalación (una sola vez, con internet)

```bash
pip install -r requirements.txt
python tools/crear_plantillas.py   # genera los .docx base en word_templates/
```

## 3. Uso diario (sin internet)

```bash
python app.py
```

Luego abrir <http://127.0.0.1:5000> en el navegador.

### Flujo de trabajo

1. **Seleccionar centro** (obligatorio). Hasta que no se elija Nogales o Melón, el formulario
   permanece bloqueado. El centro define: la base de datos, la firma del Word
   (`Departamento de Salud Nogales` / `Departamento de Salud Melón`) y el filtro de reportes.
2. **RUN** → se busca en SQLite. Si la paciente existe se autocompletan nombre y edad y aparece
   el botón **“Cargar último informe como base”** (también se ve el historial).
3. **Datos estadísticos** (bloque naranja): `Clasificación` y `¿Es Doppler?` son obligatorios.
   **Nunca se imprimen** en el Word ni en el portapapeles; solo alimentan los reportes.
4. **Macros clínicas**: un clic inserta el texto en el campo correspondiente.
   El enlace *Macros* permite crear, editar y eliminar macros (quedan guardadas en SQLite).
5. **Generar y Copiar** (`Ctrl+Enter`): en un solo clic
   - guarda la atención en SQLite,
   - rellena la plantilla `.docx` y la deja en `outputs/`,
   - copia el texto clínico estructurado al portapapeles (listo para `Ctrl+V` en ABIS).
6. **Reportes**: botón *Reportes* de la barra superior. Filtra por mes, año y centro y descarga
   el Excel con 3 hojas:
   - **Cobro - Sueldo**: Doppler vs No Doppler por día + totales con fórmulas `SUM`.
   - **Estadistica Municipal**: 1er / 2do / 3er trimestre, DIU, Climaterio y Ginecológicas.
   - **Detalle**: trazabilidad por atención.

## 4. Separación clínica / estadística (regla de oro)

| Dato | ¿Va al Word? | ¿Va al portapapeles (ABIS)? | ¿Va a SQLite? |
|---|---|---|---|
| Nombre, RUN, Edad, FUR, Fecha exploración, Solicitante, Diagnóstico | Sí | Sí | Sí |
| Información técnica, Exploración, Biometría, Impresión, Sugerencias | Sí | Sí | Sí |
| **Clasificación** (1T/2T/3T/DIU/Climaterio/Ginecológica) | **No** | **No** | **Sí** |
| **¿Es Doppler?** (Sí/No) | **No** | **No** | **Sí** |

## 5. Marcadores admitidos en las plantillas `.docx`

`{{nombre}}`, `{{run}}`, `{{edad}}`, `{{fur}}`, `{{fecha_exploracion}}`,
`{{profesional_solicitante}}`, `{{diagnostico_referencia}}`, `{{informacion_tecnica}}`,
`{{exploracion}}`, `{{biometria}}`, `{{impresion_ecografica}}`, `{{sugerencias}}`,
`{{edad_gestacional}}`, `{{translucencia_nucal}}`, `{{centro}}`, `{{centro_corto}}`.

> Para personalizar una plantilla (logo del municipio, membrete, etc.) basta con abrirla en
> Word, editarla y conservar los marcadores. La aplicación rellena tablas, párrafos,
> encabezados y pies de página sin alterar el formato original.

## 6. Copia de seguridad

Todo el historial vive en `database.db`. Copiar ese archivo (y la carpeta `outputs/`) es
suficiente para respaldar toda la información.
