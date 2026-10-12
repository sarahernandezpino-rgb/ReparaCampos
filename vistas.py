"""vistas.py - Construye el HTML. Todo texto que provenga del usuario pasa por e()."""
import html

ESTADOS = ("REGISTRADA", "ASIGNADA", "EN ATENCION", "PENDIENTE VALIDACION", "CERRADA")
PRIORIDADES = ("CRITICA", "ALTA", "NORMAL")
UBICACIONES = ("LAB-01", "AULA-201", "BIB-01")
CATEGORIAS = ("ELECTRICIDAD", "HIDRAULICA", "MOBILIARIO", "TIC")


def e(valor):
    """Escapa cualquier valor para mostrarlo como contenido, nunca como HTML o script."""
    return html.escape("" if valor is None else str(valor), quote=True)


def _layout(titulo, cuerpo, usuario=None):
    nav = ""
    if usuario is not None:
        enlaces = '<a href="/">Incidencias</a>'
        if usuario["rol"] == "COORDINADOR":
            enlaces += ' <a href="/tablero">Tablero</a>'
        nav = (
            f'<nav><span class="marca">ReparaCampus</span> {enlaces}'
            f'<span class="sesion">{e(usuario["nombre"])} ({e(usuario["rol"])})'
            '<form method="post" action="/logout" class="en-linea">'
            '<button type="submit">Salir</button></form></span></nav>'
        )
    return (
        '<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{e(titulo)} · ReparaCampus</title>"
        '<link rel="stylesheet" href="/static/estilos.css"></head>'
        f"<body>{nav}<main>{cuerpo}</main></body></html>"
    )


def _errores(errores):
    if not errores:
        return ""
    items = "".join(f"<li>{e(m)}</li>" for m in errores)
    return f'<div class="errores" role="alert"><ul>{items}</ul></div>'


def _opciones(valores, seleccionado=None, vacio=None):
    out = f'<option value="">{e(vacio)}</option>' if vacio is not None else ""
    for v in valores:
        marca = " selected" if v == seleccionado else ""
        out += f'<option value="{e(v)}"{marca}>{e(v)}</option>'
    return out


# --------------------------------------------------------------------------- páginas
def pagina_login(error=None):
    cuerpo = (
        "<h1>Iniciar sesión</h1>"
        + _errores([error] if error else [])
        + '<form method="post" action="/login" class="tarjeta">'
        '<label>Usuario <input name="usuario" required autocomplete="username"></label>'
        '<label>Contraseña <input name="password" type="password" required '
        'autocomplete="current-password"></label>'
        '<button type="submit">Entrar</button></form>'
    )
    return _layout("Ingreso", cuerpo)


def _formulario_nueva(valores):
    v = valores or {}
    riesgo = str(v.get("riesgo", ""))
    return (
        "<h2>Registrar incidencia</h2>"
        '<form method="post" action="/incidencias/nueva" class="tarjeta">'
        f'<label>Ubicación <select name="ubicacion" required>'
        f'{_opciones(UBICACIONES, v.get("ubicacion"), "Seleccione...")}</select></label>'
        f'<label>Categoría <select name="categoria" required>'
        f'{_opciones(CATEGORIAS, v.get("categoria"), "Seleccione...")}</select></label>'
        f'<label>Impacto <select name="impacto" required>'
        f'{_opciones(("BAJO", "ALTO"), v.get("impacto"), "Seleccione...")}</select></label>'
        '<label>¿Riesgo para las personas? <select name="riesgo" required>'
        '<option value="">Seleccione...</option>'
        f'<option value="1"{" selected" if riesgo == "1" else ""}>Sí</option>'
        f'<option value="0"{" selected" if riesgo == "0" else ""}>No</option></select></label>'
        '<label>Descripción (20 a 500 caracteres)'
        f'<textarea name="descripcion" rows="4" required>{e(v.get("descripcion", ""))}</textarea>'
        "</label><button type=\"submit\">Registrar</button></form>"
    )


def _formulario_asignar(inc, tecnicos):
    if inc["estado"] != "REGISTRADA":
        return ""
    opciones = "".join(f'<option value="{e(t["id"])}">{e(t["nombre"])}</option>' for t in tecnicos)
    return (
        '<form method="post" action="/incidencias/asignar" class="en-linea">'
        f'<input type="hidden" name="incidencia_id" value="{e(inc["id"])}">'
        f'<select name="tecnico_id" required>{opciones}</select>'
        '<button type="submit">Asignar</button></form>'
    )


def pagina_incidencias(usuario, incidencias, filtros=None, tecnicos=(), errores=(), valores=None):
    f = filtros or {}
    filtro = (
        '<form method="get" action="/" class="en-linea filtros">'
        f'<label>Estado <select name="estado">{_opciones(ESTADOS, f.get("estado"), "Todos")}'
        "</select></label>"
        f'<label>Prioridad <select name="prioridad">'
        f'{_opciones(PRIORIDADES, f.get("prioridad"), "Todas")}</select></label>'
        '<button type="submit">Filtrar</button></form>'
    )
    es_coord = usuario["rol"] == "COORDINADOR"
    filas = ""
    for i in incidencias:
        accion = _formulario_asignar(i, tecnicos) if es_coord else ""
        filas += (
            f'<tr><td><a href="/incidencias/detalle?id={e(i["id"])}">{e(i["codigo"])}</a></td>'
            f'<td>{e(i["ubicacion"])}</td><td>{e(i["categoria"])}</td>'
            f'<td class="prio-{e(i["prioridad"])}">{e(i["prioridad"])}</td>'
            f'<td>{e(i["estado"])}</td><td>{e(i["solicitante_nombre"])}</td>'
            f'<td>{e(i["tecnico_nombre"] or "—")}</td><td>{accion}</td></tr>'
        )
    if not filas:
        filas = '<tr><td colspan="8">No hay incidencias para mostrar.</td></tr>'
    tabla = (
        '<div class="tabla"><table><thead><tr><th>Código</th><th>Ubicación</th><th>Categoría</th>'
        "<th>Prioridad</th><th>Estado</th><th>Solicitante</th><th>Técnico</th><th></th></tr>"
        f"</thead><tbody>{filas}</tbody></table></div>"
    )
    cuerpo = "<h1>Incidencias</h1>" + _errores(errores) + filtro + tabla
    if usuario["rol"] == "SOLICITANTE":
        cuerpo += _formulario_nueva(valores)
    return _layout("Incidencias", cuerpo, usuario)


def _acciones_detalle(usuario, inc):
    estado, rol = inc["estado"], usuario["rol"]
    id_ = e(inc["id"])
    if rol == "TECNICO" and inc["tecnico_id"] == usuario["id"]:
        if estado == "ASIGNADA":
            return (
                '<form method="post" action="/incidencias/atender" class="tarjeta">'
                f'<input type="hidden" name="incidencia_id" value="{id_}">'
                '<input type="hidden" name="accion" value="INICIAR">'
                '<button type="submit">Iniciar atención</button></form>'
            )
        if estado == "EN ATENCION":
            return (
                '<form method="post" action="/incidencias/atender" class="tarjeta">'
                f'<input type="hidden" name="incidencia_id" value="{id_}">'
                '<input type="hidden" name="accion" value="SOLUCION">'
                '<label>Solución aplicada (20 a 800 caracteres)'
                '<textarea name="texto_solucion" rows="4" required></textarea></label>'
                '<button type="submit">Registrar solución</button></form>'
            )
    if rol == "SOLICITANTE" and inc["solicitante_id"] == usuario["id"]:
        base = (
            '<form method="post" action="/incidencias/validar" class="tarjeta">'
            f'<input type="hidden" name="incidencia_id" value="{id_}">'
        )
        if estado == "PENDIENTE VALIDACION":
            return (
                base + '<input type="hidden" name="accion" value="CONFIRMAR">'
                '<button type="submit">Confirmar solución</button></form>'
                + base + '<input type="hidden" name="accion" value="RECHAZAR">'
                '<label>Motivo del rechazo (10 a 300 caracteres)'
                '<textarea name="motivo" rows="3" required></textarea></label>'
                '<button type="submit">Rechazar solución</button></form>'
            )
        if estado == "CERRADA":
            return (
                base + '<input type="hidden" name="accion" value="REABRIR">'
                '<label>Motivo de la reapertura (10 a 300 caracteres; hasta 48 h después del cierre)'
                '<textarea name="motivo" rows="3" required></textarea></label>'
                '<button type="submit">Reabrir incidencia</button></form>'
            )
    return ""


def pagina_detalle(usuario, detalle, errores=()):
    inc = detalle["incidencia"]
    ficha = (
        f'<h1>{e(inc["codigo"])}</h1><dl class="ficha">'
        f'<dt>Ubicación</dt><dd>{e(inc["ubicacion"])}</dd>'
        f'<dt>Categoría</dt><dd>{e(inc["categoria"])}</dd>'
        f'<dt>Impacto</dt><dd>{e(inc["impacto"])}</dd>'
        f'<dt>Riesgo para personas</dt><dd>{"Sí" if inc["riesgo"] else "No"}</dd>'
        f'<dt>Prioridad</dt><dd>{e(inc["prioridad"])}</dd>'
        f'<dt>Estado</dt><dd>{e(inc["estado"])}</dd>'
        f'<dt>Solicitante</dt><dd>{e(inc["solicitante_nombre"])}</dd>'
        f'<dt>Técnico</dt><dd>{e(inc["tecnico_nombre"] or "—")}</dd>'
        f'<dt>Creada (UTC)</dt><dd>{e(inc["creada_en"])}</dd>'
        f'<dt>Último cierre (UTC)</dt><dd>{e(inc["cerrada_en"] or "—")}</dd></dl>'
        f'<h2>Descripción</h2><p class="texto">{e(inc["descripcion"])}</p>'
    )
    sols = "".join(
        f'<tr><td>{e(s["registrada_en"])}</td><td>{e(s["tecnico_nombre"])}</td>'
        f'<td class="texto">{e(s["texto"])}</td><td>{e(s["resultado"])}</td>'
        f'<td class="texto">{e(s["motivo_rechazo"] or "")}</td></tr>'
        for s in detalle["soluciones"]
    ) or '<tr><td colspan="5">Sin soluciones registradas.</td></tr>'
    hist = "".join(
        f'<tr><td>{e(h["fecha"])}</td><td>{e(h["usuario_nombre"])} ({e(h["usuario_rol"])})</td>'
        f'<td>{e(h["accion"])}</td><td>{e(h["estado_anterior"] or "—")} → {e(h["estado_nuevo"])}</td>'
        f'<td class="texto">{e(h["motivo"] or "")}</td></tr>'
        for h in detalle["historial"]
    )
    cuerpo = (
        _errores(errores) + ficha + _acciones_detalle(usuario, inc)
        + '<h2>Soluciones</h2><div class="tabla"><table><thead><tr><th>Fecha (UTC)</th>'
        "<th>Técnico</th><th>Solución</th><th>Resultado</th><th>Motivo de rechazo</th></tr></thead>"
        f"<tbody>{sols}</tbody></table></div>"
        '<h2>Historial</h2><div class="tabla"><table><thead><tr><th>Fecha (UTC)</th><th>Autor</th>'
        "<th>Acción</th><th>Transición</th><th>Motivo</th></tr></thead>"
        f"<tbody>{hist}</tbody></table></div>"
    )
    return _layout(inc["codigo"], cuerpo, usuario)


def pagina_tablero(usuario, t):
    def tarjetas(datos):
        return "".join(
            f'<div class="metrica"><span>{e(k)}</span><strong>{e(v)}</strong></div>'
            for k, v in datos.items()
        )

    criticas = "".join(
        f'<tr><td><a href="/incidencias/detalle?id={e(i["id"])}">{e(i["codigo"])}</a></td>'
        f'<td>{e(i["ubicacion"])}</td><td>{e(i["estado"])}</td>'
        f'<td>{e(i["tecnico_nombre"] or "—")}</td></tr>'
        for i in t["criticas_abiertas"]
    ) or '<tr><td colspan="4">No hay incidencias críticas sin cerrar.</td></tr>'
    cuerpo = (
        "<h1>Tablero de seguimiento</h1>"
        f'<div class="metricas"><div class="metrica destacada"><span>Porcentaje de cierre</span>'
        f'<strong>{e(t["porcentaje_cierre_texto"])}</strong></div>'
        f'<div class="metrica"><span>Total</span><strong>{e(t["total"])}</strong></div></div>'
        f'<h2>Por estado</h2><div class="metricas">{tarjetas(t["por_estado"])}</div>'
        f'<h2>Por prioridad</h2><div class="metricas">{tarjetas(t["por_prioridad"])}</div>'
        '<h2>Incidencias críticas no cerradas</h2><div class="tabla"><table><thead><tr>'
        "<th>Código</th><th>Ubicación</th><th>Estado</th><th>Técnico</th></tr></thead>"
        f"<tbody>{criticas}</tbody></table></div>"
    )
    return _layout("Tablero", cuerpo, usuario)


def pagina_error(usuario, titulo, mensajes):
    cuerpo = f"<h1>{e(titulo)}</h1>" + _errores(mensajes) + '<p><a href="/">Volver</a></p>'
    return _layout(titulo, cuerpo, usuario)