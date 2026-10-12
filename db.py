"""db.py - Esquema SQLite, restricciones, triggers de inmutabilidad y cuentas de prueba.

Solo este módulo crea el esquema. Las conexiones se abren en modo autocommit
(isolation_level=None) para que servicios.py controle las transacciones de forma
explícita (BEGIN IMMEDIATE / COMMIT / ROLLBACK).
"""
import os
import sqlite3

import seguridad

RUTA_POR_DEFECTO = os.environ.get("REPARACAMPUS_DB", "reparacampus.db")

# (nombre, usuario, contraseña, rol, activo). Cuentas ficticias de la demostración.
CUENTAS_DEMO = [
    ("Yuri Dominguez", "solicitante1", "Solic1-Campus26", "SOLICITANTE", 1),
    ("Jesus Martinez", "solicitante2", "Solic2-Campus26", "SOLICITANTE", 1),
    ("Leris Bolaño", "coordinador1", "Coord1-Campus26", "COORDINADOR", 1),
    ("Sara Hernandez", "tecnico1", "Tecni1-Campus26", "TECNICO", 1),
    ("Keren Sierra", "tecnico2", "Tecni2-Campus26", "TECNICO", 1),
]

ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre        TEXT NOT NULL,
    usuario       TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    rol           TEXT NOT NULL
                  CHECK (rol IN ('SOLICITANTE', 'COORDINADOR', 'TECNICO')),
    activo        INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1))
);

CREATE TABLE IF NOT EXISTS incidencias (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo         TEXT NOT NULL UNIQUE,
    ubicacion      TEXT NOT NULL
                   CHECK (ubicacion IN ('LAB-01', 'AULA-201', 'BIB-01')),
    categoria      TEXT NOT NULL
                   CHECK (categoria IN ('ELECTRICIDAD', 'HIDRAULICA', 'MOBILIARIO', 'TIC')),
    descripcion    TEXT NOT NULL
                   CHECK (length(trim(descripcion)) BETWEEN 20 AND 500),
    impacto        TEXT NOT NULL CHECK (impacto IN ('BAJO', 'ALTO')),
    riesgo         INTEGER NOT NULL CHECK (riesgo IN (0, 1)),
    prioridad      TEXT NOT NULL CHECK (prioridad IN ('CRITICA', 'ALTA', 'NORMAL')),
    estado         TEXT NOT NULL
                   CHECK (estado IN ('REGISTRADA', 'ASIGNADA', 'EN ATENCION',
                                     'PENDIENTE VALIDACION', 'CERRADA')),
    solicitante_id INTEGER NOT NULL REFERENCES usuarios(id),
    tecnico_id     INTEGER REFERENCES usuarios(id),
    creada_en      TEXT NOT NULL,
    cerrada_en     TEXT,
    -- La prioridad siempre debe coincidir con la regla de negocio.
    CHECK (prioridad = CASE WHEN riesgo = 1 THEN 'CRITICA'
                            WHEN impacto = 'ALTO' THEN 'ALTA'
                            ELSE 'NORMAL' END)
);

CREATE TABLE IF NOT EXISTS soluciones (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    incidencia_id  INTEGER NOT NULL REFERENCES incidencias(id),
    tecnico_id     INTEGER NOT NULL REFERENCES usuarios(id),
    texto          TEXT NOT NULL CHECK (length(trim(texto)) BETWEEN 20 AND 800),
    registrada_en  TEXT NOT NULL,
    resultado      TEXT NOT NULL DEFAULT 'PENDIENTE'
                   CHECK (resultado IN ('PENDIENTE', 'CONFIRMADA', 'RECHAZADA')),
    motivo_rechazo TEXT
);

CREATE TABLE IF NOT EXISTS historial (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    incidencia_id   INTEGER NOT NULL REFERENCES incidencias(id),
    usuario_id      INTEGER NOT NULL REFERENCES usuarios(id),
    accion          TEXT NOT NULL
                    CHECK (accion IN ('REGISTRO', 'ASIGNACION', 'INICIO', 'SOLUCION',
                                      'CONFIRMACION', 'RECHAZO', 'REAPERTURA')),
    estado_anterior TEXT
                    CHECK (estado_anterior IS NULL OR estado_anterior IN
                           ('REGISTRADA', 'ASIGNADA', 'EN ATENCION',
                            'PENDIENTE VALIDACION', 'CERRADA')),
    estado_nuevo    TEXT NOT NULL
                    CHECK (estado_nuevo IN ('REGISTRADA', 'ASIGNADA', 'EN ATENCION',
                                            'PENDIENTE VALIDACION', 'CERRADA')),
    motivo          TEXT,
    fecha           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_inc_solicitante ON incidencias(solicitante_id);
CREATE INDEX IF NOT EXISTS idx_inc_tecnico     ON incidencias(tecnico_id);
CREATE INDEX IF NOT EXISTS idx_inc_estado      ON incidencias(estado);
CREATE INDEX IF NOT EXISTS idx_hist_incidencia ON historial(incidencia_id);

-- Inmutabilidad del historial: ninguna sentencia puede modificar ni borrar filas.
CREATE TRIGGER IF NOT EXISTS historial_no_update
BEFORE UPDATE ON historial
BEGIN
    SELECT RAISE(ABORT, 'El historial es inmutable: UPDATE no permitido');
END;

CREATE TRIGGER IF NOT EXISTS historial_no_delete
BEFORE DELETE ON historial
BEGIN
    SELECT RAISE(ABORT, 'El historial es inmutable: DELETE no permitido');
END;
"""


def conectar(ruta=None):
    """Abre una conexión con claves foráneas activas y filas accesibles por nombre."""
    conn = sqlite3.connect(ruta or RUTA_POR_DEFECTO, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def crear_esquema(conn):
    conn.executescript(ESQUEMA)


def cargar_cuentas_demo(conn):
    """Inserta las 5 cuentas ficticias (solo guarda el hash PBKDF2 con sal)."""
    for nombre, usuario, password, rol, activo in CUENTAS_DEMO:
        existe = conn.execute(
            "SELECT 1 FROM usuarios WHERE usuario = ?", (usuario,)
        ).fetchone()
        if existe:
            continue
        conn.execute(
            "INSERT INTO usuarios (nombre, usuario, password_hash, rol, activo) "
            "VALUES (?, ?, ?, ?, ?)",
            (nombre, usuario, seguridad.hashear_password(password), rol, activo),
        )


def inicializar(ruta=None):
    """Crea esquema y cuentas si hacen falta. Devuelve la conexión abierta."""
    conn = conectar(ruta)
    crear_esquema(conn)
    cargar_cuentas_demo(conn)
    return conn


if __name__ == "__main__":
    inicializar().close()
    print("Base de datos lista en", RUTA_POR_DEFECTO)