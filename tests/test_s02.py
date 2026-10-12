"""Pruebas de la SPEC S02: Priorizar y asignar (CA-S02-01 a CA-S02-05).

Archivo independiente: incluye su propia base de pruebas (BD en memoria con las cuentas demo).
Ejecutar desde la carpeta del proyecto:
    python -m unittest tests.test_s02 -v   (o)   pytest tests/test_s02.py -v
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


class TestS02PriorizarAsignar(BaseTest):
    def test_CA_S02_01_prioridad_automatica(self):
        casos = [("1", "BAJO", "CRITICA"), ("0", "ALTO", "ALTA"), ("0", "BAJO", "NORMAL"),
                 ("1", "ALTO", "CRITICA")]
        for riesgo, impacto, esperada in casos:
            with self.subTest(riesgo=riesgo, impacto=impacto):
                self.assertEqual(self.registrar(riesgo=riesgo, impacto=impacto)["prioridad"], esperada)
        self.assertEqual(sv.calcular_prioridad(1, "BAJO"), "CRITICA")

    def test_CA_S02_02_asignacion_valida(self):
        i = self.registrar()["id"]
        inc = sv.asignar_incidencia(self.conn, self.coord, i, self.tec1["id"], T0)
        self.assertEqual((inc["estado"], inc["tecnico_id"]), ("ASIGNADA", self.tec1["id"]))
        h = self.conn.execute("SELECT * FROM historial WHERE accion = 'ASIGNACION'").fetchone()
        self.assertEqual((h["estado_anterior"], h["estado_nuevo"]), ("REGISTRADA", "ASIGNADA"))

    def test_CA_S02_03_no_reasignar(self):
        for estado in ("ASIGNADA", "EN ATENCION"):
            with self.subTest(estado=estado):
                i = self.llevar_a(estado)
                antes = self.cuenta("historial")
                with self.assertRaises(sv.ErrorEstado):
                    sv.asignar_incidencia(self.conn, self.coord, i, self.tec2["id"], T0)
                self.assertEqual(self.fila(i)["tecnico_id"], self.tec1["id"])
                self.assertEqual(self.cuenta("historial"), antes)

    def test_CA_S02_04_tecnico_inactivo_o_no_tecnico(self):
        i = self.registrar()["id"]
        self.conn.execute("UPDATE usuarios SET activo = 0 WHERE id = ?", (self.tec2["id"],))
        with self.assertRaises(sv.ErrorValidacion):
            sv.asignar_incidencia(self.conn, self.coord, i, self.tec2["id"], T0)
        with self.assertRaises(sv.ErrorValidacion):  # un solicitante no es técnico
            sv.asignar_incidencia(self.conn, self.coord, i, self.sol2["id"], T0)
        self.assertEqual(self.fila(i)["estado"], "REGISTRADA")
        self.assertEqual([t["id"] for t in sv.listar_tecnicos_activos(self.conn)], [self.tec1["id"]])

    def test_CA_S02_05_solo_coordinador(self):
        i = self.registrar()["id"]
        for u in (self.sol1, self.tec1):
            with self.subTest(rol=u["rol"]), self.assertRaises(sv.ErrorPermiso):
                sv.asignar_incidencia(self.conn, u, i, self.tec1["id"], T0)
        self.assertEqual(self.fila(i)["estado"], "REGISTRADA")


if __name__ == "__main__":
    unittest.main()