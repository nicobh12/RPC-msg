import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime
from xmlrpc.client import Binary, Fault
from xmlrpc.server import SimpleXMLRPCServer
from socketserver import ThreadingMixIn


HOST = os.environ.get("MENSAJERO_HOST", "0.0.0.0")
PORT = int(os.environ.get("MENSAJERO_PORT", "8000"))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATOS_FILE = os.environ.get("MENSAJERO_DATOS", os.path.join(BASE_DIR, "datos_servidor.json"))

ITERACIONES_PBKDF2 = 310_000
ALGORITMO_HASH = "pbkdf2_hmac_sha256"
MIN_NICKNAME = 2
MAX_NICKNAME = 24
MIN_PASSWORD = 8
MAX_PASSWORD = 128
MAX_MENSAJE = 4_000
MAX_ARCHIVO_BYTES = 5 * 1024 * 1024
MAX_NOMBRE_ARCHIVO = 120
INTERVALO_LIMPIEZA = 10
TIMEOUT_LATIDO = 30

sesiones = {}
ultimos_latidos = {}
_candado = threading.RLock()
_parar_limpieza = threading.Event()
_siguiente_id = 1
_datos = {"version": 1, "usuarios": {}, "mensajes": []}
_sal_ficticia = os.urandom(16)


def _ahora():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def cargar_datos():
    global _datos, _siguiente_id
    if not os.path.isfile(DATOS_FILE):
        _datos = {"version": 1, "usuarios": {}, "mensajes": []}
        _siguiente_id = 1
        return
    try:
        with open(DATOS_FILE, "r", encoding="utf-8") as archivo:
            contenido = json.load(archivo)
        if not isinstance(contenido, dict):
            raise ValueError("El archivo de datos no contiene un objeto JSON.")
        usuarios = contenido.get("usuarios", {})
        mensajes = contenido.get("mensajes", [])
        if not isinstance(usuarios, dict) or not isinstance(mensajes, list):
            raise ValueError("Estructura de datos inválida.")
        usuarios_limpios = {
            nombre: registro
            for nombre, registro in usuarios.items()
            if isinstance(nombre, str) and isinstance(registro, dict)
        }
        mensajes_limpios = [mensaje for mensaje in mensajes if isinstance(mensaje, dict)]
        _datos = {
            "version": 1,
            "usuarios": usuarios_limpios,
            "mensajes": mensajes_limpios,
        }
        ids = [
            mensaje.get("id", 0)
            for mensaje in mensajes_limpios
            if isinstance(mensaje.get("id", 0), int)
        ]
        _siguiente_id = max(ids, default=0) + 1
        print(f"[DATOS] Cargados {len(usuarios_limpios)} usuario(s) y {len(mensajes_limpios)} mensaje(s).")
    except (OSError, ValueError, TypeError) as error:
        print(f"[DATOS] No se pudo cargar {DATOS_FILE}: {error}")
        print("[DATOS] Se iniciará con una base vacía.")
        _datos = {"version": 1, "usuarios": {}, "mensajes": []}
        _siguiente_id = 1


def guardar_datos():
    temporal = f"{DATOS_FILE}.tmp"
    with open(temporal, "w", encoding="utf-8") as archivo:
        json.dump(_datos, archivo, ensure_ascii=False, indent=2)
        archivo.flush()
        os.fsync(archivo.fileno())
    os.replace(temporal, DATOS_FILE)


def _persistir():
    try:
        guardar_datos()
    except OSError as error:
        print(f"[DATOS] Error al guardar: {error}")
        raise Fault(500, "No se pudo guardar la información en el servidor.")


def _validar_nickname(nickname):
    if not isinstance(nickname, str):
        return "El nickname debe ser texto."
    if not nickname:
        return "El nickname no puede estar vacío."
    if nickname != nickname.strip():
        return "El nickname no puede empezar ni terminar con espacios."
    if len(nickname) < MIN_NICKNAME:
        return f"El nickname debe tener al menos {MIN_NICKNAME} caracteres."
    if len(nickname) > MAX_NICKNAME:
        return f"El nickname no puede superar los {MAX_NICKNAME} caracteres."
    if any(caracter.isspace() for caracter in nickname):
        return "El nickname no puede contener espacios."
    if any(ord(caracter) < 32 or ord(caracter) == 127 for caracter in nickname):
        return "El nickname contiene caracteres no permitidos."
    if not all(caracter.isalnum() or caracter in "._-" for caracter in nickname):
        return "Usa solo letras, números, punto, guion o guion bajo."
    return None


def _validar_password(password):
    if not isinstance(password, str):
        return "La contraseña debe ser texto."
    if len(password) < MIN_PASSWORD:
        return f"La contraseña debe tener al menos {MIN_PASSWORD} caracteres."
    if len(password) > MAX_PASSWORD:
        return f"La contraseña no puede superar los {MAX_PASSWORD} caracteres."
    if not password.strip():
        return "La contraseña no puede contener solo espacios."
    return None


def _hash_password(password, sal, iteraciones=ITERACIONES_PBKDF2):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), sal, iteraciones)


def _buscar_usuario(nickname):
    if not isinstance(nickname, str):
        return None
    clave = nickname.casefold()
    for nombre in _datos["usuarios"]:
        if nombre.casefold() == clave:
            return nombre
    return None


def _autenticar(token):
    if not isinstance(token, str) or not token:
        raise Fault(401, "Sesión inválida. Inicia sesión nuevamente.")
    with _candado:
        nickname = sesiones.get(token)
        if nickname:
            ultimos_latidos[token] = time.time()
    if not nickname:
        raise Fault(401, "Sesión inválida o expirada. Inicia sesión nuevamente.")
    return nickname


def _limpiar_nombre_archivo(nombre):
    if not isinstance(nombre, str):
        raise Fault(400, "El nombre del archivo no es válido.")
    nombre = nombre.replace("\\", "/").split("/")[-1]
    nombre = "".join(
        caracter for caracter in nombre
        if ord(caracter) >= 32 and ord(caracter) != 127
    ).strip().strip(".")
    nombre = "".join(
        "_" if caracter in '<>:"|?*' else caracter
        for caracter in nombre
    )
    if not nombre:
        raise Fault(400, "El nombre del archivo no es válido.")
    return nombre[:MAX_NOMBRE_ARCHIVO]


def _nuevo_mensaje(emisor, destinatario, tipo, **campos):
    global _siguiente_id
    mensaje = {
        "id": _siguiente_id,
        "de": emisor,
        "para": destinatario,
        "tipo": tipo,
        "fecha": _ahora(),
        "entregado": False,
    }
    mensaje.update(campos)
    _siguiente_id += 1
    return mensaje


def registrar(nickname, password):
    error_nickname = _validar_nickname(nickname)
    if error_nickname:
        return error_nickname
    error_password = _validar_password(password)
    if error_password:
        return error_password

    with _candado:
        if _buscar_usuario(nickname):
            return "Ese nickname ya está registrado."

    sal = os.urandom(16)
    digest = _hash_password(password, sal)

    with _candado:
        if _buscar_usuario(nickname):
            return "Ese nickname ya está registrado."
        _datos["usuarios"][nickname] = {
            "algoritmo": ALGORITMO_HASH,
            "iteraciones": ITERACIONES_PBKDF2,
            "sal": sal.hex(),
            "hash": digest.hex(),
            "registrado_en": _ahora(),
        }
        try:
            guardar_datos()
        except OSError as error:
            del _datos["usuarios"][nickname]
            print(f"[REGISTRO] Error al guardar: {error}")
            return "No se pudo guardar el usuario en el servidor."

    print(f"[REGISTRO] Nuevo usuario: {nickname}")
    return True


def login(nickname, password):
    if not isinstance(nickname, str) or not isinstance(password, str):
        return None
    nickname = nickname.strip()
    if not nickname or not password:
        return None
    if len(nickname) > MAX_NICKNAME or len(password) > MAX_PASSWORD:
        return None

    with _candado:
        nombre_real = _buscar_usuario(nickname)
        registro = dict(_datos["usuarios"][nombre_real]) if nombre_real else None

    if not registro:
        _hash_password(password, _sal_ficticia)
        print(f"[LOGIN] Fallido: {nickname!r}")
        return None

    try:
        sal = bytes.fromhex(registro["sal"])
        digest_guardado = bytes.fromhex(registro["hash"])
        iteraciones = int(registro.get("iteraciones", ITERACIONES_PBKDF2))
        if registro.get("algoritmo") != ALGORITMO_HASH:
            return None
        digest_calculado = _hash_password(password, sal, iteraciones)
    except (KeyError, TypeError, ValueError):
        print(f"[LOGIN] Registro inválido para {nombre_real!r}")
        return None

    if not hmac.compare_digest(digest_calculado, digest_guardado):
        print(f"[LOGIN] Fallido: {nombre_real!r}")
        return None

    token = secrets.token_urlsafe(48)
    with _candado:
        while token in sesiones:
            token = secrets.token_urlsafe(48)
        sesiones[token] = nombre_real
        ultimos_latidos[token] = time.time()

    print(f"[LOGIN] {nombre_real} conectado.")
    return token


def logout(token):
    if not isinstance(token, str):
        return False
    with _candado:
        nickname = sesiones.pop(token, None)
        ultimos_latidos.pop(token, None)
    if nickname:
        print(f"[LOGOUT] {nickname} desconectado.")
        return True
    return False


def ping(token):
    _autenticar(token)
    return True


def revisar_sesiones_inactivas():
    ahora = time.time()
    expirados = []
    with _candado:
        for token in list(sesiones):
            ultimo = ultimos_latidos.get(token)
            if ultimo is None or ahora - ultimo > TIMEOUT_LATIDO:
                nickname = sesiones.pop(token, None)
                ultimos_latidos.pop(token, None)
                if nickname:
                    expirados.append(nickname)
        for token in list(ultimos_latidos):
            if token not in sesiones:
                ultimos_latidos.pop(token, None)
    for nickname in expirados:
        print(f"[SISTEMA] Usuario {nickname} desconectado por inactividad.")


def _hilo_limpieza():
    while not _parar_limpieza.wait(INTERVALO_LIMPIEZA):
        revisar_sesiones_inactivas()


def obtener_directorio(token):
    usuario = _autenticar(token)
    with _candado:
        conectados = set(sesiones.values())
        directorio = [
            {"nickname": nombre, "conectado": nombre in conectados}
            for nombre in _datos["usuarios"]
        ]
    directorio.sort(key=lambda item: (not item["conectado"], item["nickname"].casefold()))
    return directorio


def enviar_mensaje(token, destinatario, mensaje):
    emisor = _autenticar(token)
    if not isinstance(destinatario, str) or not destinatario.strip():
        raise Fault(400, "El destinatario no es válido.")
    if not isinstance(mensaje, str):
        raise Fault(400, "El mensaje debe ser texto.")
    mensaje = mensaje.strip()
    if not mensaje:
        raise Fault(400, "El mensaje no puede estar vacío.")
    if len(mensaje) > MAX_MENSAJE:
        raise Fault(400, f"El mensaje no puede superar los {MAX_MENSAJE} caracteres.")

    with _candado:
        destino = _buscar_usuario(destinatario.strip())
        if not destino:
            raise Fault(404, "El destinatario no existe.")
        if destino.casefold() == emisor.casefold():
            raise Fault(400, "No puedes enviarte mensajes a ti mismo.")
        nuevo = _nuevo_mensaje(emisor, destino, "texto", mensaje=mensaje)
        _datos["mensajes"].append(nuevo)
        try:
            guardar_datos()
        except OSError as error:
            _datos["mensajes"].pop()
            print(f"[MENSAJE] Error al guardar: {error}")
            raise Fault(500, "No se pudo guardar el mensaje.")

    resumen = mensaje.replace("\n", " ")[:300]
    print(f"[MENSAJE] {emisor} -> {destino}: {resumen}")
    return True


def obtener_historial(token, contacto, marcar_entregado=True):
    usuario = _autenticar(token)
    if not isinstance(contacto, str) or not contacto.strip():
        raise Fault(400, "El contacto no es válido.")

    with _candado:
        otro = _buscar_usuario(contacto.strip())
        if not otro:
            raise Fault(404, "El contacto no existe.")
        historial = []
        hubo_cambios = False
        for mensaje in _datos["mensajes"]:
            de = mensaje.get("de")
            para = mensaje.get("para")
            if not ((de == usuario and para == otro) or (de == otro and para == usuario)):
                continue
            entregado = bool(mensaje.get("entregado", False))
            if marcar_entregado and para == usuario and not entregado:
                mensaje["entregado"] = True
                entregado = True
                hubo_cambios = True
            item = {
                "id": mensaje.get("id", 0),
                "de": de,
                "para": para,
                "tipo": mensaje.get("tipo", "texto"),
                "fecha": mensaje.get("fecha", ""),
                "entregado": entregado,
            }
            if item["tipo"] == "archivo":
                item["nombre_archivo"] = mensaje.get("nombre_archivo", "archivo")
                item["tamano"] = mensaje.get("tamano", 0)
                try:
                    item["datos"] = Binary(base64.b64decode(mensaje.get("datos_b64", "")))
                except (ValueError, TypeError):
                    item["datos"] = Binary(b"")
            else:
                item["mensaje"] = mensaje.get("mensaje", "")
            historial.append(item)
        if hubo_cambios:
            try:
                guardar_datos()
            except OSError as error:
                print(f"[HISTORIAL] No se pudo guardar la confirmación de entrega: {error}")

    return historial


def borrar_historial(token, contacto):
    usuario = _autenticar(token)
    if not isinstance(contacto, str) or not contacto.strip():
        raise Fault(400, "El contacto no es válido.")

    with _candado:
        otro = _buscar_usuario(contacto.strip())
        if not otro:
            raise Fault(404, "El contacto no existe.")
        anteriores = _datos["mensajes"]
        _datos["mensajes"] = [
            mensaje for mensaje in anteriores
            if not (
                (mensaje.get("de") == usuario and mensaje.get("para") == otro)
                or (mensaje.get("de") == otro and mensaje.get("para") == usuario)
            )
        ]
        borrados = len(anteriores) - len(_datos["mensajes"])
        try:
            guardar_datos()
        except OSError as error:
            _datos["mensajes"] = anteriores
            print(f"[HISTORIAL] Error al guardar: {error}")
            raise Fault(500, "No se pudo borrar el historial.")

    print(f"[HISTORIAL] {usuario} borró {borrados} mensaje(s) con {otro}.")
    return True


def enviar_archivo(token, destinatario, nombre_archivo, datos_binarios):
    emisor = _autenticar(token)
    if not isinstance(destinatario, str) or not destinatario.strip():
        raise Fault(400, "El destinatario no es válido.")
    if isinstance(datos_binarios, Binary):
        contenido = datos_binarios.data
    elif isinstance(datos_binarios, (bytes, bytearray)):
        contenido = bytes(datos_binarios)
    else:
        raise Fault(400, "El archivo debe enviarse como xmlrpc.client.Binary.")
    if not contenido:
        raise Fault(400, "El archivo está vacío.")
    if len(contenido) > MAX_ARCHIVO_BYTES:
        raise Fault(413, "El archivo supera el límite de 5 MB.")

    nombre = _limpiar_nombre_archivo(nombre_archivo)

    with _candado:
        destino = _buscar_usuario(destinatario.strip())
        if not destino:
            raise Fault(404, "El destinatario no existe.")
        if destino.casefold() == emisor.casefold():
            raise Fault(400, "No puedes enviarte archivos a ti mismo.")
        nuevo = _nuevo_mensaje(
            emisor,
            destino,
            "archivo",
            nombre_archivo=nombre,
            tamano=len(contenido),
            datos_b64=base64.b64encode(contenido).decode("ascii"),
        )
        _datos["mensajes"].append(nuevo)
        try:
            guardar_datos()
        except OSError as error:
            _datos["mensajes"].pop()
            print(f"[ARCHIVO] Error al guardar: {error}")
            raise Fault(500, "No se pudo guardar el archivo.")

    print(f"[ARCHIVO] {emisor} -> {destino}: {nombre} ({len(contenido)} bytes)")
    return True


class ServidorXMLRPC(ThreadingMixIn, SimpleXMLRPCServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    cargar_datos()
    servidor = ServidorXMLRPC(
        (HOST, PORT),
        allow_none=True,
        logRequests=False,
    )
    funciones = {
        "registrar": registrar,
        "login": login,
        "logout": logout,
        "ping": ping,
        "obtener_directorio": obtener_directorio,
        "enviar_mensaje": enviar_mensaje,
        "obtener_historial": obtener_historial,
        "borrar_historial": borrar_historial,
        "enviar_archivo": enviar_archivo,
    }
    for nombre, funcion in funciones.items():
        servidor.register_function(funcion, nombre)

    print("=" * 62)
    print(f"Servidor XML-RPC escuchando en {HOST}:{PORT}")
    print("Clientes Windows: http://192.168.56.10:8000")
    print(f"Archivo de datos: {DATOS_FILE}")
    print("Ctrl+C para detener el servidor.")
    print("=" * 62)

    _parar_limpieza.clear()
    hilo_limpieza = threading.Thread(
        target=_hilo_limpieza,
        name="limpieza-sesiones",
        daemon=True,
    )
    hilo_limpieza.start()

    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\n[Servidor] Deteniendo...")
    finally:
        _parar_limpieza.set()
        hilo_limpieza.join(timeout=1)
        servidor.server_close()
        print("[Servidor] Cerrado.")


if __name__ == "__main__":
    main()
