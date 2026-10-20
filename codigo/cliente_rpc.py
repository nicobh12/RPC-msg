import os
import queue
import socket
import threading
import traceback
import xmlrpc.client
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
import tkinter as tk


URL_SERVIDOR = os.environ.get("MENSAJERO_URL", "http://192.168.56.10:8000")
TIEMPO_ESPERA = 6
INTERVALO_DIRECTORIO_MS = 4_000
INTERVALO_HISTORIAL_MS = 2_200
INTERVALO_NOVEDADES_MS = 5_000
INTERVALO_PING_MS = 10_000
MAX_MENSAJE = 4_000
MAX_ARCHIVO_BYTES = 5 * 1024 * 1024

EXTENSIONES_IMAGEN = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
EXTENSIONES_AUDIO = {".mp3", ".wav", ".ogg", ".m4a", ".flac"}
EXTENSIONES_TEXTO = {".txt", ".pdf", ".doc", ".docx", ".rtf", ".odt"}

FUENTE = "Segoe UI"
FONDO = "#EEF3F8"
TARJETA = "#FFFFFF"
SIDEBAR = "#172033"
SIDEBAR_ACTIVO = "#24304A"
ACENTO = "#2F6FED"
ACENTO_OSCURO = "#245BD0"
TEXTO = "#1F2937"
TEXTO_SUAVE = "#6B7280"
BORDE = "#DDE5EE"
EXITO = "#12805C"
ERROR = "#C0392B"
EN_LINEA = "#0F9D58"
DESCONECTADO = "#8A94A6"


class TransporteConTiempo(xmlrpc.client.Transport):
    def make_connection(self, host):
        conexion = super().make_connection(host)
        conexion.timeout = TIEMPO_ESPERA
        return conexion


def rpc(metodo, *argumentos):
    with xmlrpc.client.ServerProxy(
        URL_SERVIDOR,
        allow_none=True,
        transport=TransporteConTiempo(),
    ) as proxy:
        return getattr(proxy, metodo)(*argumentos)


def describir_error(error):
    if isinstance(error, xmlrpc.client.Fault):
        return error.faultString
    if isinstance(error, xmlrpc.client.ProtocolError):
        return f"El servidor respondió con HTTP {error.errcode}."
    if isinstance(error, (TimeoutError, socket.timeout)):
        return "El servidor tardó demasiado en responder."
    if isinstance(error, OSError):
        return f"No se pudo conectar con {URL_SERVIDOR}.\n{error}"
    return str(error)


def error_de_sesion(error):
    return isinstance(error, xmlrpc.client.Fault) and error.faultCode == 401


def tamano_legible(cantidad):
    try:
        cantidad = int(cantidad)
    except (TypeError, ValueError):
        return "tamaño desconocido"
    unidades = ("B", "KB", "MB", "GB")
    valor = float(cantidad)
    for unidad in unidades:
        if valor < 1024 or unidad == unidades[-1]:
            if unidad == "B":
                return f"{int(valor)} {unidad}"
            return f"{valor:.1f} {unidad}"
        valor /= 1024
    return f"{cantidad} B"


def formatear_fecha(valor):
    if not valor:
        return ""
    try:
        fecha = datetime.fromisoformat(valor)
        if fecha.tzinfo is not None:
            fecha = fecha.astimezone()
        return fecha.strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return str(valor)


def clasificar_archivo(nombre):
    extension = os.path.splitext(str(nombre))[1].lower()
    if extension in EXTENSIONES_IMAGEN:
        return "🖼️", "Imagen"
    if extension in EXTENSIONES_AUDIO:
        return "🎵", "Audio"
    if extension in EXTENSIONES_TEXTO:
        return "📄", "Documento"
    return "📎", "Archivo"


class Aplicacion:
    def __init__(self, root):
        self.root = root
        self.token = None
        self.nickname = None
        self.vista_login = None
        self.principal = None
        self.chats = {}
        self.no_leidos = set()
        self.eventos = queue.Queue()
        self.cerrando = False
        self._job_ping = None

        self.root.option_add("*Font", (FUENTE, 10))
        self.root.configure(bg=FONDO)
        self.root.protocol("WM_DELETE_WINDOW", self.cerrar_aplicacion)
        self._configurar_estilos()
        self.mostrar_login()
        self.root.after(75, self._procesar_eventos)

    def _configurar_estilos(self):
        estilo = ttk.Style(self.root)
        try:
            estilo.theme_use("clam")
        except tk.TclError:
            pass

        estilo.configure("TFrame", background=FONDO)
        estilo.configure("Card.TFrame", background=TARJETA)
        estilo.configure("TLabel", background=FONDO, foreground=TEXTO)
        estilo.configure("Card.TLabel", background=TARJETA, foreground=TEXTO)
        estilo.configure("Titulo.TLabel", background=TARJETA, foreground=TEXTO, font=(FUENTE, 24, "bold"))
        estilo.configure("Subtitulo.TLabel", background=TARJETA, foreground=TEXTO_SUAVE, font=(FUENTE, 10))
        estilo.configure("Campo.TLabel", background=TARJETA, foreground=TEXTO, font=(FUENTE, 9, "bold"))
        estilo.configure("Login.TEntry", padding=10, fieldbackground="#F7F9FC", foreground=TEXTO)
        estilo.configure("Buscar.TEntry", padding=9, fieldbackground="#F7F9FC", foreground=TEXTO)

        estilo.configure(
            "Accent.TButton",
            background=ACENTO,
            foreground="#FFFFFF",
            borderwidth=0,
            padding=(18, 10),
            font=(FUENTE, 10, "bold"),
        )
        estilo.map(
            "Accent.TButton",
            background=[("active", ACENTO_OSCURO), ("disabled", "#A8BEED")],
            foreground=[("disabled", "#F2F5FA")],
        )
        estilo.configure(
            "Secondary.TButton",
            background="#E9EFF7",
            foreground=TEXTO,
            borderwidth=0,
            padding=(14, 9),
            font=(FUENTE, 9),
        )
        estilo.map(
            "Secondary.TButton",
            background=[("active", "#DDE7F4"), ("disabled", "#F0F3F8")],
            foreground=[("disabled", "#9AA5B5")],
        )
        estilo.configure(
            "Small.TButton",
            background="#EEF3F8",
            foreground=TEXTO_SUAVE,
            borderwidth=0,
            padding=(10, 6),
            font=(FUENTE, 9),
        )
        estilo.map("Small.TButton", background=[("active", "#E2EAF4")])

        estilo.configure(
            "Treeview",
            background=TARJETA,
            fieldbackground=TARJETA,
            foreground=TEXTO,
            borderwidth=0,
            rowheight=44,
            font=(FUENTE, 10),
        )
        estilo.configure(
            "Treeview.Heading",
            background=TARJETA,
            foreground=TEXTO_SUAVE,
            relief="flat",
            borderwidth=0,
            font=(FUENTE, 9, "bold"),
            padding=(6, 8),
        )
        estilo.map(
            "Treeview",
            background=[("selected", "#E7F0FF")],
            foreground=[("selected", TEXTO)],
        )
        estilo.map("Treeview.Heading", background=[("active", TARJETA)])

    def _centrar(self, ancho, alto):
        self.root.update_idletasks()
        x = max(0, (self.root.winfo_screenwidth() - ancho) // 2)
        y = max(0, (self.root.winfo_screenheight() - alto) // 2)
        self.root.geometry(f"{ancho}x{alto}+{x}+{y}")

    def mostrar_login(self):
        self._detener_ping()
        self.no_leidos.clear()
        for chat in list(self.chats.values()):
            chat.cerrar()
        self.chats.clear()
        if self.principal:
            self.principal.detener()
            self.principal = None
        for widget in list(self.root.winfo_children()):
            widget.destroy()
        self.root.title("Mensajero — Acceso")
        self.root.minsize(420, 540)
        self._centrar(440, 590)
        self.vista_login = VistaLogin(self.root, self)
        self.vista_login.pack(fill="both", expand=True)

    def login_exitoso(self, token, nickname):
        self.token = token
        self.nickname = nickname
        self.no_leidos.clear()
        if self.vista_login:
            self.vista_login.destroy()
            self.vista_login = None
        self.root.title(f"Mensajero — ({self.nickname})")
        self.root.minsize(760, 520)
        self._centrar(900, 640)
        self.principal = VentanaPrincipal(self.root, self)
        self.principal.pack(fill="both", expand=True)
        self._iniciar_ping()

    def cerrar_sesion(self):
        if not self.token:
            return
        if not messagebox.askyesno(
            "Cerrar sesión",
            "¿Seguro que quieres cerrar sesión?",
            parent=self.root,
        ):
            return
        token = self.token
        self.token = None
        self.nickname = None
        self.mostrar_login()
        self.ejecutar_async(lambda: rpc("logout", token), lambda resultado, error: None)

    def sesion_expirada(self):
        if self.token is None:
            return
        self.token = None
        self.nickname = None
        self.mostrar_login()
        messagebox.showwarning(
            "Sesión finalizada",
            "La sesión dejó de ser válida. Inicia sesión nuevamente.",
            parent=self.root,
        )

    def abrir_chat(self, contacto):
        if contacto.casefold() == self.nickname.casefold():
            return
        existente = self.chats.get(contacto)
        if existente:
            existente.ventana.deiconify()
            existente.ventana.lift()
            existente.ventana.focus_force()
            existente.marcar_leido()
            return
        chat = VentanaChat(self, contacto)
        self.chats[contacto] = chat
        chat.marcar_leido()

    def marcar_no_leido(self, contacto):
        self.no_leidos.add(contacto.casefold())
        if self.principal:
            self.principal.actualizar_marca_contacto(contacto)

    def marcar_leido(self, contacto):
        self.no_leidos.discard(contacto.casefold())
        if self.principal:
            self.principal.actualizar_marca_contacto(contacto)

    def esta_no_leido(self, contacto):
        return contacto.casefold() in self.no_leidos

    def actualizar_ids_conocidos(self, contacto, ids):
        if self.principal:
            self.principal.actualizar_ids_conocidos(contacto, ids)

    def _iniciar_ping(self):
        self._detener_ping()
        self._programar_ping()

    def _programar_ping(self):
        if self.cerrando or not self.token:
            return
        self._job_ping = self.root.after(INTERVALO_PING_MS, self._ping)

    def _ping(self):
        self._job_ping = None
        if self.cerrando or not self.token:
            return
        token = self.token
        self.ejecutar_async(lambda: rpc("ping", token), lambda resultado, error: None)
        self._programar_ping()

    def _detener_ping(self):
        if self._job_ping:
            try:
                self.root.after_cancel(self._job_ping)
            except tk.TclError:
                pass
            self._job_ping = None

    def estado_contacto(self, contacto):
        if not self.principal:
            return None
        for item in self.principal.directorio:
            if item.get("nickname", "").casefold() == contacto.casefold():
                return bool(item.get("conectado"))
        return None

    def ejecutar_async(self, funcion, al_terminar):
        def trabajador():
            try:
                resultado = funcion()
            except Exception as error:
                self.eventos.put((al_terminar, None, error))
            else:
                self.eventos.put((al_terminar, resultado, None))

        threading.Thread(target=trabajador, daemon=True).start()

    def _procesar_eventos(self):
        try:
            while True:
                callback, resultado, error = self.eventos.get_nowait()
                try:
                    callback(resultado, error)
                except Exception:
                    traceback.print_exc()
        except queue.Empty:
            pass
        if not self.cerrando:
            self.root.after(75, self._procesar_eventos)

    def cerrar_aplicacion(self):
        if self.cerrando:
            return
        self.cerrando = True
        self._detener_ping()
        for chat in list(self.chats.values()):
            chat.cerrar()
        self.chats.clear()
        if self.principal:
            self.principal.detener()
        token = self.token
        self.token = None
        self.root.withdraw()
        try:
            if token:
                rpc("logout", token)
        except Exception:
            pass
        self.root.destroy()


class VistaLogin(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, bg=FONDO)
        self.app = app
        self.modo_registro = False
        self.ocupado = False
        self.var_nickname = tk.StringVar()
        self.var_password = tk.StringVar()
        self.var_confirmacion = tk.StringVar()
        self._construir()

    def _construir(self):
        contenedor = tk.Frame(self, bg=FONDO)
        contenedor.pack(fill="both", expand=True, padx=28, pady=24)

        tarjeta = tk.Frame(
            contenedor,
            bg=TARJETA,
            highlightbackground=BORDE,
            highlightthickness=1,
        )
        tarjeta.pack(fill="both", expand=True)
        tk.Frame(tarjeta, bg=ACENTO, height=5).pack(fill="x")

        interior = tk.Frame(tarjeta, bg=TARJETA)
        interior.pack(fill="both", expand=True, padx=34, pady=28)

        avatar = tk.Canvas(interior, width=76, height=76, bg=TARJETA, highlightthickness=0)
        avatar.pack()
        avatar.create_oval(4, 4, 72, 72, fill=ACENTO, outline="")
        avatar.create_text(38, 37, text="M", fill="#FFFFFF", font=(FUENTE, 31, "bold"))

        self.titulo = ttk.Label(interior, text="Bienvenido de nuevo", style="Titulo.TLabel")
        self.titulo.pack(pady=(12, 2))
        self.subtitulo = ttk.Label(
            interior,
            text="Inicia sesión para conversar con tus contactos",
            style="Subtitulo.TLabel",
        )
        self.subtitulo.pack(pady=(0, 18))

        formulario = tk.Frame(interior, bg=TARJETA)
        formulario.pack(fill="x")
        formulario.columnconfigure(0, weight=1)

        ttk.Label(formulario, text="Nickname", style="Campo.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        self.entrada_nickname = ttk.Entry(
            formulario,
            textvariable=self.var_nickname,
            style="Login.TEntry",
        )
        self.entrada_nickname.grid(row=1, column=0, sticky="ew", pady=(0, 12))

        ttk.Label(formulario, text="Contraseña", style="Campo.TLabel").grid(
            row=2, column=0, sticky="w", pady=(0, 4)
        )
        self.entrada_password = ttk.Entry(
            formulario,
            textvariable=self.var_password,
            show="*",
            style="Login.TEntry",
        )
        self.entrada_password.grid(row=3, column=0, sticky="ew", pady=(0, 12))

        self.label_confirmacion = ttk.Label(
            formulario,
            text="Confirmar contraseña",
            style="Campo.TLabel",
        )
        self.entrada_confirmacion = ttk.Entry(
            formulario,
            textvariable=self.var_confirmacion,
            show="*",
            style="Login.TEntry",
        )

        self.estado = tk.Label(
            interior,
            text=" ",
            bg=TARJETA,
            fg=ERROR,
            font=(FUENTE, 9),
            wraplength=320,
            justify="center",
        )
        self.estado.pack(fill="x", pady=(0, 12))

        self.boton_principal = ttk.Button(
            interior,
            text="Iniciar sesión",
            style="Accent.TButton",
            command=self._enviar_formulario,
        )
        self.boton_principal.pack(fill="x")

        self.boton_secundario = ttk.Button(
            interior,
            text="Registrarse",
            style="Secondary.TButton",
            command=self._alternar_modo,
        )
        self.boton_secundario.pack(fill="x", pady=(8, 0))

        self.pista = tk.Label(
            interior,
            text="Mínimo 8 caracteres. Tus contraseñas se almacenan con hash y sal.",
            bg=TARJETA,
            fg=TEXTO_SUAVE,
            font=(FUENTE, 8),
            wraplength=320,
            justify="center",
        )
        self.pista.pack(pady=(16, 0))

        pie = tk.Label(
            self,
            text=f"Servidor: {URL_SERVIDOR.replace('http://', '').replace('https://', '')}",
            bg=FONDO,
            fg=TEXTO_SUAVE,
            font=(FUENTE, 8),
        )
        pie.pack(pady=(0, 8))

        self.entrada_nickname.bind("<Return>", lambda evento: self._enviar_formulario())
        self.entrada_password.bind("<Return>", lambda evento: self._enviar_formulario())
        self.entrada_confirmacion.bind("<Return>", lambda evento: self._enviar_formulario())
        self.entrada_nickname.focus_set()

    def _alternar_modo(self):
        if self.ocupado:
            return
        self.modo_registro = not self.modo_registro
        self.var_password.set("")
        self.var_confirmacion.set("")
        self.estado.configure(text=" ", fg=ERROR)

        if self.modo_registro:
            self.titulo.configure(text="Crea tu cuenta")
            self.subtitulo.configure(text="Elige un nickname único y una contraseña segura")
            self.boton_principal.configure(text="Crear cuenta")
            self.boton_secundario.configure(text="Volver al inicio de sesión")
            self.label_confirmacion.grid(row=4, column=0, sticky="w", pady=(0, 4))
            self.entrada_confirmacion.grid(row=5, column=0, sticky="ew", pady=(0, 12))
            self.pista.configure(text="Nickname sin espacios. Contraseña de 8 a 128 caracteres.")
            self.entrada_confirmacion.focus_set()
        else:
            self.titulo.configure(text="Bienvenido de nuevo")
            self.subtitulo.configure(text="Inicia sesión para conversar con tus contactos")
            self.boton_principal.configure(text="Iniciar sesión")
            self.boton_secundario.configure(text="Registrarse")
            self.label_confirmacion.grid_remove()
            self.entrada_confirmacion.grid_remove()
            self.pista.configure(text="Mínimo 8 caracteres. Tus contraseñas se almacenan con hash y sal.")
            self.entrada_password.focus_set()

    def _mostrar_estado(self, texto, color=ERROR):
        self.estado.configure(text=texto, fg=color)

    def _cambiar_ocupado(self, ocupado):
        self.ocupado = ocupado
        estado = ["disabled"] if ocupado else ["!disabled"]
        for widget in (
            self.entrada_nickname,
            self.entrada_password,
            self.entrada_confirmacion,
            self.boton_principal,
            self.boton_secundario,
        ):
            widget.state(estado)

    def _enviar_formulario(self):
        if self.ocupado:
            return
        nickname = self.var_nickname.get().strip()
        password = self.var_password.get()

        if not nickname:
            self._mostrar_estado("Escribe un nickname.")
            self.entrada_nickname.focus_set()
            return
        if any(caracter.isspace() for caracter in nickname):
            self._mostrar_estado("El nickname no puede contener espacios.")
            self.entrada_nickname.focus_set()
            return

        if self.modo_registro:
            if len(password) < 8:
                self._mostrar_estado("La contraseña debe tener al menos 8 caracteres.")
                self.entrada_password.focus_set()
                return
            if len(password) > 128:
                self._mostrar_estado("La contraseña no puede superar los 128 caracteres.")
                return
            if password != self.var_confirmacion.get():
                self._mostrar_estado("Las contraseñas no coinciden.")
                self.entrada_confirmacion.focus_set()
                return
            self._cambiar_ocupado(True)
            self._mostrar_estado("Creando cuenta...", TEXTO_SUAVE)
            self.app.ejecutar_async(
                lambda: rpc("registrar", nickname, password),
                self._registro_terminado,
            )
            return

        if not password:
            self._mostrar_estado("Escribe tu contraseña.")
            self.entrada_password.focus_set()
            return
        self._cambiar_ocupado(True)
        self._mostrar_estado("Iniciando sesión...", TEXTO_SUAVE)
        self.app.ejecutar_async(
            lambda: rpc("login", nickname, password),
            self._login_terminado,
        )

    def _login_terminado(self, resultado, error):
        if not self.winfo_exists():
            return
        self._cambiar_ocupado(False)
        if error:
            self.var_password.set("")
            self.entrada_password.focus_set()
            self._mostrar_estado(describir_error(error))
            return
        if not resultado:
            self.var_password.set("")
            self.entrada_password.focus_set()
            self._mostrar_estado("Nickname o contraseña incorrectos.")
            return
        nickname = self.var_nickname.get().strip()
        self.app.login_exitoso(str(resultado), nickname)

    def _registro_terminado(self, resultado, error):
        if not self.winfo_exists():
            return
        self._cambiar_ocupado(False)
        if error:
            self._mostrar_estado(describir_error(error))
            return
        if resultado is True:
            self.var_password.set("")
            self.var_confirmacion.set("")
            if self.modo_registro:
                self._alternar_modo()
            self._mostrar_estado("Cuenta creada. Ahora inicia sesión.", EXITO)
            self.entrada_password.focus_set()
            return
        self._mostrar_estado(str(resultado) if resultado else "No se pudo crear la cuenta.")


class VentanaPrincipal(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, bg=FONDO)
        self.app = app
        self.directorio = []
        self._cerrada = False
        self._dir_cargando = False
        self._job_directorio = None
        self._ids_conocidos = {}
        self._novedades_cargando = False
        self._job_novedades = None
        self.var_busqueda = tk.StringVar()
        self._construir()
        self.var_busqueda.trace_add("write", lambda *args: self._pintar_directorio())
        self.actualizar_directorio()
        self._programar_novedades(1_500)

    def _construir(self):
        sidebar = tk.Frame(self, bg=SIDEBAR, width=238)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        marca = tk.Frame(sidebar, bg=SIDEBAR)
        marca.pack(fill="x", padx=22, pady=(24, 32))
        logo = tk.Canvas(marca, width=38, height=38, bg=SIDEBAR, highlightthickness=0)
        logo.pack(side="left")
        logo.create_oval(2, 2, 36, 36, fill=ACENTO, outline="")
        logo.create_text(19, 19, text="M", fill="#FFFFFF", font=(FUENTE, 16, "bold"))
        tk.Label(
            marca,
            text="Mensajero",
            bg=SIDEBAR,
            fg="#FFFFFF",
            font=(FUENTE, 15, "bold"),
        ).pack(side="left", padx=(10, 0))

        perfil = tk.Frame(sidebar, bg=SIDEBAR)
        perfil.pack(fill="x", padx=22, pady=(0, 24))
        avatar = tk.Canvas(perfil, width=52, height=52, bg=SIDEBAR, highlightthickness=0)
        avatar.pack()
        avatar.create_oval(2, 2, 50, 50, fill=SIDEBAR_ACTIVO, outline="")
        inicial = self.app.nickname[:1].upper() if self.app.nickname else "?"
        avatar.create_text(26, 26, text=inicial, fill="#FFFFFF", font=(FUENTE, 19, "bold"))
        tk.Label(
            perfil,
            text=self.app.nickname,
            bg=SIDEBAR,
            fg="#FFFFFF",
            font=(FUENTE, 11, "bold"),
        ).pack(pady=(8, 1))
        tk.Label(
            perfil,
            text="● En línea",
            bg=SIDEBAR,
            fg="#55D6A7",
            font=(FUENTE, 8),
        ).pack()

        activo = tk.Frame(sidebar, bg=SIDEBAR_ACTIVO)
        activo.pack(fill="x", padx=14, pady=(4, 0))
        tk.Label(
            activo,
            text="Contactos",
            bg=SIDEBAR_ACTIVO,
            fg="#FFFFFF",
            font=(FUENTE, 10, "bold"),
            anchor="w",
            padx=14,
            pady=11,
        ).pack(fill="x")

        tk.Frame(sidebar, bg=SIDEBAR).pack(fill="both", expand=True)

        tk.Label(
            sidebar,
            text="XML-RPC · 8000",
            bg=SIDEBAR,
            fg="#7F8CA3",
            font=(FUENTE, 8),
        ).pack(pady=(0, 10))

        salir = tk.Button(
            sidebar,
            text="Cerrar sesión",
            command=self.app.cerrar_sesion,
            bg=SIDEBAR,
            fg="#F1A5A5",
            activebackground=SIDEBAR_ACTIVO,
            activeforeground="#FFCACA",
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            font=(FUENTE, 9, "bold"),
            padx=12,
            pady=10,
        )
        salir.pack(fill="x", padx=14, pady=(0, 18))

        contenido = tk.Frame(self, bg=TARJETA)
        contenido.pack(side="left", fill="both", expand=True)

        encabezado = tk.Frame(contenido, bg=TARJETA)
        encabezado.pack(fill="x", padx=30, pady=(26, 6))
        bloque = tk.Frame(encabezado, bg=TARJETA)
        bloque.pack(side="left")
        tk.Label(
            bloque,
            text="Contactos",
            bg=TARJETA,
            fg=TEXTO,
            font=(FUENTE, 24, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            bloque,
            text="Selecciona un contacto para abrir una conversación",
            bg=TARJETA,
            fg=TEXTO_SUAVE,
            font=(FUENTE, 9),
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))
        self.boton_refrescar = ttk.Button(
            encabezado,
            text="Actualizar",
            style="Secondary.TButton",
            command=self.actualizar_directorio,
        )
        self.boton_refrescar.pack(side="right", pady=(8, 0))

        busqueda = tk.Frame(contenido, bg=TARJETA)
        busqueda.pack(fill="x", padx=30, pady=(18, 10))
        tk.Label(
            busqueda,
            text="Buscar",
            bg=TARJETA,
            fg=TEXTO_SUAVE,
            font=(FUENTE, 8, "bold"),
        ).pack(anchor="w", pady=(0, 4))
        ttk.Entry(
            busqueda,
            textvariable=self.var_busqueda,
            style="Buscar.TEntry",
        ).pack(fill="x")

        tabla = tk.Frame(contenido, bg=TARJETA)
        tabla.pack(fill="both", expand=True, padx=30, pady=(0, 8))
        tabla.rowconfigure(0, weight=1)
        tabla.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            tabla,
            columns=("estado", "nickname"),
            show="headings",
            selectmode="browse",
        )
        self.tree.heading("estado", text="Estado", anchor="w")
        self.tree.heading("nickname", text="Contacto", anchor="w")
        self.tree.column("estado", width=170, minwidth=150, stretch=False, anchor="w")
        self.tree.column("nickname", width=360, minwidth=180, stretch=True, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.tag_configure("online", foreground=EN_LINEA)
        self.tree.tag_configure("offline", foreground=DESCONECTADO)
        self.tree.tag_configure(
            "unread",
            foreground=ACENTO,
            background="#EEF4FF",
            font=(FUENTE, 10, "bold"),
        )
        self.tree.bind("<Double-1>", self._abrir_desde_clic)
        self.tree.bind("<Return>", lambda evento: self._abrir_seleccionado())

        scrollbar = ttk.Scrollbar(tabla, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        pie = tk.Frame(contenido, bg=TARJETA)
        pie.pack(fill="x", padx=30, pady=(0, 22))
        self.lbl_estado = tk.Label(
            pie,
            text="Cargando contactos...",
            bg=TARJETA,
            fg=TEXTO_SUAVE,
            font=(FUENTE, 8),
            anchor="w",
        )
        self.lbl_estado.pack(side="left")
        tk.Label(
            pie,
            text="Doble clic para conversar",
            bg=TARJETA,
            fg=TEXTO_SUAVE,
            font=(FUENTE, 8),
            anchor="e",
        ).pack(side="right")

    def actualizar_directorio(self):
        if self._cerrada:
            return
        if self._job_directorio:
            try:
                self.after_cancel(self._job_directorio)
            except tk.TclError:
                pass
            self._job_directorio = None
        if self._dir_cargando:
            self._job_directorio = self.after(INTERVALO_DIRECTORIO_MS, self.actualizar_directorio)
            return
        self._dir_cargando = True
        self.boton_refrescar.state(["disabled"])
        self.lbl_estado.configure(text="Actualizando contactos...")
        self.app.ejecutar_async(
            lambda: rpc("obtener_directorio", self.app.token),
            self._directorio_recibido,
        )
        self._job_directorio = self.after(INTERVALO_DIRECTORIO_MS, self.actualizar_directorio)

    def _directorio_recibido(self, resultado, error):
        self._dir_cargando = False
        if self._cerrada:
            return
        self.boton_refrescar.state(["!disabled"])
        if error:
            if error_de_sesion(error):
                self.app.sesion_expirada()
                return
            self.lbl_estado.configure(text="Sin conexión con el servidor. Reintentando...")
            return
        self.directorio = list(resultado or [])
        self._pintar_directorio()

    def _valores_contacto(self, nickname, conectado):
        estado = "🟢  En línea" if conectado else "⚪  Desconectado"
        no_leido = self.app.esta_no_leido(nickname)
        if no_leido:
            estado = f"{estado}  💬"
        etiqueta = "online" if conectado else "offline"
        tags = (etiqueta, "unread") if no_leido else (etiqueta,)
        return (estado, nickname), tags

    def _pintar_directorio(self):
        if self._cerrada:
            return
        filtro = self.var_busqueda.get().strip().casefold()
        for item in self.tree.get_children():
            self.tree.delete(item)

        visibles = 0
        en_linea = 0
        for contacto in self.directorio:
            nickname = str(contacto.get("nickname", ""))
            if not nickname:
                continue
            if nickname.casefold() == self.app.nickname.casefold():
                continue
            conectado = bool(contacto.get("conectado"))
            if filtro and filtro not in nickname.casefold():
                continue
            visibles += 1
            if conectado:
                en_linea += 1
            valores, tags = self._valores_contacto(nickname, conectado)
            self.tree.insert(
                "",
                "end",
                iid=nickname,
                values=valores,
                tags=tags,
            )

        if visibles:
            self.lbl_estado.configure(
                text=f"{visibles} contacto(s) · {en_linea} en línea"
            )
        elif filtro:
            self.lbl_estado.configure(text="No hay contactos que coincidan con la búsqueda.")
        else:
            self.lbl_estado.configure(text="No hay otros usuarios registrados todavía.")

    def actualizar_marca_contacto(self, nickname):
        if self._cerrada or not self.tree.exists(nickname):
            return
        contacto = next(
            (
                item for item in self.directorio
                if str(item.get("nickname", "")).casefold() == nickname.casefold()
            ),
            None,
        )
        if not contacto:
            return
        valores, tags = self._valores_contacto(
            str(contacto.get("nickname", nickname)),
            bool(contacto.get("conectado")),
        )
        self.tree.item(nickname, values=valores, tags=tags)

    def actualizar_ids_conocidos(self, contacto, ids):
        self._ids_conocidos[contacto] = set(ids)

    def _programar_novedades(self, retraso=INTERVALO_NOVEDADES_MS):
        if self._cerrada:
            return
        self._job_novedades = self.after(retraso, self._revisar_novedades)

    def _revisar_novedades(self):
        if self._cerrada:
            return
        if self._novedades_cargando:
            self._programar_novedades()
            return
        abiertos = {contacto.casefold() for contacto in self.app.chats}
        contactos = []
        for contacto in self.directorio:
            nickname = str(contacto.get("nickname", ""))
            if not nickname or nickname.casefold() == self.app.nickname.casefold():
                continue
            if nickname.casefold() in abiertos:
                continue
            contactos.append(nickname)
        if not contactos:
            self._programar_novedades()
            return

        self._novedades_cargando = True
        token = self.app.token

        def tarea():
            resultados = {}
            for nickname in contactos:
                resultados[nickname] = rpc("obtener_historial", token, nickname, False)
            return resultados

        self.app.ejecutar_async(tarea, self._novedades_recibidas)
        self._programar_novedades()

    def _novedades_recibidas(self, resultado, error):
        self._novedades_cargando = False
        if self._cerrada:
            return
        if error:
            if error_de_sesion(error):
                self.app.sesion_expirada()
            return
        abiertos = {contacto.casefold() for contacto in self.app.chats}
        for nickname, historial in (resultado or {}).items():
            if nickname.casefold() in abiertos:
                continue
            ids = {
                mensaje.get("id")
                for mensaje in historial
                if mensaje.get("id") is not None
            }
            conocidos = self._ids_conocidos.get(nickname)
            if conocidos is None:
                self._ids_conocidos[nickname] = ids
                continue
            nuevos = [
                mensaje for mensaje in historial
                if mensaje.get("id") not in conocidos
            ]
            self._ids_conocidos[nickname] = conocidos | ids
            if any(
                str(mensaje.get("de", "")).casefold() != self.app.nickname.casefold()
                for mensaje in nuevos
            ):
                self.app.marcar_no_leido(nickname)

    def _abrir_desde_clic(self, evento):
        fila = self.tree.identify_row(evento.y)
        if fila:
            self.app.abrir_chat(fila)

    def _abrir_seleccionado(self):
        seleccion = self.tree.selection()
        if not seleccion:
            return
        self.app.abrir_chat(seleccion[0])

    def detener(self):
        self._cerrada = True
        for trabajo in (self._job_directorio, self._job_novedades):
            if trabajo:
                try:
                    self.after_cancel(trabajo)
                except tk.TclError:
                    pass
        self._job_directorio = None
        self._job_novedades = None


class VentanaChat:
    def __init__(self, app, contacto):
        self.app = app
        self.contacto = contacto
        self._titulo_base = f"Conversación con ({self.contacto})"
        self.ventana = tk.Toplevel(app.root)
        self.ventana.title(self._titulo_base)
        self.ventana.configure(bg=FONDO)
        self.ventana.minsize(500, 540)
        self.ventana.transient(app.root)
        self.ventana.protocol("WM_DELETE_WINDOW", self.cerrar)

        ancho = 640
        alto = 690
        x = app.root.winfo_rootx() + 110 + len(app.chats) * 28
        y = app.root.winfo_rooty() + 50 + len(app.chats) * 28
        self.ventana.geometry(f"{ancho}x{alto}+{x}+{y}")

        self._cerrada = False
        self._hist_cargando = False
        self._job_historial = None
        self._ids_vistos = set()
        self._entregas = {}
        self._primera_carga = True
        self._placeholder = False
        self._enviando = False
        self._enviando_archivo = False
        self._alerta_activa = False
        self._job_parpadeo = None

        self._construir()
        self.ventana.bind("<FocusIn>", self._al_recibir_foco)
        self._cargar_historial()
        self._programar_refresco()
        self.entrada.focus_set()

    def _construir(self):
        barra = tk.Frame(self.ventana, bg=TARJETA, height=78)
        barra.pack(fill="x")
        barra.pack_propagate(False)

        avatar = tk.Canvas(barra, width=44, height=44, bg=TARJETA, highlightthickness=0)
        avatar.pack(side="left", padx=(20, 12), pady=17)
        avatar.create_oval(2, 2, 42, 42, fill="#E8F0FF", outline="")
        avatar.create_text(
            22,
            22,
            text=self.contacto[:1].upper(),
            fill=ACENTO,
            font=(FUENTE, 16, "bold"),
        )

        datos = tk.Frame(barra, bg=TARJETA)
        datos.pack(side="left", pady=17)
        tk.Label(
            datos,
            text=self.contacto,
            bg=TARJETA,
            fg=TEXTO,
            font=(FUENTE, 12, "bold"),
            anchor="w",
        ).pack(anchor="w")
        self.lbl_presencia = tk.Label(
            datos,
            text="Chat privado",
            bg=TARJETA,
            fg=TEXTO_SUAVE,
            font=(FUENTE, 8),
            anchor="w",
        )
        self.lbl_presencia.pack(anchor="w", pady=(1, 0))

        ttk.Button(
            barra,
            text="Borrar historial",
            style="Small.TButton",
            command=self._borrar_historial,
        ).pack(side="right", padx=18)

        tk.Frame(self.ventana, bg=BORDE, height=1).pack(fill="x")

        cuerpo = tk.Frame(self.ventana, bg=TARJETA)
        cuerpo.pack(fill="both", expand=True)

        self.texto = tk.Text(
            cuerpo,
            wrap="word",
            state="disabled",
            bg=TARJETA,
            fg=TEXTO,
            relief="flat",
            borderwidth=0,
            padx=18,
            pady=12,
            font=(FUENTE, 10),
            selectbackground="#C9DAFB",
        )
        self.texto.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(cuerpo, orient="vertical", command=self.texto.yview)
        scroll.pack(side="right", fill="y")
        self.texto.configure(yscrollcommand=scroll.set)

        self.texto.tag_configure(
            "placeholder",
            foreground=TEXTO_SUAVE,
            font=(FUENTE, 10),
            justify="center",
            spacing1=48,
        )
        self.texto.tag_configure(
            "yo_nombre",
            foreground=ACENTO,
            font=(FUENTE, 8, "bold"),
            justify="right",
            spacing1=8,
        )
        self.texto.tag_configure(
            "otro_nombre",
            foreground="#475569",
            font=(FUENTE, 8, "bold"),
            justify="left",
            spacing1=8,
        )
        self.texto.tag_configure(
            "yo_texto",
            foreground=TEXTO,
            background="#E8F0FF",
            justify="right",
            lmargin1=130,
            lmargin2=130,
            rmargin=8,
            spacing3=8,
        )
        self.texto.tag_configure(
            "otro_texto",
            foreground=TEXTO,
            background="#F2F4F7",
            justify="left",
            lmargin1=8,
            lmargin2=8,
            rmargin=130,
            spacing3=8,
        )
        self.texto.tag_configure(
            "archivo_texto",
            foreground=ACENTO_OSCURO,
            font=(FUENTE, 9, "underline"),
        )
        self.texto.tag_configure(
            "entregado",
            foreground=ACENTO,
            font=(FUENTE, 8, "bold"),
        )
        self.texto.tag_configure(
            "pendiente",
            foreground=TEXTO_SUAVE,
            font=(FUENTE, 8),
        )

        tk.Frame(self.ventana, bg=BORDE, height=1).pack(fill="x")

        compositor = tk.Frame(self.ventana, bg=TARJETA)
        compositor.pack(fill="x", padx=16, pady=(12, 14))

        fila = tk.Frame(compositor, bg=TARJETA)
        fila.pack(fill="x")

        self.boton_adjuntar = ttk.Button(
            fila,
            text="Adjuntar",
            style="Secondary.TButton",
            command=self._adjuntar_archivo,
        )
        self.boton_adjuntar.pack(side="left", padx=(0, 8), pady=(0, 2))

        self.entrada = tk.Text(
            fila,
            height=2,
            wrap="word",
            bg="#F7F9FC",
            fg=TEXTO,
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=BORDE,
            highlightcolor=ACENTO,
            font=(FUENTE, 10),
            padx=10,
            pady=8,
            insertbackground=TEXTO,
        )
        self.boton_enviar = ttk.Button(
            fila,
            text="Enviar",
            style="Accent.TButton",
            command=self._enviar_mensaje,
        )
        self.boton_enviar.pack(side="right", pady=(0, 2))

        self.entrada.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.entrada.bind("<Return>", self._al_presionar_enter)

        self.lbl_estado = tk.Label(
            compositor,
            text="Enter para enviar · Shift+Enter para una nueva línea",
            bg=TARJETA,
            fg=TEXTO_SUAVE,
            font=(FUENTE, 8),
            anchor="w",
        )
        self.lbl_estado.pack(fill="x", pady=(8, 0))

    def _al_presionar_enter(self, evento):
        if evento.state & 0x0001:
            return None
        self._enviar_mensaje()
        return "break"

    def _cargar_historial(self):
        if self._cerrada or self._hist_cargando:
            return
        self._hist_cargando = True
        self.app.ejecutar_async(
            lambda: rpc("obtener_historial", self.app.token, self.contacto),
            self._historial_recibido,
        )

    def _programar_refresco(self):
        if self._cerrada:
            return
        self._job_historial = self.ventana.after(
            INTERVALO_HISTORIAL_MS,
            self._refrescar,
        )

    def _refrescar(self):
        if self._cerrada:
            return
        self._cargar_historial()
        self._programar_refresco()

    def _historial_recibido(self, resultado, error):
        self._hist_cargando = False
        if self._cerrada or not self.ventana.winfo_exists():
            return
        if error:
            if error_de_sesion(error):
                self.app.sesion_expirada()
                return
            self.lbl_estado.configure(text=f"No se pudo actualizar: {describir_error(error)}")
            return

        historial = list(resultado or [])
        ids_servidor = {mensaje.get("id") for mensaje in historial}
        self.app.actualizar_ids_conocidos(self.contacto, ids_servidor)

        primera_carga = self._primera_carga
        self._primera_carga = False
        rehacer = bool(self._ids_vistos and not self._ids_vistos.issubset(ids_servidor))
        cambio_entrega = self._actualizar_estados_entrega(historial)
        nuevos = [
            mensaje for mensaje in historial
            if mensaje.get("id") not in self._ids_vistos
        ]
        entrantes = [
            mensaje for mensaje in nuevos
            if str(mensaje.get("de", "")).casefold() != self.app.nickname.casefold()
        ]
        notificar = bool(entrantes) and not primera_carga and not self._esta_enfocada()

        if not self._enviando and not self._enviando_archivo:
            self.lbl_estado.configure(text="Enter para enviar · Shift+Enter para una nueva línea")

        if rehacer or cambio_entrega:
            self._render_completo(historial)
            if notificar:
                self._activar_alerta()
            return

        if not nuevos:
            if not self._ids_vistos:
                self._mostrar_placeholder()
            self._actualizar_presencia()
            return

        if self._placeholder:
            self._limpiar_texto()
        for mensaje in nuevos:
            self._insertar_mensaje(mensaje)
            self._ids_vistos.add(mensaje.get("id"))
        self._desplazar_al_final()
        self._actualizar_presencia()
        if notificar:
            self._activar_alerta()

    def _actualizar_estados_entrega(self, historial):
        cambio = False
        actuales = {}
        for mensaje in historial:
            if str(mensaje.get("de", "")).casefold() != self.app.nickname.casefold():
                continue
            mensaje_id = mensaje.get("id")
            estado = bool(mensaje.get("entregado"))
            if mensaje_id in self._entregas and self._entregas[mensaje_id] != estado:
                cambio = True
            actuales[mensaje_id] = estado
        self._entregas = actuales
        return cambio

    def _esta_enfocada(self):
        try:
            if self.ventana.state() not in ("normal", "zoomed"):
                return False
            foco = self.ventana.focus_get()
            if foco is None:
                return False
            ruta_ventana = str(self.ventana)
            ruta_foco = str(foco)
            return ruta_foco == ruta_ventana or ruta_foco.startswith(ruta_ventana + ".")
        except (tk.TclError, KeyError):
            return False

    def _activar_alerta(self):
        if self._cerrada or self._esta_enfocada():
            return
        self.app.marcar_no_leido(self.contacto)
        if self._alerta_activa:
            return
        self._alerta_activa = True
        try:
            self.ventana.bell()
        except tk.TclError:
            pass
        self._parpadear()

    def _parpadear(self):
        if self._cerrada or not self._alerta_activa:
            return
        if self.ventana.title() == self._titulo_base:
            self.ventana.title(f"¡Nuevo mensaje! - ({self.contacto})")
        else:
            self.ventana.title(self._titulo_base)
        self._job_parpadeo = self.ventana.after(900, self._parpadear)

    def _detener_alerta(self):
        self._alerta_activa = False
        if self._job_parpadeo:
            try:
                self.ventana.after_cancel(self._job_parpadeo)
            except tk.TclError:
                pass
            self._job_parpadeo = None
        self.ventana.title(self._titulo_base)
        self.app.marcar_leido(self.contacto)

    def _al_recibir_foco(self, evento=None):
        self._detener_alerta()

    def marcar_leido(self):
        self._detener_alerta()

    def _actualizar_presencia(self):
        estado = self.app.estado_contacto(self.contacto)
        if estado is True:
            self.lbl_presencia.configure(text="● En línea", fg=EN_LINEA)
        elif estado is False:
            self.lbl_presencia.configure(text="○ Desconectado", fg=DESCONECTADO)
        else:
            self.lbl_presencia.configure(text="Chat privado", fg=TEXTO_SUAVE)

    def _limpiar_texto(self):
        self.texto.configure(state="normal")
        self.texto.delete("1.0", "end")
        self.texto.configure(state="disabled")
        self._placeholder = False

    def _mostrar_placeholder(self):
        if self._placeholder:
            return
        self.texto.configure(state="normal")
        self.texto.delete("1.0", "end")
        self.texto.insert(
            "end",
            "Aún no hay mensajes.\nEscribe el primero para iniciar la conversación.",
            "placeholder",
        )
        self.texto.configure(state="disabled")
        self._placeholder = True

    def _render_completo(self, historial):
        self._ids_vistos.clear()
        self._limpiar_texto()
        if not historial:
            self._mostrar_placeholder()
            self._actualizar_presencia()
            return
        for mensaje in historial:
            self._insertar_mensaje(mensaje)
            self._ids_vistos.add(mensaje.get("id"))
        self._desplazar_al_final()
        self._actualizar_presencia()

    def _insertar_mensaje(self, mensaje):
        propio = str(mensaje.get("de", "")).casefold() == self.app.nickname.casefold()
        nombre = "Tú" if propio else str(mensaje.get("de", self.contacto))
        fecha = formatear_fecha(mensaje.get("fecha", ""))
        etiqueta_nombre = "yo_nombre" if propio else "otro_nombre"
        etiqueta_texto = "yo_texto" if propio else "otro_texto"

        self.texto.configure(state="normal")
        self.texto.insert("end", f"{nombre} · {fecha}", etiqueta_nombre)
        if propio:
            if mensaje.get("entregado"):
                self.texto.insert("end", "  ✓✓", "entregado")
            else:
                self.texto.insert("end", "  ✓", "pendiente")
        self.texto.insert("end", "\n", etiqueta_nombre)

        if mensaje.get("tipo") == "archivo":
            nombre_archivo = str(mensaje.get("nombre_archivo", "archivo"))
            tamano = tamano_legible(mensaje.get("tamano", 0))
            icono, tipo_archivo = clasificar_archivo(nombre_archivo)
            etiqueta_archivo = f"archivo_{mensaje.get('id', id(mensaje))}"
            self.texto.insert(
                "end",
                f"{icono} {tipo_archivo}: {nombre_archivo} ({tamano}) · clic para guardar\n",
                (etiqueta_texto, etiqueta_archivo, "archivo_texto"),
            )
            self.texto.tag_bind(
                etiqueta_archivo,
                "<Button-1>",
                lambda evento, datos=mensaje: self._guardar_adjunto(datos),
            )
        else:
            self.texto.insert(
                "end",
                f"{mensaje.get('mensaje', '')}\n",
                etiqueta_texto,
            )
        self.texto.configure(state="disabled")

    def _desplazar_al_final(self):
        self.texto.see("end")

    def _enviar_mensaje(self):
        if self._enviando or self._cerrada:
            return
        texto = self.entrada.get("1.0", "end-1c").strip()
        if not texto:
            return
        if len(texto) > MAX_MENSAJE:
            messagebox.showerror(
                "Mensaje demasiado largo",
                f"El mensaje no puede superar los {MAX_MENSAJE} caracteres.",
                parent=self.ventana,
            )
            return

        self._enviando = True
        self.boton_enviar.state(["disabled"])
        self.lbl_estado.configure(text="Enviando mensaje...")
        self.entrada.delete("1.0", "end")

        def tarea():
            return rpc("enviar_mensaje", self.app.token, self.contacto, texto)

        self.app.ejecutar_async(tarea, lambda resultado, error: self._mensaje_enviado(texto, resultado, error))

    def _mensaje_enviado(self, texto, resultado, error):
        if self._cerrada or not self.ventana.winfo_exists():
            return
        self._enviando = False
        self.boton_enviar.state(["!disabled"])
        self.lbl_estado.configure(text="Enter para enviar · Shift+Enter para una nueva línea")
        if error:
            if error_de_sesion(error):
                self.app.sesion_expirada()
                return
            if not self.entrada.get("1.0", "end-1c").strip():
                self.entrada.insert("1.0", texto)
            messagebox.showerror("No se pudo enviar", describir_error(error), parent=self.ventana)
            return
        self._cargar_historial()

    def _adjuntar_archivo(self):
        if self._enviando_archivo or self._cerrada:
            return
        ruta = filedialog.askopenfilename(
            parent=self.ventana,
            title="Adjuntar archivo",
            filetypes=(("Todos los archivos", "*.*"),),
        )
        if not ruta:
            return

        nombre = os.path.basename(ruta)
        self._enviando_archivo = True
        self.boton_adjuntar.state(["disabled"])
        self.lbl_estado.configure(text=f"Enviando {nombre}...")

        def tarea():
            with open(ruta, "rb") as archivo:
                contenido = archivo.read(MAX_ARCHIVO_BYTES + 1)
            if len(contenido) > MAX_ARCHIVO_BYTES:
                raise ValueError("El archivo supera el límite de 5 MB.")
            return rpc(
                "enviar_archivo",
                self.app.token,
                self.contacto,
                nombre,
                xmlrpc.client.Binary(contenido),
            )

        self.app.ejecutar_async(tarea, self._archivo_enviado)

    def _archivo_enviado(self, resultado, error):
        if self._cerrada or not self.ventana.winfo_exists():
            return
        self._enviando_archivo = False
        self.boton_adjuntar.state(["!disabled"])
        self.lbl_estado.configure(text="Enter para enviar · Shift+Enter para una nueva línea")
        if error:
            if error_de_sesion(error):
                self.app.sesion_expirada()
                return
            messagebox.showerror("No se pudo enviar el archivo", describir_error(error), parent=self.ventana)
            return
        self._cargar_historial()

    def _guardar_adjunto(self, mensaje):
        datos = mensaje.get("datos")
        if isinstance(datos, xmlrpc.client.Binary):
            contenido = datos.data
        elif isinstance(datos, (bytes, bytearray)):
            contenido = bytes(datos)
        else:
            messagebox.showerror("Archivo no disponible", "No se recibieron los datos del archivo.", parent=self.ventana)
            return

        nombre = str(mensaje.get("nombre_archivo", "archivo"))
        ruta = filedialog.asksaveasfilename(
            parent=self.ventana,
            title="Guardar archivo",
            initialfile=nombre,
        )
        if not ruta:
            return
        try:
            with open(ruta, "wb") as archivo:
                archivo.write(contenido)
        except OSError as error:
            messagebox.showerror("No se pudo guardar", str(error), parent=self.ventana)
            return
        messagebox.showinfo("Archivo guardado", f"Se guardó en:\n{ruta}", parent=self.ventana)

    def _borrar_historial(self):
        if not messagebox.askyesno(
            "Borrar historial",
            f"¿Borrar toda la conversación con {self.contacto}?",
            parent=self.ventana,
        ):
            return

        def tarea():
            return rpc("borrar_historial", self.app.token, self.contacto)

        self.app.ejecutar_async(tarea, self._historial_borrado)

    def _historial_borrado(self, resultado, error):
        if self._cerrada or not self.ventana.winfo_exists():
            return
        if error:
            if error_de_sesion(error):
                self.app.sesion_expirada()
                return
            messagebox.showerror("No se pudo borrar", describir_error(error), parent=self.ventana)
            return
        self._ids_vistos.clear()
        self._entregas.clear()
        self.app.actualizar_ids_conocidos(self.contacto, set())
        self._limpiar_texto()
        self._mostrar_placeholder()
        self.lbl_estado.configure(text="Historial borrado.")

    def cerrar(self):
        if self._cerrada:
            return
        self._cerrada = True
        for trabajo in (self._job_historial, self._job_parpadeo):
            if trabajo:
                try:
                    self.ventana.after_cancel(trabajo)
                except tk.TclError:
                    pass
        self._job_historial = None
        self._job_parpadeo = None
        self.app.chats.pop(self.contacto, None)
        try:
            self.ventana.destroy()
        except tk.TclError:
            pass


def main():
    root = tk.Tk()
    Aplicacion(root)
    root.mainloop()


if __name__ == "__main__":
    main()
