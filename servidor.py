"""servidor.py - Enrutador HTTP. Valida sesión y rol en el servidor antes de llamar a servicios.py."""
import os
import sys
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import db
import seguridad
import servicios
import vistas

COOKIE = "sesion"
MAX_CUERPO = 64 * 1024
RUTA_ESTATICOS = Path(__file__).resolve().parent / "static"

# Contrato de rutas: ruta POST -> rol permitido.
ROLES_POST = {
    "/incidencias/nueva": "SOLICITANTE",
    "/incidencias/asignar": "COORDINADOR",
    "/incidencias/atender": "TECNICO",
    "/incidencias/validar": "SOLICITANTE",
}


class Manejador(BaseHTTPRequestHandler):
    server_version = "ReparaCampus/1.0"

    # ------------------------------------------------------------------ utilidades
    def _enviar(self, status, cuerpo, tipo="text/html; charset=utf-8", cabeceras=()):
        datos = cuerpo.encode("utf-8") if isinstance(cuerpo, str) else cuerpo
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(datos)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; frame-ancestors 'none'; form-action 'self'",
        )
        for nombre, valor in cabeceras:
            self.send_header(nombre, valor)
        self.end_headers()
        self.wfile.write(datos)

    def _redirigir(self, destino, cabeceras=()):
        self.send_response(303)
        self.send_header("Location", destino)
        self.send_header("Content-Length", "0")
        for nombre, valor in cabeceras:
            self.send_header(nombre, valor)
        self.end_headers()

    def _token(self):
        try:
            jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        except cookies.CookieError:
            return None
        morsel = jar.get(COOKIE)
        return morsel.value if morsel else None

    def _usuario_actual(self, conn):
        usuario_id = self.server.sesiones.obtener(self._token())
        if usuario_id is None:
            return None
        return conn.execute(
            "SELECT id, nombre, usuario, rol, activo FROM usuarios WHERE id = ? AND activo = 1",
            (usuario_id,),
        ).fetchone()

    def _leer_formulario(self):
        try:
            largo = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            largo = -1
        if largo < 0 or largo > MAX_CUERPO:
            return None
        cuerpo = self.rfile.read(largo).decode("utf-8", errors="replace")
        return {k: v[0] for k, v in parse_qs(cuerpo, keep_blank_values=True).items()}

    def _pagina_error(self, usuario, exc):
        titulos = {400: "Datos no válidos", 403: "Acceso denegado", 404: "No encontrado",
                   409: "Operación no permitida"}
        self._enviar(exc.http_status,
                     vistas.pagina_error(usuario, titulos.get(exc.http_status, "Error"), exc.mensajes))

    def _listado(self, conn, usuario, filtros, status=200, errores=(), valores=None):
        incidencias = servicios.listar_incidencias(
            conn, usuario, filtros.get("estado"), filtros.get("prioridad"))
        tecnicos = servicios.listar_tecnicos_activos(conn) if usuario["rol"] == "COORDINADOR" else []
        self._enviar(status, vistas.pagina_incidencias(
            usuario, incidencias, filtros, tecnicos, errores, valores))

    # ------------------------------------------------------------------ despacho
    def do_GET(self):
        self._atender("GET")

    def do_POST(self):
        self._atender("POST")

    def _atender(self, metodo):
        partes = urlsplit(self.path)
        ruta, consulta = partes.path, {k: v[0] for k, v in parse_qs(partes.query).items()}
        form = self._leer_formulario() if metodo == "POST" else {}
        if form is None:
            self._enviar(413, "Cuerpo demasiado grande", "text/plain; charset=utf-8")
            return
        conn = db.conectar(self.server.ruta_db)
        try:
            usuario = self._usuario_actual(conn)
            if metodo == "GET":
                self._get(conn, usuario, ruta, consulta)
            else:
                self._post(conn, usuario, ruta, form)
        except servicios.ErrorServicio as exc:
            self._pagina_error(usuario if "usuario" in locals() else None, exc)
        except Exception:  # error inesperado: no se filtran detalles al cliente
            self.log_error("Error interno en %s %s", metodo, self.path)
            self._enviar(500, "Error interno del servidor", "text/plain; charset=utf-8")
        finally:
            conn.close()

    def _get(self, conn, usuario, ruta, consulta):
        if ruta == "/static/estilos.css":
            self._enviar(200, (RUTA_ESTATICOS / "estilos.css").read_bytes(), "text/css; charset=utf-8")
        elif ruta == "/login":
            if usuario is not None:
                self._redirigir("/")
            else:
                self._enviar(200, vistas.pagina_login())
        elif usuario is None:
            self._redirigir("/login")
        elif ruta == "/":
            self._listado(conn, usuario, consulta)
        elif ruta == "/incidencias/detalle":
            detalle = servicios.detalle_incidencia(conn, usuario, consulta.get("id"))
            self._enviar(200, vistas.pagina_detalle(usuario, detalle))
        elif ruta == "/tablero":
            self._enviar(200, vistas.pagina_tablero(usuario, servicios.tablero(conn, usuario)))
        else:
            self._enviar(404, vistas.pagina_error(usuario, "No encontrado", ["La página no existe."]))

    def _post(self, conn, usuario, ruta, form):
        if ruta == "/login":
            return self._login(conn, form)
        if ruta == "/logout":
            self.server.sesiones.cerrar(self._token())
            return self._redirigir("/login", [("Set-Cookie", self._cookie("", 0))])
        if ruta not in ROLES_POST:
            return self._enviar(404, vistas.pagina_error(usuario, "No encontrado", ["La ruta no existe."]))
        if usuario is None:
            return self._redirigir("/login")
        if usuario["rol"] != ROLES_POST[ruta]:
            raise servicios.ErrorPermiso("No tiene permisos para realizar esta acción.")

        if ruta == "/incidencias/nueva":
            try:
                servicios.registrar_incidencia(conn, usuario, form)
            except servicios.ErrorValidacion as exc:
                return self._listado(conn, usuario, {}, 400, exc.mensajes, form)
            return self._redirigir("/")
        if ruta == "/incidencias/asignar":
            servicios.asignar_incidencia(conn, usuario, form.get("incidencia_id"), form.get("tecnico_id"))
            return self._redirigir("/")
        if ruta == "/incidencias/atender":
            servicios.atender(conn, usuario, form.get("incidencia_id"),
                              form.get("accion"), form.get("texto_solucion"))
        else:  # /incidencias/validar
            servicios.validar(conn, usuario, form.get("incidencia_id"),
                              form.get("accion"), form.get("motivo"))
        return self._redirigir("/incidencias/detalle?id=" + quote(str(form.get("incidencia_id", ""))))

    def _login(self, conn, form):
        usuario = servicios.autenticar(conn, form.get("usuario"), form.get("password"))
        if usuario is None:
            return self._enviar(401, vistas.pagina_login("Usuario o contraseña incorrectos."))
        token = self.server.sesiones.crear(usuario["id"])
        self._redirigir("/", [("Set-Cookie", self._cookie(token, seguridad.TTL_SESION_SEGUNDOS))])

    @staticmethod
    def _cookie(valor, max_age):
        return f"{COOKIE}={valor}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}"


def crear_servidor(ruta_db=None, host="127.0.0.1", puerto=8000):
    """Inicializa la base de datos y devuelve el servidor (sin arrancarlo)."""
    ruta_db = ruta_db or db.RUTA_POR_DEFECTO
    db.inicializar(ruta_db).close()
    servidor = ThreadingHTTPServer((host, puerto), Manejador)
    servidor.ruta_db = ruta_db
    servidor.sesiones = seguridad.GestorSesiones()
    return servidor


if __name__ == "__main__":
    puerto = int(os.environ.get("PORT", sys.argv[1] if len(sys.argv) > 1 else 8000))
    srv = crear_servidor(puerto=puerto)
    print(f"ReparaCampus en http://127.0.0.1:{puerto}  (Ctrl+C para detener)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()