"""servicios.py - Reglas de negocio de ReparaCampus (SPEC S01 a S05).

No depende de HTTP. Toda función recibe la conexión y el usuario autenticado
(sqlite3.Row o dict con 'id' y 'rol'). Cada operación que escribe se ejecuta en una
única transacción: si algo falla, no queda ningún cambio parcial.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import seguridad

UBICACIONES = ("LAB-01", "AULA-201", "BIB-01")
CATEGORIAS = ("ELECTRICIDAD", "HIDRAULICA", "MOBILIARIO", "TIC")
IMPACTOS = ("BAJO", "ALTO")
PRIORIDADES = ("CRITICA", "ALTA", "NORMAL")
ESTADOS = ("REGISTRADA", "ASIGNADA", "EN ATENCION", "PENDIENTE VALIDACION", "CERRADA")

DESCRIPCION_MIN, DESCRIPCION_MAX = 20, 500
SOLUCION_MIN, SOLUCION_MAX = 20, 800
MOTIVO_MIN, MOTIVO_MAX = 10, 300
PLAZO_REAPERTURA = timedelta(hours=48)


# --------------------------------------------------------------------------- errores
class ErrorServicio(Exception):
    http_status = 400

    def __init__(self, mensajes):
        if isinstance(mensajes, str):
            mensajes = [mensajes]
        self.mensajes = list(mensajes)
        super().__init__("; ".join(self.mensajes))


class ErrorValidacion(ErrorServicio):
    http_status = 400


class ErrorPermiso(ErrorServicio):
    http_status = 403


class ErrorNoEncontrado(ErrorServicio):
    http_status = 404


class ErrorEstado(ErrorServicio):
    """La operación no es válida para el estado actual (o venció un plazo)."""
    http_status = 409


# --------------------------------------------------------------------------- utilidades
def ahora_utc():
    return datetime.now(timezone.utc)


def a_texto(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def de_texto(texto):
    dt = datetime.fromisoformat(texto)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


@contextmanager
def transaccion(conn):
    """BEGIN IMMEDIATE ... COMMIT; ante cualquier excepción hace ROLLBACK y la relanza."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _exigir_rol(usuario, rol):
    if usuario is None or usuario["rol"] != rol:
        raise ErrorPermiso("No tiene permisos para realizar esta acción.")


def _texto(valor):
    return "" if valor is None else str(valor).strip()


def _entero(valor, nombre):
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        raise ErrorValidacion(f"El campo '{nombre}' debe ser un número entero.")


def _parsear_riesgo(valor):
    """Devuelve 1, 0 o None si no es un booleano reconocible (sí/no, 1/0)."""
    if isinstance(valor, bool):
        return int(valor)
    if isinstance(valor, int):
        return valor if valor in (0, 1) else None
    texto = str(valor).strip().lower()
    if texto in ("1", "si", "sí", "true"):
        return 1
    if texto in ("0", "no", "false"):
        return 0
    return None


def _cargar_incidencia(conn, incidencia_id):
    fila = conn.execute("SELECT * FROM incidencias WHERE id = ?", (incidencia_id,)).fetchone()
    if fila is None:
        raise ErrorNoEncontrado("La incidencia no existe.")
    return fila


def _insertar_historial(conn, incidencia_id, usuario_id, accion, anterior, nuevo, motivo, fecha):
    conn.execute(
        "INSERT INTO historial (incidencia_id, usuario_id, accion, estado_anterior, "
        "estado_nuevo, motivo, fecha) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (incidencia_id, usuario_id, accion, anterior, nuevo, motivo, fecha),
    )


def _validar_longitud(valor, minimo, maximo, nombre):
    texto = _texto(valor)
    if not (minimo <= len(texto) <= maximo):
        raise ErrorValidacion(f"{nombre} debe tener entre {minimo} y {maximo} caracteres.")
    return texto


# --------------------------------------------------------------------------- autenticación
def autenticar(conn, nombre_usuario, password):
    """Devuelve la fila del usuario activo si las credenciales son correctas; si no, None."""
    fila = conn.execute(
        "SELECT * FROM usuarios WHERE usuario = ? AND activo = 1", (_texto(nombre_usuario),)
    ).fetchone()
    if fila is None:
        seguridad.verificacion_falsa(password or "")
        return None
    if seguridad.verificar_password(password or "", fila["password_hash"]):
        return fila
    return None


# --------------------------------------------------------------------------- S01 / S02
def calcular_prioridad(riesgo, impacto):
    if riesgo == 1:
        return "CRITICA"
    return "ALTA" if impacto == "ALTO" else "NORMAL"


def validar_datos_incidencia(datos):
    """Recorta espacios, valida dominios y longitudes. Devuelve los datos limpios."""
    errores, limpio = [], {}
    for campo in ("ubicacion", "categoria", "impacto", "descripcion"):
        limpio[campo] = _texto(datos.get(campo))
        if not limpio[campo]:
            errores.append(f"El campo '{campo}' es obligatorio.")

    bruto = datos.get("riesgo")
    if bruto is None or (isinstance(bruto, str) and not bruto.strip()):
        errores.append("El campo 'riesgo' es obligatorio.")
    else:
        limpio["riesgo"] = _parsear_riesgo(bruto)
        if limpio["riesgo"] is None:
            errores.append("El campo 'riesgo' debe ser un valor sí/no (1/0).")

    if limpio["ubicacion"] and limpio["ubicacion"] not in UBICACIONES:
        errores.append("Ubicación no permitida (LAB-01, AULA-201 o BIB-01).")
    if limpio["categoria"] and limpio["categoria"] not in CATEGORIAS:
        errores.append("Categoría no permitida (ELECTRICIDAD, HIDRAULICA, MOBILIARIO o TIC).")
    if limpio["impacto"] and limpio["impacto"] not in IMPACTOS:
        errores.append("Impacto no permitido (BAJO o ALTO).")
    if limpio["descripcion"] and not (
        DESCRIPCION_MIN <= len(limpio["descripcion"]) <= DESCRIPCION_MAX
    ):
        errores.append(
            f"La descripción debe tener entre {DESCRIPCION_MIN} y {DESCRIPCION_MAX} caracteres."
        )
    if errores:
        raise ErrorValidacion(errores)
    limpio["prioridad"] = calcular_prioridad(limpio["riesgo"], limpio["impacto"])
    return limpio


def registrar_incidencia(conn, usuario, datos, ahora=None):
    """S01: valida, calcula prioridad, genera INC-XXXX e inserta incidencia + historial."""
    _exigir_rol(usuario, "SOLICITANTE")
    d = validar_datos_incidencia(datos)
    marca = a_texto(ahora or ahora_utc())
    with transaccion(conn):
        numero = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM incidencias").fetchone()[0]
        conn.execute(
            "INSERT INTO incidencias (id, codigo, ubicacion, categoria, descripcion, impacto, "
            "riesgo, prioridad, estado, solicitante_id, creada_en) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'REGISTRADA', ?, ?)",
            (numero, f"INC-{numero:04d}", d["ubicacion"], d["categoria"], d["descripcion"],
             d["impacto"], d["riesgo"], d["prioridad"], usuario["id"], marca),
        )
        _insertar_historial(conn, numero, usuario["id"], "REGISTRO", None, "REGISTRADA", None, marca)
    return dict(_cargar_incidencia(conn, numero))


def listar_tecnicos_activos(conn):
    return [dict(f) for f in conn.execute(
        "SELECT id, nombre, usuario FROM usuarios WHERE rol = 'TECNICO' AND activo = 1 "
        "ORDER BY nombre"
    )]


def asignar_incidencia(conn, usuario, incidencia_id, tecnico_id, ahora=None):
    """S02: solo el coordinador asigna incidencias REGISTRADA a técnicos activos."""
    _exigir_rol(usuario, "COORDINADOR")
    incidencia_id = _entero(incidencia_id, "incidencia_id")
    tecnico_id = _entero(tecnico_id, "tecnico_id")
    marca = a_texto(ahora or ahora_utc())
    with transaccion(conn):
        inc = _cargar_incidencia(conn, incidencia_id)
        if inc["estado"] != "REGISTRADA":
            raise ErrorEstado("Solo se pueden asignar incidencias en estado REGISTRADA.")
        tec = conn.execute("SELECT * FROM usuarios WHERE id = ?", (tecnico_id,)).fetchone()
        if tec is None or tec["rol"] != "TECNICO":
            raise ErrorValidacion("El usuario seleccionado no es un técnico.")
        if not tec["activo"]:
            raise ErrorValidacion("El técnico seleccionado está inactivo.")
        conn.execute(
            "UPDATE incidencias SET tecnico_id = ?, estado = 'ASIGNADA' WHERE id = ?",
            (tecnico_id, incidencia_id),
        )
        _insertar_historial(conn, incidencia_id, usuario["id"], "ASIGNACION",
                            "REGISTRADA", "ASIGNADA", None, marca)
    return dict(_cargar_incidencia(conn, incidencia_id))


# --------------------------------------------------------------------------- S03
def _cargar_para_tecnico(conn, usuario, incidencia_id):
    _exigir_rol(usuario, "TECNICO")
    inc = _cargar_incidencia(conn, incidencia_id)
    if inc["tecnico_id"] != usuario["id"]:
        raise ErrorPermiso("La incidencia no está asignada a este técnico.")
    return inc


def iniciar_atencion(conn, usuario, incidencia_id, ahora=None):
    """S03: ASIGNADA -> EN ATENCION por el técnico asignado."""
    incidencia_id = _entero(incidencia_id, "incidencia_id")
    marca = a_texto(ahora or ahora_utc())
    with transaccion(conn):
        inc = _cargar_para_tecnico(conn, usuario, incidencia_id)
        if inc["estado"] != "ASIGNADA":
            raise ErrorEstado("Solo se puede iniciar la atención de una incidencia ASIGNADA.")
        conn.execute("UPDATE incidencias SET estado = 'EN ATENCION' WHERE id = ?", (incidencia_id,))
        _insertar_historial(conn, incidencia_id, usuario["id"], "INICIO",
                            "ASIGNADA", "EN ATENCION", None, marca)
    return dict(_cargar_incidencia(conn, incidencia_id))


def registrar_solucion(conn, usuario, incidencia_id, texto, ahora=None):
    """S03: EN ATENCION -> PENDIENTE VALIDACION, guardando la solución (20 a 800 caracteres)."""
    incidencia_id = _entero(incidencia_id, "incidencia_id")
    marca = a_texto(ahora or ahora_utc())
    with transaccion(conn):
        inc = _cargar_para_tecnico(conn, usuario, incidencia_id)
        if inc["estado"] != "EN ATENCION":
            raise ErrorEstado("Solo se puede registrar la solución de una incidencia EN ATENCION.")
        texto = _validar_longitud(texto, SOLUCION_MIN, SOLUCION_MAX, "La solución")
        conn.execute(
            "INSERT INTO soluciones (incidencia_id, tecnico_id, texto, registrada_en, resultado) "
            "VALUES (?, ?, ?, ?, 'PENDIENTE')",
            (incidencia_id, usuario["id"], texto, marca),
        )
        conn.execute(
            "UPDATE incidencias SET estado = 'PENDIENTE VALIDACION' WHERE id = ?", (incidencia_id,)
        )
        _insertar_historial(conn, incidencia_id, usuario["id"], "SOLUCION",
                            "EN ATENCION", "PENDIENTE VALIDACION", None, marca)
    return dict(_cargar_incidencia(conn, incidencia_id))


def atender(conn, usuario, incidencia_id, accion, texto_solucion=None, ahora=None):
    """Despachador de /incidencias/atender (accion = INICIAR | SOLUCION)."""
    accion = _texto(accion).upper()
    if accion == "INICIAR":
        return iniciar_atencion(conn, usuario, incidencia_id, ahora)
    if accion == "SOLUCION":
        return registrar_solucion(conn, usuario, incidencia_id, texto_solucion, ahora)
    raise ErrorValidacion("Acción no válida (INICIAR o SOLUCION).")


# --------------------------------------------------------------------------- S04
def _cargar_para_solicitante(conn, usuario, incidencia_id):
    _exigir_rol(usuario, "SOLICITANTE")
    inc = _cargar_incidencia(conn, incidencia_id)
    if inc["solicitante_id"] != usuario["id"]:
        raise ErrorPermiso("Solo el solicitante que registró la incidencia puede validarla.")
    return inc


def confirmar_solucion(conn, usuario, incidencia_id, ahora=None):
    """S04: PENDIENTE VALIDACION -> CERRADA, guardando cerrada_en en UTC."""
    incidencia_id = _entero(incidencia_id, "incidencia_id")
    marca = a_texto(ahora or ahora_utc())
    with transaccion(conn):
        inc = _cargar_para_solicitante(conn, usuario, incidencia_id)
        if inc["estado"] != "PENDIENTE VALIDACION":
            raise ErrorEstado("Solo se puede confirmar una incidencia PENDIENTE VALIDACION.")
        conn.execute(
            "UPDATE incidencias SET estado = 'CERRADA', cerrada_en = ? WHERE id = ?",
            (marca, incidencia_id),
        )
        conn.execute(
            "UPDATE soluciones SET resultado = 'CONFIRMADA' WHERE id = ("
            "SELECT MAX(id) FROM soluciones WHERE incidencia_id = ? AND resultado = 'PENDIENTE')",
            (incidencia_id,),
        )
        _insertar_historial(conn, incidencia_id, usuario["id"], "CONFIRMACION",
                            "PENDIENTE VALIDACION", "CERRADA", None, marca)
    return dict(_cargar_incidencia(conn, incidencia_id))


def rechazar_solucion(conn, usuario, incidencia_id, motivo, ahora=None):
    """S04: PENDIENTE VALIDACION -> EN ATENCION con el mismo técnico; conserva la solución."""
    incidencia_id = _entero(incidencia_id, "incidencia_id")
    marca = a_texto(ahora or ahora_utc())
    with transaccion(conn):
        inc = _cargar_para_solicitante(conn, usuario, incidencia_id)
        if inc["estado"] != "PENDIENTE VALIDACION":
            raise ErrorEstado("Solo se puede rechazar una incidencia PENDIENTE VALIDACION.")
        motivo = _validar_longitud(motivo, MOTIVO_MIN, MOTIVO_MAX, "El motivo")
        conn.execute(
            "UPDATE soluciones SET resultado = 'RECHAZADA', motivo_rechazo = ? WHERE id = ("
            "SELECT MAX(id) FROM soluciones WHERE incidencia_id = ? AND resultado = 'PENDIENTE')",
            (motivo, incidencia_id),
        )
        conn.execute("UPDATE incidencias SET estado = 'EN ATENCION' WHERE id = ?", (incidencia_id,))
        _insertar_historial(conn, incidencia_id, usuario["id"], "RECHAZO",
                            "PENDIENTE VALIDACION", "EN ATENCION", motivo, marca)
    return dict(_cargar_incidencia(conn, incidencia_id))


def reabrir_incidencia(conn, usuario, incidencia_id, motivo, ahora=None):
    """S04: CERRADA -> EN ATENCION si pasaron <= 48.0 h desde cerrada_en (UTC)."""
    incidencia_id = _entero(incidencia_id, "incidencia_id")
    ahora = ahora or ahora_utc()
    with transaccion(conn):
        inc = _cargar_para_solicitante(conn, usuario, incidencia_id)
        if inc["estado"] != "CERRADA":
            raise ErrorEstado("Solo se puede reabrir una incidencia CERRADA.")
        motivo = _validar_longitud(motivo, MOTIVO_MIN, MOTIVO_MAX, "El motivo")
        if ahora - de_texto(inc["cerrada_en"]) > PLAZO_REAPERTURA:
            raise ErrorEstado("El plazo de 48 horas para reabrir la incidencia ya venció.")
        conn.execute("UPDATE incidencias SET estado = 'EN ATENCION' WHERE id = ?", (incidencia_id,))
        _insertar_historial(conn, incidencia_id, usuario["id"], "REAPERTURA",
                            "CERRADA", "EN ATENCION", motivo, a_texto(ahora))
    return dict(_cargar_incidencia(conn, incidencia_id))


def validar(conn, usuario, incidencia_id, accion, motivo=None, ahora=None):
    """Despachador de /incidencias/validar (accion = CONFIRMAR | RECHAZAR | REABRIR)."""
    accion = _texto(accion).upper()
    if accion == "CONFIRMAR":
        return confirmar_solucion(conn, usuario, incidencia_id, ahora)
    if accion == "RECHAZAR":
        return rechazar_solucion(conn, usuario, incidencia_id, motivo, ahora)
    if accion == "REABRIR":
        return reabrir_incidencia(conn, usuario, incidencia_id, motivo, ahora)
    raise ErrorValidacion("Acción no válida (CONFIRMAR, RECHAZAR o REABRIR).")


# --------------------------------------------------------------------------- S05
_SELECT_INCIDENCIAS = (
    "SELECT i.*, s.nombre AS solicitante_nombre, t.nombre AS tecnico_nombre "
    "FROM incidencias i JOIN usuarios s ON s.id = i.solicitante_id "
    "LEFT JOIN usuarios t ON t.id = i.tecnico_id"
)


def _filtro_por_rol(usuario):
    rol = usuario["rol"] if usuario is not None else None
    if rol == "SOLICITANTE":
        return "i.solicitante_id = ?", [usuario["id"]]
    if rol == "TECNICO":
        return "i.tecnico_id = ?", [usuario["id"]]
    if rol == "COORDINADOR":
        return None, []
    raise ErrorPermiso("Usuario sin permisos de consulta.")


def listar_incidencias(conn, usuario, estado=None, prioridad=None):
    """S05: consulta filtrada por rol y, opcionalmente, por estado y prioridad."""
    estado, prioridad = _texto(estado), _texto(prioridad)
    if estado and estado not in ESTADOS:
        raise ErrorValidacion("Filtro de estado no válido.")
    if prioridad and prioridad not in PRIORIDADES:
        raise ErrorValidacion("Filtro de prioridad no válido.")
    condicion, params = _filtro_por_rol(usuario)
    where = [condicion] if condicion else []
    if estado:
        where.append("i.estado = ?")
        params.append(estado)
    if prioridad:
        where.append("i.prioridad = ?")
        params.append(prioridad)
    sql = _SELECT_INCIDENCIAS + (" WHERE " + " AND ".join(where) if where else "")
    return [dict(f) for f in conn.execute(sql + " ORDER BY i.id DESC", params)]


def detalle_incidencia(conn, usuario, incidencia_id):
    """S05: incidencia + soluciones + historial cronológico, si el usuario puede verla."""
    incidencia_id = _entero(incidencia_id, "id")
    condicion, params = _filtro_por_rol(usuario)
    sql = _SELECT_INCIDENCIAS + " WHERE i.id = ?" + (f" AND {condicion}" if condicion else "")
    fila = conn.execute(sql, [incidencia_id] + params).fetchone()
    if fila is None:  # no existe o es ajena: no se revela la diferencia
        raise ErrorNoEncontrado("La incidencia no existe.")
    historial = [dict(f) for f in conn.execute(
        "SELECT h.*, u.nombre AS usuario_nombre, u.rol AS usuario_rol FROM historial h "
        "JOIN usuarios u ON u.id = h.usuario_id WHERE h.incidencia_id = ? "
        "ORDER BY h.fecha, h.id", (incidencia_id,))]
    soluciones = [dict(f) for f in conn.execute(
        "SELECT s.*, u.nombre AS tecnico_nombre FROM soluciones s "
        "JOIN usuarios u ON u.id = s.tecnico_id WHERE s.incidencia_id = ? "
        "ORDER BY s.id", (incidencia_id,))]
    return {"incidencia": dict(fila), "historial": historial, "soluciones": soluciones}


def formatear_porcentaje(cerradas, total):
    """Un decimal con coma; '0,0 %' si no hay incidencias."""
    if total == 0:
        return "0,0 %"
    return f"{cerradas / total * 100:.1f}".replace(".", ",") + " %"


def tablero(conn, usuario):
    """S05: métricas del coordinador."""
    _exigir_rol(usuario, "COORDINADOR")
    por_estado = {e: 0 for e in ESTADOS}
    for f in conn.execute("SELECT estado, COUNT(*) AS n FROM incidencias GROUP BY estado"):
        por_estado[f["estado"]] = f["n"]
    por_prioridad = {p: 0 for p in PRIORIDADES}
    for f in conn.execute("SELECT prioridad, COUNT(*) AS n FROM incidencias GROUP BY prioridad"):
        por_prioridad[f["prioridad"]] = f["n"]
    criticas = [dict(f) for f in conn.execute(
        _SELECT_INCIDENCIAS + " WHERE i.prioridad = 'CRITICA' AND i.estado <> 'CERRADA' "
        "ORDER BY i.id")]
    total = sum(por_estado.values())
    cerradas = por_estado["CERRADA"]
    return {
        "total": total,
        "por_estado": por_estado,
        "por_prioridad": por_prioridad,
        "criticas_abiertas": criticas,
        "porcentaje_cierre": (cerradas / total * 100) if total else 0.0,
        "porcentaje_cierre_texto": formatear_porcentaje(cerradas, total),
    }