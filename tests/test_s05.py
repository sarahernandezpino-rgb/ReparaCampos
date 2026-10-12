"""Pruebas de la SPEC S05: Consultar e historizar (CA-S05-01 a CA-S05-06, más BD, seguridad y permisos HTTP).

Archivo independiente: incluye su propia base de pruebas (BD en memoria con las cuentas demo).
Ejecutar desde la carpeta del proyecto:
    python -m unittest tests.test_s05 -v   (o)   pytest tests/test_s05.py -v
"""
import http.client
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import seguridad  # noqa: E402
import servicios as sv  # noqa: E402
import servidor  # noqa: E402

seguridad.ITERACIONES = 1000  # pruebas rápidas; el valor real se usa fuera de las pruebas

DESC_OK = "El tomacorriente del laboratorio no funciona"  # > 20 caracteres
SOL_OK = "Se reemplazó el tomacorriente y se verificó el voltaje."
MOTIVO_OK = "La falla persiste después de la reparación."
T0 = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)


class BaseTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.inicializar(":memory:")
        self.sol1 = self.usuario("solicitante1")
        self.sol2 = self.usuario("solicitante2")
        self.coord = self.usuario("coordinador1")
        self.tec1 = self.usuario("tecnico1")
        self.tec2 = self.usuario("tecnico2")

    def tearDown(self):
        self.conn.close()

    def usuario(self, nombre):
        return dict(self.conn.execute("SELECT * FROM usuarios WHERE usuario = ?", (nombre,)).fetchone())

    def datos(self, **cambios):
        d = {"ubicacion": "LAB-01", "categoria": "TIC", "impacto": "ALTO",
             "riesgo": "0", "descripcion": DESC_OK}
        d.update(cambios)
        return d

    def registrar(self, solicitante=None, **cambios):
        return sv.registrar_incidencia(self.conn, solicitante or self.sol1, self.datos(**cambios), T0)

    def llevar_a(self, estado, **cambios):
        """Lleva una incidencia nueva hasta el estado indicado con tecnico1."""
        inc = self.registrar(**cambios)
        pasos = ["REGISTRADA", "ASIGNADA", "EN ATENCION", "PENDIENTE VALIDACION", "CERRADA"]
        hasta = pasos.index(estado)
        i = inc["id"]
        if hasta >= 1:
            sv.asignar_incidencia(self.conn, self.coord, i, self.tec1["id"], T0)
        if hasta >= 2:
            sv.iniciar_atencion(self.conn, self.tec1, i, T0)
        if hasta >= 3:
            sv.registrar_solucion(self.conn, self.tec1, i, SOL_OK, T0)
        if hasta >= 4:
            sv.confirmar_solucion(self.conn, self.sol1, i, T0)
        return i

    def fila(self, i):
        return dict(self.conn.execute("SELECT * FROM incidencias WHERE id = ?", (i,)).fetchone())

    def cuenta(self, tabla):
        return self.conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]


class TestS05Consultar(BaseTest):
    def test_CA_S05_01_visibilidad_por_rol(self):
        a = self.registrar(self.sol1)["id"]
        b = self.registrar(self.sol2)["id"]
        sv.asignar_incidencia(self.conn, self.coord, b, self.tec1["id"], T0)
        ids = lambda u: sorted(r["id"] for r in sv.listar_incidencias(self.conn, u))  # noqa: E731
        self.assertEqual(ids(self.sol1), [a])
        self.assertEqual(ids(self.sol2), [b])
        self.assertEqual(ids(self.tec1), [b])
        self.assertEqual(ids(self.tec2), [])
        self.assertEqual(ids(self.coord), [a, b])
        with self.assertRaises(sv.ErrorNoEncontrado):  # detalle de una incidencia ajena
            sv.detalle_incidencia(self.conn, self.sol1, b)
        with self.assertRaises(sv.ErrorNoEncontrado):
            sv.detalle_incidencia(self.conn, self.tec2, b)
        sv.detalle_incidencia(self.conn, self.coord, a)

    def test_CA_S05_02_filtros_combinados(self):
        critica = self.llevar_a("EN ATENCION", riesgo="1")
        self.llevar_a("EN ATENCION", riesgo="0")
        self.registrar(riesgo="1")  # crítica pero REGISTRADA
        r = sv.listar_incidencias(self.conn, self.coord, "EN ATENCION", "CRITICA")
        self.assertEqual([x["id"] for x in r], [critica])
        self.assertEqual(len(sv.listar_incidencias(self.conn, self.coord, "", "CRITICA")), 2)
        with self.assertRaises(sv.ErrorValidacion):
            sv.listar_incidencias(self.conn, self.coord, "INVENTADO", None)

    def test_CA_S05_03_historial_completo_cronologico(self):
        i = self.registrar(riesgo="1")["id"]
        h = lambda n: T0 + timedelta(hours=n)  # noqa: E731
        sv.asignar_incidencia(self.conn, self.coord, i, self.tec1["id"], h(1))
        sv.iniciar_atencion(self.conn, self.tec1, i, h(2))
        sv.registrar_solucion(self.conn, self.tec1, i, SOL_OK, h(3))
        sv.rechazar_solucion(self.conn, self.sol1, i, MOTIVO_OK, h(4))
        sv.registrar_solucion(self.conn, self.tec1, i, SOL_OK + " Segunda.", h(5))
        sv.confirmar_solucion(self.conn, self.sol1, i, h(6))
        sv.reabrir_incidencia(self.conn, self.sol1, i, MOTIVO_OK, h(7))
        d = sv.detalle_incidencia(self.conn, self.sol1, i)
        self.assertEqual([x["accion"] for x in d["historial"]],
                         ["REGISTRO", "ASIGNACION", "INICIO", "SOLUCION", "RECHAZO",
                          "SOLUCION", "CONFIRMACION", "REAPERTURA"])
        fechas = [x["fecha"] for x in d["historial"]]
        self.assertEqual(fechas, sorted(fechas))
        self.assertEqual(d["historial"][0]["usuario_nombre"], self.sol1["nombre"])
        self.assertEqual(d["historial"][4]["motivo"], MOTIVO_OK)
        self.assertEqual([s["resultado"] for s in d["soluciones"]], ["RECHAZADA", "CONFIRMADA"])

    def test_CA_S05_04_tablero_vacio(self):
        t = sv.tablero(self.conn, self.coord)
        self.assertEqual(t["total"], 0)
        self.assertEqual(t["porcentaje_cierre_texto"], "0,0 %")
        self.assertTrue(all(v == 0 for v in t["por_estado"].values()))
        self.assertTrue(all(v == 0 for v in t["por_prioridad"].values()))
        self.assertEqual(t["criticas_abiertas"], [])

    def test_CA_S05_05_porcentaje_de_cierre(self):
        self.llevar_a("CERRADA")
        self.registrar()
        self.registrar(riesgo="1")
        self.llevar_a("ASIGNADA", riesgo="1")
        t = sv.tablero(self.conn, self.coord)
        self.assertEqual(t["total"], 4)
        self.assertEqual(t["porcentaje_cierre_texto"], "25,0 %")
        self.assertEqual(t["por_estado"]["CERRADA"], 1)
        self.assertEqual(t["por_prioridad"]["CRITICA"], 2)
        self.assertEqual(len(t["criticas_abiertas"]), 2)
        self.assertEqual(sv.formatear_porcentaje(1, 3), "33,3 %")

    def test_tablero_solo_coordinador(self):
        for u in (self.sol1, self.tec1):
            with self.assertRaises(sv.ErrorPermiso):
                sv.tablero(self.conn, u)

    def test_CA_S05_06_historial_inmutable(self):
        self.registrar()
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("UPDATE historial SET motivo = 'alterado'")
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("DELETE FROM historial")
        self.assertEqual(self.cuenta("historial"), 1)
        self.assertIsNone(self.conn.execute("SELECT motivo FROM historial").fetchone()[0])


class TestRestriccionesBD(BaseTest):
    def test_check_rechaza_valores_fuera_de_dominio(self):
        i = self.registrar()["id"]
        for sql in ("UPDATE incidencias SET estado = 'INVENTADO' WHERE id = ?",
                    "UPDATE incidencias SET ubicacion = 'AULA-101' WHERE id = ?",
                    "UPDATE incidencias SET prioridad = 'NORMAL' WHERE id = ?",  # riesgo=0, ALTO -> ALTA
                    "UPDATE incidencias SET tecnico_id = 999 WHERE id = ?"):  # clave foránea
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute(sql, (i,))

    def test_cuentas_demo(self):
        roles = [r[0] for r in self.conn.execute("SELECT rol FROM usuarios ORDER BY id")]
        self.assertEqual(sorted(roles), ["COORDINADOR", "SOLICITANTE", "SOLICITANTE", "TECNICO", "TECNICO"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM usuarios WHERE activo = 1").fetchone()[0], 5)
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)


class TestSeguridad(BaseTest):
    def test_hash_con_sal_y_verificacion(self):
        h1, h2 = seguridad.hashear_password("Clave-123"), seguridad.hashear_password("Clave-123")
        self.assertNotEqual(h1, h2)
        self.assertNotIn("Clave-123", h1)
        self.assertTrue(seguridad.verificar_password("Clave-123", h1))
        self.assertFalse(seguridad.verificar_password("otra", h1))
        self.assertFalse(seguridad.verificar_password("x", "basura"))

    def test_contrasenas_no_se_guardan_en_claro(self):
        for _, _, password, _, _ in db.CUENTAS_DEMO:
            self.assertEqual(self.conn.execute(
                "SELECT COUNT(*) FROM usuarios WHERE password_hash = ?", (password,)).fetchone()[0], 0)

    def test_autenticar(self):
        self.assertIsNotNone(sv.autenticar(self.conn, "tecnico1", "Tecni1-Campus26"))
        self.assertIsNone(sv.autenticar(self.conn, "tecnico1", "mala"))
        self.assertIsNone(sv.autenticar(self.conn, "nadie", "x"))
        self.conn.execute("UPDATE usuarios SET activo = 0 WHERE usuario = 'tecnico2'")
        self.assertIsNone(sv.autenticar(self.conn, "tecnico2", "Tecni2-Campus26"))

    def test_sesiones(self):
        g = seguridad.GestorSesiones()
        token = g.crear(7)
        self.assertEqual(len(token), 64)
        self.assertEqual(g.obtener(token), 7)
        g.cerrar(token)
        self.assertIsNone(g.obtener(token))
        self.assertIsNone(g.obtener("falso"))
        self.assertIsNone(seguridad.GestorSesiones(ttl=-1).obtener(seguridad.GestorSesiones(ttl=-1).crear(1)))


class TestServidorHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.srv = servidor.crear_servidor(str(Path(cls.tmp.name) / "t.db"), "127.0.0.1", 0)
        cls.puerto = cls.srv.server_address[1]
        cls.hilo = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.hilo.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.hilo.join(timeout=5)
        cls.tmp.cleanup()

    def peticion(self, metodo, ruta, datos=None, cookie=None):
        con = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=10)
        cabeceras = {"Content-Type": "application/x-www-form-urlencoded"}
        if cookie:
            cabeceras["Cookie"] = cookie
        con.request(metodo, ruta, urlencode(datos) if datos is not None else None, cabeceras)
        r = con.getresponse()
        cuerpo = r.read().decode("utf-8")
        resultado = (r.status, dict(r.getheaders()), cuerpo)
        con.close()
        return resultado

    def sesion(self, usuario, password):
        status, cab, _ = self.peticion("POST", "/login", {"usuario": usuario, "password": password})
        self.assertEqual(status, 303)
        return cab["Set-Cookie"].split(";")[0]

    def form(self, **cambios):
        d = {"ubicacion": "BIB-01", "categoria": "MOBILIARIO", "impacto": "BAJO",
             "riesgo": "0", "descripcion": DESC_OK}
        d.update(cambios)
        return d

    def test_sin_sesion_redirige_al_login(self):
        status, cab, _ = self.peticion("GET", "/")
        self.assertEqual((status, cab["Location"]), (303, "/login"))
        status, cab, _ = self.peticion("POST", "/incidencias/nueva", self.form())
        self.assertEqual((status, cab["Location"]), (303, "/login"))

    def test_login_incorrecto_y_cookie_segura(self):
        self.assertEqual(self.peticion("POST", "/login", {"usuario": "tecnico1", "password": "mala"})[0], 401)
        _, cab, _ = self.peticion("POST", "/login", {"usuario": "solicitante1", "password": "Solic1-Campus26"})
        self.assertIn("HttpOnly", cab["Set-Cookie"])
        self.assertIn("SameSite=Strict", cab["Set-Cookie"])

    def test_registro_y_validacion_http(self):
        cookie = self.sesion("solicitante1", "Solic1-Campus26")
        status, cab, _ = self.peticion("POST", "/incidencias/nueva", self.form(), cookie)
        self.assertEqual((status, cab["Location"]), (303, "/"))
        status, _, cuerpo = self.peticion("POST", "/incidencias/nueva", self.form(ubicacion="AULA-101"), cookie)
        self.assertEqual(status, 400)
        self.assertIn("Ubicación no permitida", cuerpo)
        status, _, _ = self.peticion("POST", "/incidencias/nueva", self.form(descripcion=""), cookie)
        self.assertEqual(status, 400)

    def test_permisos_por_rol_http(self):
        tec = self.sesion("tecnico1", "Tecni1-Campus26")
        coord = self.sesion("coordinador1", "Coord1-Campus26")
        sol = self.sesion("solicitante1", "Solic1-Campus26")
        self.assertEqual(self.peticion("POST", "/incidencias/nueva", self.form(), tec)[0], 403)
        self.assertEqual(self.peticion("POST", "/incidencias/nueva", self.form(), coord)[0], 403)
        self.assertEqual(self.peticion("POST", "/incidencias/asignar",
                                       {"incidencia_id": "1", "tecnico_id": "4"}, sol)[0], 403)
        self.assertEqual(self.peticion("POST", "/incidencias/atender",
                                       {"incidencia_id": "1", "accion": "INICIAR"}, sol)[0], 403)
        self.assertEqual(self.peticion("POST", "/incidencias/validar",
                                       {"incidencia_id": "1", "accion": "CONFIRMAR"}, coord)[0], 403)
        self.assertEqual(self.peticion("GET", "/tablero", cookie=sol)[0], 403)
        self.assertEqual(self.peticion("GET", "/tablero", cookie=coord)[0], 200)

    def test_xss_escapado_en_respuesta(self):
        cookie = self.sesion("solicitante2", "Solic2-Campus26")
        payload = "<script>alert(1)</script> en el laboratorio"
        self.peticion("POST", "/incidencias/nueva", self.form(descripcion=payload), cookie)
        coord = self.sesion("coordinador1", "Coord1-Campus26")
        _, _, cuerpo = self.peticion("GET", "/incidencias/detalle?id=1", cookie=coord)
        self.assertNotIn("<script>alert", cuerpo)

    def test_flujo_completo_http(self):
        sol = self.sesion("solicitante1", "Solic1-Campus26")
        coord = self.sesion("coordinador1", "Coord1-Campus26")
        tec = self.sesion("tecnico2", "Tecni2-Campus26")
        self.peticion("POST", "/incidencias/nueva", self.form(riesgo="1"), sol)
        _, _, listado = self.peticion("GET", "/", cookie=sol)
        numero = max(int(x) for x in __import__("re").findall(r"detalle\?id=(\d+)", listado))
        self.assertEqual(self.peticion("POST", "/incidencias/asignar",
                                       {"incidencia_id": numero, "tecnico_id": 5}, coord)[0], 303)
        self.assertEqual(self.peticion("POST", "/incidencias/atender",
                                       {"incidencia_id": numero, "accion": "INICIAR"}, tec)[0], 303)
        self.assertEqual(self.peticion("POST", "/incidencias/atender",
                                       {"incidencia_id": numero, "accion": "SOLUCION",
                                        "texto_solucion": SOL_OK}, tec)[0], 303)
        self.assertEqual(self.peticion("POST", "/incidencias/validar",
                                       {"incidencia_id": numero, "accion": "CONFIRMAR"}, sol)[0], 303)
        status, _, cuerpo = self.peticion("GET", f"/incidencias/detalle?id={numero}", cookie=sol)
        self.assertEqual(status, 200)
        self.assertIn("CERRADA", cuerpo)
        # una transición inválida se responde con 409
        self.assertEqual(self.peticion("POST", "/incidencias/validar",
                                       {"incidencia_id": numero, "accion": "CONFIRMAR"}, sol)[0], 409)


if __name__ == "__main__":
    unittest.main()