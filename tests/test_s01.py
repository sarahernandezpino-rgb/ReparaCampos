"""Pruebas de la SPEC S01: Registrar incidencia (CA-S01-01 a CA-S01-07, más escape XSS de las vistas).

Archivo independiente: incluye su propia base de pruebas (BD en memoria con las cuentas demo).
Ejecutar desde la carpeta del proyecto:
    python -m unittest tests.test_s01 -v   (o)   pytest tests/test_s01.py -v
"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import seguridad  # noqa: E402
import servicios as sv  # noqa: E402
import vistas  # noqa: E402

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


class TestS01Registrar(BaseTest):
    def test_CA_S01_01_registro_valido(self):
        inc = self.registrar()
        self.assertEqual(inc["codigo"], "INC-0001")
        self.assertEqual(inc["estado"], "REGISTRADA")
        self.assertEqual(inc["solicitante_id"], self.sol1["id"])
        self.assertTrue(inc["creada_en"])
        h = self.conn.execute("SELECT * FROM historial").fetchall()
        self.assertEqual(len(h), 1)
        self.assertEqual((h[0]["accion"], h[0]["estado_anterior"], h[0]["estado_nuevo"]),
                         ("REGISTRO", None, "REGISTRADA"))
        self.assertEqual(self.registrar()["codigo"], "INC-0002")

    def test_CA_S01_02_campos_vacios(self):
        for campo in ("ubicacion", "categoria", "impacto", "riesgo", "descripcion"):
            with self.subTest(campo=campo):
                with self.assertRaises(sv.ErrorValidacion):
                    sv.registrar_incidencia(self.conn, self.sol1, self.datos(**{campo: "  "}), T0)
        with self.assertRaises(sv.ErrorValidacion):
            sv.registrar_incidencia(self.conn, self.sol1, {}, T0)
        self.assertEqual(self.cuenta("incidencias"), 0)

    def test_CA_S01_03_ubicacion_o_categoria_invalida(self):
        with self.assertRaises(sv.ErrorValidacion):
            self.registrar(ubicacion="AULA-101")
        with self.assertRaises(sv.ErrorValidacion):
            self.registrar(categoria="PINTURA")
        self.assertEqual(self.cuenta("incidencias"), 0)

    def test_CA_S01_04_impacto_o_riesgo_invalido(self):
        with self.assertRaises(sv.ErrorValidacion):
            self.registrar(impacto="MEDIO")
        for riesgo in ("quizas", "2", "-1"):
            with self.subTest(riesgo=riesgo), self.assertRaises(sv.ErrorValidacion):
                self.registrar(riesgo=riesgo)
        self.assertEqual(self.cuenta("incidencias"), 0)

    def test_CA_S01_05_longitud_descripcion_tras_trim(self):
        with self.assertRaises(sv.ErrorValidacion):
            self.registrar(descripcion="a" * 19)
        with self.assertRaises(sv.ErrorValidacion):
            self.registrar(descripcion="a" * 501)
        with self.assertRaises(sv.ErrorValidacion):  # 19 caracteres reales + espacios exteriores
            self.registrar(descripcion="   " + "a" * 19 + "   ")
        self.assertEqual(self.cuenta("incidencias"), 0)
        self.registrar(descripcion="a" * 20)
        self.registrar(descripcion="  " + "b" * 500 + "  ")
        self.assertEqual(self.cuenta("incidencias"), 2)

    def test_CA_S01_06_solo_solicitante(self):
        for u in (self.coord, self.tec1):
            with self.subTest(rol=u["rol"]), self.assertRaises(sv.ErrorPermiso):
                sv.registrar_incidencia(self.conn, u, self.datos(), T0)
        self.assertEqual(self.cuenta("incidencias"), 0)

    def test_CA_S01_07_transaccion_atomica(self):
        with mock.patch.object(sv, "_insertar_historial", side_effect=RuntimeError("falla")):
            with self.assertRaises(RuntimeError):
                self.registrar()
        self.assertEqual(self.cuenta("incidencias"), 0)
        self.assertEqual(self.cuenta("historial"), 0)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(self.registrar()["codigo"], "INC-0001")  # la BD sigue operativa


class TestVistas(BaseTest):
    def test_escape_de_texto_de_usuario(self):
        payload = "<script>alert('xss')</script> descripción larga"
        i = self.registrar(descripcion=payload)["id"]
        detalle = sv.detalle_incidencia(self.conn, self.coord, i)
        for pagina in (vistas.pagina_detalle(self.coord, detalle),
                       vistas.pagina_incidencias(self.coord, sv.listar_incidencias(self.conn, self.coord)),
                       vistas.pagina_incidencias(self.sol1, [], valores={"descripcion": payload}),
                       vistas.pagina_error(self.coord, "Error", [payload])):
            self.assertNotIn("<script>alert", pagina)
        self.assertIn("&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;", vistas.pagina_detalle(self.coord, detalle))


if __name__ == "__main__":
    unittest.main()