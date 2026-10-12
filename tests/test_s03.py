"""Pruebas de la SPEC S03: Atender incidencia (CA-S03-01 a CA-S03-05).

Archivo independiente: incluye su propia base de pruebas (BD en memoria con las cuentas demo).
Ejecutar desde la carpeta del proyecto:
    python -m unittest tests.test_s03 -v   (o)   pytest tests/test_s03.py -v
"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import seguridad  # noqa: E402
import servicios as sv  # noqa: E402

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


class TestS03Atender(BaseTest):
    def test_CA_S03_01_iniciar_atencion(self):
        i = self.llevar_a("ASIGNADA")
        inc = sv.iniciar_atencion(self.conn, self.tec1, i, T0)
        self.assertEqual(inc["estado"], "EN ATENCION")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM historial WHERE accion = 'INICIO'").fetchone()[0], 1)

    def test_CA_S03_02_registrar_solucion(self):
        i = self.llevar_a("EN ATENCION")
        inc = sv.registrar_solucion(self.conn, self.tec1, i, "x" * 50, T0)
        self.assertEqual(inc["estado"], "PENDIENTE VALIDACION")
        s = self.conn.execute("SELECT * FROM soluciones").fetchone()
        self.assertEqual((s["tecnico_id"], s["resultado"]), (self.tec1["id"], "PENDIENTE"))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM historial WHERE accion = 'SOLUCION'").fetchone()[0], 1)

    def test_CA_S03_03_tecnico_no_asignado(self):
        i = self.llevar_a("ASIGNADA")
        antes = self.cuenta("historial")
        with self.assertRaises(sv.ErrorPermiso):
            sv.iniciar_atencion(self.conn, self.tec2, i, T0)
        sv.iniciar_atencion(self.conn, self.tec1, i, T0)
        with self.assertRaises(sv.ErrorPermiso):
            sv.registrar_solucion(self.conn, self.tec2, i, SOL_OK, T0)
        self.assertEqual(self.cuenta("historial"), antes + 1)
        self.assertEqual(self.cuenta("soluciones"), 0)

    def test_CA_S03_04_longitud_solucion(self):
        i = self.llevar_a("EN ATENCION")
        for texto in ("a" * 19, "a" * 801, "  " + "a" * 19 + "  ", "   "):
            with self.subTest(largo=len(texto)), self.assertRaises(sv.ErrorValidacion):
                sv.registrar_solucion(self.conn, self.tec1, i, texto, T0)
        self.assertEqual(self.fila(i)["estado"], "EN ATENCION")
        self.assertEqual(self.cuenta("soluciones"), 0)
        sv.registrar_solucion(self.conn, self.tec1, i, "a" * 800, T0)

    def test_CA_S03_05_solucion_sin_iniciar(self):
        i = self.llevar_a("ASIGNADA")
        with self.assertRaises(sv.ErrorEstado):
            sv.registrar_solucion(self.conn, self.tec1, i, SOL_OK, T0)
        self.assertEqual(self.fila(i)["estado"], "ASIGNADA")
        self.assertEqual(self.cuenta("soluciones"), 0)

    def test_despachador_atender(self):
        i = self.llevar_a("ASIGNADA")
        sv.atender(self.conn, self.tec1, i, "INICIAR", None, T0)
        sv.atender(self.conn, self.tec1, i, "SOLUCION", SOL_OK, T0)
        self.assertEqual(self.fila(i)["estado"], "PENDIENTE VALIDACION")
        with self.assertRaises(sv.ErrorValidacion):
            sv.atender(self.conn, self.tec1, i, "OTRA", None, T0)


if __name__ == "__main__":
    unittest.main()