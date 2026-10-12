"""seguridad.py - Hash de contraseñas (PBKDF2-SHA256 con sal) y sesiones con token aleatorio."""
import hashlib
import hmac
import secrets
import threading
import time

ITERACIONES = 210_000
TTL_SESION_SEGUNDOS = 8 * 60 * 60
_ALGORITMO = "pbkdf2_sha256"


def hashear_password(password, iteraciones=None):
    """Devuelve 'pbkdf2_sha256$iteraciones$sal_hex$hash_hex'. Nunca la contraseña."""
    iteraciones = iteraciones or ITERACIONES
    sal = secrets.token_bytes(16)
    derivado = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), sal, iteraciones)
    return f"{_ALGORITMO}${iteraciones}${sal.hex()}${derivado.hex()}"


def verificar_password(password, almacenado):
    """Compara en tiempo constante. Devuelve False ante cualquier formato inválido."""
    try:
        algoritmo, iteraciones, sal_hex, hash_hex = almacenado.split("$")
        if algoritmo != _ALGORITMO:
            return False
        derivado = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(sal_hex), int(iteraciones)
        )
        return hmac.compare_digest(derivado.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def verificacion_falsa(password):
    """Gasta el mismo tiempo que una verificación real (usuario inexistente)."""
    hashear_password(password)


class GestorSesiones:
    """Sesiones en memoria: token aleatorio -> id de usuario, con caducidad."""

    def __init__(self, ttl=TTL_SESION_SEGUNDOS):
        self._ttl = ttl
        self._sesiones = {}
        self._candado = threading.Lock()

    def crear(self, usuario_id):
        token = secrets.token_hex(32)
        with self._candado:
            self._sesiones[token] = (usuario_id, time.monotonic() + self._ttl)
        return token

    def obtener(self, token):
        if not token:
            return None
        with self._candado:
            dato = self._sesiones.get(token)
            if dato is None:
                return None
            usuario_id, expira = dato
            if time.monotonic() > expira:
                del self._sesiones[token]
                return None
            return usuario_id

    def cerrar(self, token):
        with self._candado:
            self._sesiones.pop(token, None)