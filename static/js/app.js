/* =====================================================================
   EcoGestor Local - Logica del formulario (JavaScript puro, sin CDN)
   ===================================================================== */

'use strict';

/* ------------------------------------------------------------------
   Estado global de la sesion en el navegador
   ------------------------------------------------------------------ */
const Estado = {
    centro: null,        // clave interna del centro activo ('Nogales' | 'Melon')
    centroUi: null,      // etiqueta visible ('Nogales' | 'Melón')
    macros: [],          // macros descargadas del servidor
    campoMacroActivo: null, // campo_destinobeing editado en el modal de macros
    ultimoInforme: null, // ultimo informe de la paciente (para "cargar como base")
    ultimoDebounce: null
};

/* ------------------------------------------------------------------
   Utilidades
   ------------------------------------------------------------------ */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const CAMPOS = {
    nombre: 'nombre',
    run: 'run',
    edad: 'edad',
    fechaNacimiento: 'fechaNacimiento',
    fur: 'fur',
    fechaExploracion: 'fechaExploracion',
    profesionalSolicitante: 'profesionalSolicitante',
    diagnosticoReferencia: 'diagnosticoReferencia',
    informacionTecnica: 'informacionTecnica',
    exploracion: 'exploracion',
    biometria: 'biometria',
    impresionEcografica: 'impresionEcografica',
    sugerencias: 'sugerencias'
};

/** Muestra un toast (notificacion flotante) de Bootstrap. */
function toast(mensaje, tipo = 'success', titulo = '') {
    const id = 'toast-' + Date.now();
    const color = { success: 'text-bg-success', danger: 'text-bg-danger',
                    warning: 'text-bg-warning', info: 'text-bg-info' }[tipo] || 'text-bg-secondary';
    const html = `
        <div id="${id}" class="toast ${tipo === 'warning' ? 'border-warning' : ''}" role="alert">
            <div class="toast-header ${color}">
                <strong class="me-auto">${titulo || 'EcoGestor'}</strong>
                <small class="text-white-50">ahora</small>
                <button type="button" class="btn-close btn-close-white" data-bs-dismiss="toast"></button>
            </div>
            <div class="toast-body">${mensaje}</div>
        </div>`;
    $('#toastContainer').insertAdjacentHTML('beforeend', html);
    const el = document.getElementById(id);
    new bootstrap.Toast(el, { delay: 3500 }).show();
}

/** Copia texto al portapapeles del sistema (con respaldo para navegadores antiguos). */
async function copiarAlPortapapeles(texto) {
    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(texto);
            return true;
        }
    } catch (e) { /* cae al metodo alternativo */ }

    try {
        const area = document.createElement('textarea');
        area.value = texto;
        area.setAttribute('readonly', '');
        area.style.position = 'fixed';
        area.style.opacity = '0';
        document.body.appendChild(area);
        area.select();
        area.setSelectionRange(0, texto.length);
        const ok = document.execCommand('copy');
        document.body.removeChild(area);
        return ok;
    } catch (e) {
        return false;
    }
}

/** Peticion JSON POST. */
async function postJSON(url, payload) {
    const resp = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {})
    });
    return await resp.json();
}

/** Peticion JSON GET. */
async function getJSON(url) {
    const resp = await fetch(url);
    return await resp.json();
}

/* ------------------------------------------------------------------
   1) CONTEXTO ACTIVO (CENTRO OBLIGATORIO)
   ------------------------------------------------------------------ */
async function cargarContexto() {
    const data = await getJSON('/api/contexto');
    Estado.centro = data.centro;
    Estado.centro_ui = data.centro_ui;
    aplicarEstadoCentro();
}

function aplicarEstadoCentro() {
    const bloqueo = $('#bloqueoCentro');
    const contenido = $('#appContent');
    const badge = $('#badgeCentroActual');
    const btnFirma = $('#badgeFirma');

    $$('.btn-centro').forEach(btn => {
        btn.classList.toggle('activo', btn.dataset.centro === Estado.centroUi);
    });

    if (Estado.centro) {
        bloqueo.classList.add('d-none');
        contenido.classList.remove('bloqueado');
        badge.textContent = 'Centro: ' + Estado.centroUi;
        badge.className = 'badge text-bg-light';
        btnFirma.textContent = 'Firma: Departamento de Salud ' + Estado.centroUi;
        btnFirma.className = 'badge text-bg-success';
        // Con el centro activo habilitamos el formulario completo
        cargarMacros();
    } else {
        bloqueo.classList.remove('d-none');
        contenido.classList.add('bloqueado');
        badge.textContent = 'Sin centro seleccionado';
        badge.className = 'badge text-bg-danger';
        btnFirma.textContent = 'Firma: —';
        btnFirma.className = 'badge text-bg-secondary';
    }
}

async function seleccionarCentro(nombreUi) {
    const data = await postJSON('/api/contexto', { centro: nombreUi });
    if (!data.ok) {
        toast(data.error || 'No se pudo seleccionar el centro.', 'danger');
        return;
    }
    Estado.centro = data.centro;
    Estado.centroUi = data.centro_ui;
    aplicarEstadoCentro();
    toast(`Centro activo: <strong>${Estado.centroUi}</strong><br>
            Todos los informes se firmarán a nombre de <em>${data.centro_nombre}</em>.`,
           'success', 'Contexto establecido');
}

/* ------------------------------------------------------------------
   2) BUSQUEDA DE PACIENTES RECURRENTES POR RUN
   ------------------------------------------------------------------ */
function normalizarRun(run) {
    return (run || '').toUpperCase().replace(/[.\-\s]/g, '');
}

function formatearRun(run) {
    const r = normalizarRun(run);
    if (!r) return '';
    if (r.endsWith('K') || r.endsWith('9')) return `${r.slice(0, -1)}.${r.slice(-1)}`;
    return r;
}

async function buscarPaciente(run) {
    const info = $('#infoPaciente');
    if (!run || run.length < 4) {
        info.innerHTML = '<span class="text-secondary">Ingrese el RUN para buscar historia previa.</span>';
        $('#btnCargarUltimoInforme').classList.add('d-none');
        return;
    }

    const data = await getJSON('/api/pacientes?q=' + encodeURIComponent(run));
    if (!data.ok || !data.pacientes.length) {
        info.innerHTML = '<span class="text-secondary">Paciente nueva: se creará el registro al guardar.</span>';
        $('#btnCargarUltimoInforme').classList.add('d-none');
        $('#historial').innerHTML = '<div class="text-secondary small p-2">Sin atenciones previas.</div>';
        return;
    }

    const p = data.pacientes.find(x => x.run === normalizarRun(run)) || data.pacientes[0];

    // Autocompletado de nombre y edad
    $('#nombre').value = p.nombre;
    if (p.fecha_nacimiento) $('#fechaNacimiento').value = p.fecha_nacimiento;
    calcularEdadAutomatica();

    // Boton "cargar ultimo informe"
    const btnCargar = $('#btnCargarUltimoInforme');
    if (p.ultimo_informe) {
        Estado.ultimoInforme = p.ultimo_informe;
        btnCargar.classList.remove('d-none');
        info.innerHTML = `<span class="text-success"><i class="bi bi-check-circle"></i>
            Paciente recurrente: <strong>${p.nombre}</strong>
            (última atención: ${p.ultimo_informe.fecha_exploracion})</span>`;
    } else {
        Estado.ultimoInforme = null;
        btnCargar.classList.add('d-none');
        info.innerHTML = `<span class="text-success"><i class="bi bi-check-circle"></i>
            Paciente recurrente: <strong>${p.nombre}</strong> — sin informes previos.</span>`;
    }

    // Historial
    const det = await getJSON('/api/paciente/' + encodeURIComponent(p.run));
    renderHistorial(det.atenciones || []);
}

function renderHistorial(atenciones) {
    const cont = $('#historial');
    if (!atenciones.length) {
        cont.innerHTML = '<div class="text-secondary small p-2">Sin atenciones previas.</div>';
        return;
    }
    cont.innerHTML = atenciones.map(a => `
        <div class="hist-item">
            <div><strong>${a.fecha_exploracion}</strong>
                 <span class="badge text-bg-light">${a.centro}</span></div>
            <small>${a.tipo_informe} · ${a.clasificacion} ·
                  Doppler: ${a.es_doppler ? 'Sí' : 'No'}</small>
        </div>`).join('');
}

/** Copia los datos clinicos del ultimo informe como base del nuevo. */
function cargarUltimoInforme() {
    const a = Estado.ultimoInforme;
    if (!a) {
        toast('No hay informe previo para cargar.', 'warning');
        return;
    }
    $('#profesionalSolicitante').value = a.profesional_solicitante || '';
    $('#diagnosticoReferencia').value = a.diagnostico_referencia || '';
    $('#informacionTecnica').value = a.informacion_tecnica || '';
    $('#exploracion').value = a.exploracion || '';
    $('#biometria').value = a.biometria || '';
    $('#impresionEcografica').value = a.impresion_ecografica || '';
    $('#sugerencias').value = a.sugerencias || '';
    $('#tipoInforme').value = a.tipo_informe || '';
    $('#clasificacion').value = a.clasificacion || '';
    if (a.es_doppler) $('#dopplerSi').checked = true; else $('#dopplerNo').checked = true;
    if (a.fur) $('#fur').value = a.fur;
    actualizarInfoPlantilla();

    toast('Datos del último informe cargados como base.<br>' +
          '<small>Revise y ajuste antes de generar.</small>', 'info', 'Informe cargado');
}

/* ------------------------------------------------------------------
   3) EDAD AUTOMATICA A PARTIR DE LA FECHA DE NACIMIENTO
   ------------------------------------------------------------------ */
function calcularEdadAutomatica() {
    const nac = $('#fechaNacimiento').value;
    if (!nac) return;
    const ref = $('#fechaExploracion').value || new Date().toISOString().slice(0, 10);
    const dNac = new Date(nac + 'T00:00:00');
    const dRef = new Date(ref + 'T00:00:00');
    if (isNaN(dNac) || isNaN(dRef)) return;
    let edad = dRef.getFullYear() - dNac.getFullYear();
    const m = dRef.getMonth() - dNac.getMonth();
    if (m < 0 || (m === 0 && dRef.getDate() < dNac.getDate())) edad--;
    if (edad >= 0 && edad <= 120) $('#edad').value = edad;
}

/* ------------------------------------------------------------------
   4) MACROS CLINICAS
   ------------------------------------------------------------------ */
async function cargarMacros() {
    const data = await getJSON('/api/macros');
    if (!data.ok) return;
    Estado.macros = data.macros;
    renderMacros();
}

function renderMacros() {
    $$('.macros-barra').forEach(barra => {
        const campo = barra.dataset.macros;
        const macros = Estado.macros.filter(m => m.campo_destino === campo);
        barra.innerHTML = '';
        if (!macros.length) {
            barra.innerHTML = '<span class="macros-barra btn-empty btn btn-outline-secondary" disabled>' +
                              'Sin macros en este campo</span>';
            return;
        }
        macros.forEach(m => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'btn btn-outline-primary';
            btn.textContent = m.nombre;
            btn.title = m.texto;
            btn.addEventListener('click', () => insertarMacro(campo, m.texto));
            barra.appendChild(btn);
        });
    });
}

/** Inserta el texto de la macro en el campo correspondiente, en la posicion del cursor. */
function insertarMacro(campo, texto) {
    const area = document.querySelector(`.macro-target[data-campo="${campo}"]`);
    if (!area) return;

    const inicio = area.selectionStart ?? area.value.length;
    const fin = area.selectionEnd ?? area.value.length;
    const valor = area.value;

    let nuevo = '';
    if (fin > inicio) {
        // Reemplaza el texto seleccionado
        nuevo = valor.slice(0, inicio) + texto + valor.slice(fin);
    } else {
        const pre = valor.slice(0, inicio);
        const post = valor.slice(fin);
        const sepAntes = pre && !pre.endsWith('\n') ? '\n' : '';
        const sepDespues = post && !post.startsWith('\n') ? '\n' : '';
        nuevo = pre + sepAntes + texto + sepDespues + post;
    }

    area.value = nuevo.trim() === '' ? nuevo : nuevo;
    // Reposiciona el cursor al final del texto insertado
    const pos = inicio + texto.length;
    area.focus();
    area.setSelectionRange(pos, pos);
    area.dispatchEvent(new Event('input'));
}

/* ------------------------------------------------------------------
   5) GESTION DE MACROS (modal CRUD)
   ------------------------------------------------------------------ */
function abrirModalMacros(campo) {
    Estado.campoMacroActivo = campo;
    $('#macroCampoTitle').textContent = etiquetaCampo(campo);
    $('#macroId').value = '';
    $('#macroNombre').value = '';
    $('#macroTexto').value = '';
    renderListaMacrosModal();
    new bootstrap.Modal(document.getElementById('modalMacros')).show();
}

function etiquetaCampo(campo) {
    return {
        informacion_tecnica: 'Información Técnica',
        exploracion: 'Exploración',
        biometria: 'Biometría',
        impresion_ecografica: 'Impresión Ecográfica',
        sugerencias: 'Sugerencias'
    }[campo] || campo;
}

function renderListaMacrosModal() {
    const cont = $('#listaMacrosModal');
    const macros = Estado.macros.filter(m => m.campo_destino === Estado.campoMacroActivo);
    if (!macros.length) {
        cont.innerHTML = '<div class="text-secondary small">No hay macros en este campo. Crea la primera.</div>';
        return;
    }
    cont.innerHTML = macros.map(m => `
        <div class="list-group-item d-flex justify-content-between align-items-center gap-2">
            <div class="flex-grow-1">
                <div class="fw-semibold">${m.nombre}</div>
                <small class="text-secondary">${m.texto}</small>
            </div>
            <div class="btn-group btn-group-sm">
                <button class="btn btn-outline-secondary" onclick="editarMacro(${m.id})"
                        title="Editar"><i class="bi bi-pencil"></i></button>
                <button class="btn btn-outline-danger" onclick="eliminarMacro(${m.id})"
                        title="Eliminar"><i class="bi bi-trash"></i></button>
            </div>
        </div>`).join('');
}

function editarMacro(id) {
    const m = Estado.macros.find(x => x.id === id);
    if (!m) return;
    $('#macroId').value = m.id;
    $('#macroNombre').value = m.nombre;
    $('#macroTexto').value = m.texto;
    $('#macroNombre').focus();
}

async function eliminarMacro(id) {
    if (!confirm('¿Eliminar esta macro?')) return;
    const data = await fetch('/api/macros/' + id, { method: 'DELETE' });
    const r = await data.json();
    if (r.ok) {
        Estado.macros = Estado.macros.filter(m => m.id !== id);
        renderMacros();
        renderListaMacrosModal();
        toast('Macro eliminada.', 'info');
    }
}

async function guardarMacro(event) {
    event.preventDefault();
    const id = $('#macroId').value;
    const nombre = $('#macroNombre').value.trim();
    const texto = $('#macroTexto').value.trim();
    if (!nombre || !texto) return;

    const payload = {
        nombre, texto,
        campo_destino: Estado.campoMacroActivo
    };

    let data;
    if (id) {
        const resp = await fetch('/api/macros/' + id, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        data = await resp.json();
    } else {
        data = await postJSON('/api/macros', payload);
    }

    if (!data.ok) {
        toast(data.error || 'No se pudo guardar la macro.', 'danger');
        return;
    }

    // Actualiza el cache local
    const idx = Estado.macros.findIndex(m => m.id === data.macro.id);
    if (idx >= 0) Estado.macros[idx] = data.macro;
    else Estado.macros.push(data.macro);

    renderMacros();
    renderListaMacrosModal();
    $('#macroId').value = '';
    $('#macroNombre').value = '';
    $('#macroTexto').value = '';
    toast('Macro guardada.', 'success');
}

/* ------------------------------------------------------------------
   6) GENERAR INFORME: BD + WORD + PORTAPAPELES
   ------------------------------------------------------------------ */
function recopilarDatos() {
    const dopplerSi = $('#dopplerSi').checked;
    return {
        run: $('#run').value.trim(),
        nombre: $('#nombre').value.trim(),
        fecha_nacimiento: $('#fechaNacimiento').value,
        edad: $('#edad').value,
        fur: $('#fur').value,
        fecha_exploracion: $('#fechaExploracion').value,
        profesional_solicitante: $('#profesionalSolicitante').value.trim(),
        diagnostico_referencia: $('#diagnosticoReferencia').value.trim(),
        clasificacion: $('#clasificacion').value,
        es_doppler: dopplerSi ? 1 : 0,
        tipo_informe: $('#tipoInforme').value,
        informacion_tecnica: $('#informacionTecnica').value,
        exploracion: $('#exploracion').value,
        biometria: $('#biometria').value,
        edad_gestacional: $('#edadGestacional').value.trim(),
        translucencia_nucal: $('#translucenciaNucal').value.trim(),
        impresion_ecografica: $('#impresionEcografica').value,
        sugerencias: $('#sugerencias').value
    };
}

function validarFormulario(datos) {
    const errores = [];
    if (!datos.run) errores.push('Ingrese el RUN.');
    if (!datos.nombre) errores.push('Ingrese el nombre de la paciente.');
    if (!datos.fecha_exploracion) errores.push('Ingrese la fecha de exploración.');
    if (!datos.clasificacion) errores.push('Seleccione la clasificación (obligatorio, solo BD).');
    if (!datos.tipo_informe) errores.push('Seleccione el tipo de informe.');
    if (!$('#dopplerSi').checked && !$('#dopplerNo').checked) {
        errores.push('Indique ¿Es Doppler? (Sí/No).');
    }
    return errores;
}

async function generarInforme() {
    if (!Estado.centro) {
        toast('Debe seleccionar el centro de salud.', 'danger');
        return;
    }

    const datos = recopilarDatos();
    const errores = validarFormulario(datos);
    if (errores.length) {
        toast('<ul class="mb-0">' + errores.map(e => `<li>${e}</li>`).join('') + '</ul>', 'danger',
              'Faltan datos');
        return;
    }

    const btn = $('#btnGenerar');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Generando…';

    try {
        const data = await postJSON('/api/informes', datos);

        if (!data.ok) {
            toast(data.error || 'No se pudo generar el informe.', 'danger');
            return;
        }

        // Copia automatica al portapapeles (ABIS)
        const copiado = await copiarAlPortapapeles(data.abis);
        $('#previewAbis').textContent = data.abis;

        // Actualiza el historial con la atencion recien creada
        const pac = await getJSON('/api/paciente/' + encodeURIComponent(datos.run));
        renderHistorial(pac.atenciones || []);

        $('#resultadoBox').innerHTML = `
            <div class="text-success"><i class="bi bi-check-circle"></i> Informe generado y guardado en BD.</div>
            <div><i class="bi bi-clipboard-check"></i> ${copiado ? 'Texto copiado al portapapeles.'
                        : 'No se pudo copiar: copie manualmente desde la vista previa.'}</div>
            ${data.docx ? `<div><a href="${data.docx}" target="_blank"><i class="bi bi-file-earmark-word"></i>
                          ${data.docx_nombre}</a></div>` : ''}
            ${data.aviso ? `<div class="text-warning small">${data.aviso}</div>` : ''}
        `;

        $('#confirmTitulo').textContent = 'Informe generado correctamente';
        $('#confirmMensaje').innerHTML =
            `Word: <strong>${data.docx_nombre || 'no generado'}</strong><br>` +
            `Portapapeles: <strong>${copiado ? 'copiado' : 'pendiente'}</strong><br>` +
            `Centro: ${data.centro} · Clasificación: ${datos.clasificacion} · ` +
            `Doppler: ${datos.es_doppler ? 'Sí' : 'No'}`;
        $('#confirmLink').innerHTML = data.docx
            ? `<a class="btn btn-sm btn-outline-primary mt-2" href="${data.docx}" target="_blank">
                 Abrir informe Word</a>` : '';
        new bootstrap.Modal(document.getElementById('modalConfirmacion')).show();

    } catch (err) {
        toast('Error inesperado: ' + err.message, 'danger');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-lightning-charge-fill"></i> Generar y Copiar (Word + ABIS)';
    }
}

/* ------------------------------------------------------------------
   7) REPORTES MENSUALES (EXCEL)
   ------------------------------------------------------------------ */
function llenarSelectoresReporte() {
    const meses = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
                   'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'];
    const selMes = $('#repMes');
    const selAnio = $('#repAnio');
    const ahora = new Date();

    selMes.innerHTML = meses.map((m, i) =>
        `<option value="${i + 1}" ${i + 1 === ahora.getMonth() + 1 ? 'selected' : ''}>${m}</option>`
    ).join('');

    const anios = [];
    for (let a = ahora.getFullYear() - 3; a <= ahora.getFullYear() + 1; a++) anios.push(a);
    selAnio.innerHTML = anios.map(a =>
        `<option value="${a}" ${a === ahora.getFullYear() ? 'selected' : ''}>${a}</option>`
    ).join('');

    if (Estado.centro) $('#repCentro').value = Estado.centro;
}

function parametrosReporte() {
    return new URLSearchParams({
        mes: $('#repMes').value,
        anio: $('#repAnio').value,
        centro: $('#repCentro').value
    }).toString();
}

async function cargarResumenReporte() {
    const cont = $('#resumenReporte');
    cont.innerHTML = '<div class="text-secondary small">Cargando…</div>';
    const data = await getJSON('/api/reportes/resumen?' + parametrosReporte());
    if (!data.ok) {
        cont.innerHTML = '<div class="text-danger small">No se pudo cargar el resumen.</div>';
        return;
    }

    const clasif = Object.entries(data.por_clasificacion)
        .map(([k, v]) => `<tr><td>${k}</td><td class="text-end fw-semibold">${v}</td></tr>`).join('');

    const filas = data.por_fecha.map(f => `
        <tr>
            <td>${f.fecha_es}</td>
            <td>${f.centro}</td>
            <td class="text-end">${f.doppler}</td>
            <td class="text-end">${f.no_doppler}</td>
            <td class="text-end fw-semibold">${f.total}</td>
        </tr>`).join('');

    cont.innerHTML = `
        <div class="row g-2 mb-3">
            <div class="col-6 col-md-3">
                <div class="card kpi doppler p-2">
                    <div class="small text-secondary">Doppler</div>
                    <div class="fs-4 fw-bold">${data.doppler}</div>
                </div>
            </div>
            <div class="col-6 col-md-3">
                <div class="card kpi nodoppler p-2">
                    <div class="small text-secondary">No Doppler</div>
                    <div class="fs-4 fw-bold">${data.no_doppler}</div>
                </div>
            </div>
            <div class="col-6 col-md-3">
                <div class="card kpi total p-2">
                    <div class="small text-secondary">Total mes</div>
                    <div class="fs-4 fw-bold">${data.total}</div>
                </div>
            </div>
            <div class="col-6 col-md-3">
                <div class="card kpi p-2">
                    <div class="small text-secondary">Centro</div>
                    <div class="fs-6 fw-bold">${data.centro}</div>
                </div>
            </div>
        </div>

        <div class="row g-3">
            <div class="col-12 col-lg-7">
                <h6>Detalle por fecha</h6>
                <div class="table-responsive">
                    <table class="table table-sm table-bordered tabla-reporte">
                        <thead><tr><th>Fecha</th><th>Centro</th><th class="text-end">Doppler</th>
                        <th class="text-end">No Dop.</th><th class="text-end">Total</th></tr></thead>
                        <tbody>${filas || '<tr><td colspan="5" class="text-center text-secondary">Sin datos</td></tr>'}</tbody>
                    </table>
                </div>
            </div>
            <div class="col-12 col-lg-5">
                <h6>Estadística municipal</h6>
                <table class="table table-sm table-bordered tabla-reporte">
                    <thead><tr><th>Clasificación</th><th class="text-end">Total</th></tr></thead>
                    <tbody>${clasif}</tbody>
                </table>
            </div>
        </div>`;
}

function descargarExcel() {
    const url = '/api/reportes/excel?' + parametrosReporte();
    window.open(url, '_blank');
}

/* ------------------------------------------------------------------
   8) UTILIDADES VARIAS
   ------------------------------------------------------------------ */
function actualizarInfoPlantilla() {
    const sel = $('#tipoInforme');
    const val = sel.value;
    const info = $('#infoPlantilla');
    if (!val) {
        info.textContent = 'La plantilla .docx se completa con los marcadores {{variable}}.';
        return;
    }
    info.innerHTML = 'Se usará la plantilla <code>word_templates/' + val + '.docx</code>.';
}

function limpiarFormulario() {
    if (!confirm('¿Limpiar todos los campos del informe? (No borra lo ya guardado en BD)')) return;
    ['run', 'nombre', 'fechaNacimiento', 'edad', 'fur', 'profesionalSolicitante',
     'diagnosticoReferencia', 'informacionTecnica', 'exploracion', 'biometria',
     'impresionEcografica', 'sugerencias', 'edadGestacional', 'translucenciaNucal'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    $('#clasificacion').value = '';
    $('#tipoInforme').value = '';
    $('#dopplerNo').checked = true;
    $('#infoPaciente').innerHTML = '<span class="text-secondary">Ingrese el RUN para buscar historia previa.</span>';
    $('#btnCargarUltimoInforme').classList.add('d-none');
    $('#historial').innerHTML = '<div class="text-secondary small p-2">Ingrese un RUN para ver el historial.</div>';
    $('#previewAbis').textContent = 'El texto generado aparecerá aquí…';
    $('#resultadoBox').innerHTML = 'Genera el Word en <code>outputs/</code> y copia el texto al portapapeles.';
    actualizarInfoPlantilla();
}

/* ------------------------------------------------------------------
   9) INICIALIZACION Y EVENTOS
   ------------------------------------------------------------------ */
document.addEventListener('DOMContentLoaded', async function () {

    // Fecha de exploracion por defecto = hoy
    const hoy = new Date();
    const isoHoy = hoy.getFullYear() + '-' +
        String(hoy.getMonth() + 1).padStart(2, '0') + '-' +
        String(hoy.getDate()).padStart(2, '0');
    $('#fechaExploracion').value = isoHoy;

    await cargarContexto();
    llenarSelectoresReporte();
    actualizarInfoPlantilla();

    // --- Botones de centro (navbar y modal de bloqueo) ---
    $$('.btn-centro, [data-centro]').forEach(btn => {
        btn.addEventListener('click', (ev) => {
            ev.preventDefault();
            seleccionarCentro(btn.dataset.centro);
        });
    });

    // --- Busqueda de paciente por RUN (con retardo) ---
    $('#run').addEventListener('input', function () {
        clearTimeout(Estado.ultimoDebounce);
        Estado.ultimoDebounce = setTimeout(() => buscarPaciente(this.value.trim()), 350);
    });
    $('#btnBuscarPaciente').addEventListener('click', () => buscarPaciente($('#run').value.trim()));
    $('#btnCargarUltimoInforme').addEventListener('click', cargarUltimoInforme);

    // --- Edad automatica ---
    $('#fechaNacimiento').addEventListener('change', calcularEdadAutomatica);
    $('#fechaExploracion').addEventListener('change', calcularEdadAutomatica);

    // --- Botones rapidos "No informado" / "Sin orden" ---
    $$('[data-quick]').forEach(btn => {
        btn.addEventListener('click', () => {
            const campo = document.getElementById(btn.dataset.target);
            if (campo) campo.value = btn.dataset.quick;
        });
    });

    // --- Gestion de macros ---
    $$('[data-gestionar]').forEach(btn => {
        btn.addEventListener('click', () => abrirModalMacros(btn.dataset.gestionar));
    });
    $('#formMacro').addEventListener('submit', guardarMacro);
    $('#btnCancelarMacro').addEventListener('click', () => {
        $('#macroId').value = ''; $('#macroNombre').value = ''; $('#macroTexto').value = '';
    });

    // --- Generar informe ---
    $('#btnGenerar').addEventListener('click', generarInforme);

    // --- Copiar vista previa ---
    $('#btnCopiarPreview').addEventListener('click', async () => {
        const texto = $('#previewAbis').textContent;
        if (!texto || texto.startsWith('El texto generado')) {
            toast('Primero debe generar un informe.', 'warning');
            return;
        }
        const ok = await copiarAlPortapapeles(texto);
        toast(ok ? 'Texto copiado al portapapeles.' : 'No se pudo copiar.', ok ? 'success' : 'danger');
    });

    // --- Limpiar ---
    $('#btnLimpiar').addEventListener('click', limpiarFormulario);

    // --- Reportes ---
    $('#modalReportes').addEventListener('show.bs.modal', cargarResumenReporte);
    $('#repMes').addEventListener('change', cargarResumenReporte);
    $('#repAnio').addEventListener('change', cargarResumenReporte);
    $('#repCentro').addEventListener('change', cargarResumenReporte);
    $('#btnDescargarExcel').addEventListener('click', descargarExcel);

    // --- Atajos de teclado ---
    document.addEventListener('keydown', (ev) => {
        if (ev.ctrlKey && ev.key === 'Enter') {
            ev.preventDefault();
            generarInforme();
        }
    });
});
