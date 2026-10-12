"""Pruebas de la SPEC S04: Validar, rechazar y reabrir (CA-S04-01 a CA-S04-06).

Archivo independiente: incluye su propia base de pruebas (BD en memoria con las cuentas demo).
Ejecutar desde la carpeta del proyecto:
    python -m unittest tests.test_s04 -v   (o)   pytest tests/test_s04.py -v
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
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


class TestS04Validar(BaseTest):
    def test_CA_S04_01_confirmar(self):
        i = self.llevar_a("PENDIENTE VALIDACION")
        inc = sv.confirmar_solucion(self.conn, self.sol1, i, T0)
        self.assertEqual(inc["estado"], "CERRADA")
        self.assertEqual(sv.de_texto(inc["cerrada_en"]), T0)
        self.assertEqual(self.conn.execute("SELECT resultado FROM soluciones").fetchone()[0], "CONFIRMADA")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM historial WHERE accion = 'CONFIRMACION'").fetchone()[0], 1)

    def test_CA_S04_02_rechazar_conserva_tecnico_y_solucion(self):
        i = self.llevar_a("PENDIENTE VALIDACION")
        inc = sv.rechazar_solucion(self.conn, self.sol1, i, "x" * 25, T0)
        self.assertEqual((inc["estado"], inc["tecnico_id"]), ("EN ATENCION", self.tec1["id"]))
        s = self.conn.execute("SELECT * FROM soluciones").fetchone()
        self.assertEqual((s["resultado"], s["motivo_rechazo"]), ("RECHAZADA", "x" * 25))
        h = self.conn.execute("SELECT * FROM historial WHERE accion = 'RECHAZO'").fetchone()
        self.assertEqual(h["motivo"], "x" * 25)
        # Nueva solución: la rechazada se conserva.
        sv.registrar_solucion(self.conn, self.tec1, i, SOL_OK, T0)
        self.assertEqual([r["resultado"] for r in self.conn.execute("SELECT resultado FROM soluciones")],
                         ["RECHAZADA", "PENDIENTE"])

    def test_CA_S04_03_reabrir_dentro_de_plazo(self):
        i = self.llevar_a("CERRADA")
        inc = sv.reabrir_incidencia(self.conn, self.sol1, i, MOTIVO_OK, T0 + timedelta(hours=12))
        self.assertEqual((inc["estado"], inc["tecnico_id"]), ("EN ATENCION", self.tec1["id"]))
        h = self.conn.execute("SELECT * FROM historial WHERE accion = 'REAPERTURA'").fetchone()
        self.assertEqual((h["estado_anterior"], h["estado_nuevo"], h["motivo"]),
                         ("CERRADA", "EN ATENCION", MOTIVO_OK))

    def test_CA_S04_04_plazo_vencido_y_limite_exacto(self):
        i = self.llevar_a("CERRADA")
        with self.assertRaises(sv.ErrorEstado):
            sv.reabrir_incidencia(self.conn, self.sol1, i, MOTIVO_OK, T0 + timedelta(hours=48, minutes=5))
        with self.assertRaises(sv.ErrorEstado):
            sv.reabrir_incidencia(self.conn, self.sol1, i, MOTIVO_OK, T0 + timedelta(hours=48, seconds=1))
        self.assertEqual(self.fila(i)["estado"], "CERRADA")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM historial WHERE accion = 'REAPERTURA'").fetchone()[0], 0)
        # Exactamente 48.0 horas: permitido.
        sv.reabrir_incidencia(self.conn, self.sol1, i, MOTIVO_OK, T0 + timedelta(hours=48))
        self.assertEqual(self.fila(i)["estado"], "EN ATENCION")

    def test_plazo_cuenta_desde_el_ultimo_cierre(self):
        i = self.llevar_a("CERRADA")
        sv.reabrir_incidencia(self.conn, self.sol1, i, MOTIVO_OK, T0 + timedelta(hours=40))
        sv.registrar_solucion(self.conn, self.tec1, i, SOL_OK, T0 + timedelta(hours=41))
        segundo_cierre = T0 + timedelta(hours=60)
        sv.confirmar_solucion(self.conn, self.sol1, i, segundo_cierre)
        sv.reabrir_incidencia(self.conn, self.sol1, i, MOTIVO_OK, segundo_cierre + timedelta(hours=47))
        self.assertEqual(self.fila(i)["estado"], "EN ATENCION")

    def test_CA_S04_05_motivo_invalido(self):
        i = self.llevar_a("PENDIENTE VALIDACION")
        for motivo in ("a" * 9, "   ", "", None, "a" * 301):
            with self.subTest(motivo=motivo), self.assertRaises(sv.ErrorValidacion):
                sv.rechazar_solucion(self.conn, self.sol1, i, motivo, T0)
        self.assertEqual(self.fila(i)["estado"], "PENDIENTE VALIDACION")
        sv.confirmar_solucion(self.conn, self.sol1, i, T0)
        for motivo in ("a" * 9, "   "):
            with self.subTest(motivo=motivo), self.assertRaises(sv.ErrorValidacion):
                sv.reabrir_incidencia(self.conn, self.sol1, i, motivo, T0)
        self.assertEqual(self.fila(i)["estado"], "CERRADA")

    def test_CA_S04_06_solo_el_solicitante_autor(self):
        i = self.llevar_a("PENDIENTE VALIDACION")
        for u in (self.sol2, self.coord, self.tec1):
            with self.subTest(usuario=u["usuario"]):
                with self.assertRaises(sv.ErrorPermiso):
                    sv.confirmar_solucion(self.conn, u, i, T0)
                with self.assertRaises(sv.ErrorPermiso):
                    sv.rechazar_solucion(self.conn, u, i, MOTIVO_OK, T0)
        self.assertEqual(self.fila(i)["estado"], "PENDIENTE VALIDACION")

    def test_transiciones_invalidas(self):
        i = self.llevar_a("EN ATENCION")
        with self.assertRaises(sv.ErrorEstado):
            sv.confirmar_solucion(self.conn, self.sol1, i, T0)
        with self.assertRaises(sv.ErrorEstado):
            sv.reabrir_incidencia(self.conn, self.sol1, i, MOTIVO_OK, T0)

    def test_despachador_validar(self):
        i = self.llevar_a("PENDIENTE VALIDACION")
        sv.validar(self.conn, self.sol1, i, "CONFIRMAR", None, T0)
        self.assertEqual(self.fila(i)["estado"], "CERRADA")
        with self.assertRaises(sv.ErrorValidacion):
            sv.validar(self.conn, self.sol1, i, "BORRAR", None, T0)


if __name__ == "__main__":
    unittest.main()