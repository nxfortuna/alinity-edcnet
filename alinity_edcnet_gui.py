"""
Interfaz grafica del middleware Alinity ci -> EDCNet.

Flujo:
  1. Nombre de quien procesa (se envia como "Detection Operator").
  2. Seleccion de los controles a transmitir: todos marcados, filtrados por
     el dia en curso, con comentario opcional por fila.
  3. Generar archivo -> .csv en la carpeta Ready for Upload del agente.

Tras generar, pide al agente EDCNet que suba el archivo en el momento.
Menu Herramientas: controles leidos y configuracion.

Uso:  pythonw alinity_edcnet_gui.py [--config RUTA]
"""

import argparse
import json
import os
import queue
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import alinity_edcnet as core

TITULO = "Alinity ci → EDCNet"
ESTADO_UI = core.BASE_DIR / "ui_estado.json"   # recuerda el ultimo operador

P = dict(fondo="#F3F5F8", tarjeta="#FFFFFF", primario="#1F4E79", primario_hover="#2B6399",
         texto="#1E2833", suave="#6B7785", borde="#D8DEE6", ok="#2E7D32", error="#C62828",
         aviso="#B26A00", zebra="#F7F9FC", sel="#DCEBFA")
FUENTE = "Segoe UI"
MARCADO, DESMARCADO = "☑", "☐"


def aplicar_estilo(root: tk.Tk):
    st = ttk.Style(root)
    st.theme_use("clam")
    root.configure(background=P["fondo"])
    root.option_add("*Font", (FUENTE, 10))
    # lista desplegable de los Combobox: letra mas grande y seleccion con el color de la app
    root.option_add("*TCombobox*Listbox.font", (FUENTE, 12))
    root.option_add("*TCombobox*Listbox.selectBackground", P["primario"])
    root.option_add("*TCombobox*Listbox.selectForeground", "white")
    st.configure(".", background=P["fondo"], foreground=P["texto"], font=(FUENTE, 10))
    st.configure("TFrame", background=P["fondo"])
    st.configure("Tarjeta.TFrame", background=P["tarjeta"])
    st.configure("TLabel", background=P["fondo"])
    st.configure("Tarjeta.TLabel", background=P["tarjeta"])
    st.configure("Titulo.TLabel", background=P["tarjeta"], font=(FUENTE, 18, "bold"))
    st.configure("Seccion.TLabel", background=P["tarjeta"], font=(FUENTE, 13, "bold"))
    st.configure("Sub.TLabel", background=P["tarjeta"], foreground=P["suave"])
    st.configure("Suave.TLabel", foreground=P["suave"])
    st.configure("Aviso.TButton", foreground=P["aviso"])
    st.configure("Header.TFrame", background=P["primario"])
    st.configure("Header.TLabel", background=P["primario"], foreground="white", font=(FUENTE, 14, "bold"))
    st.configure("HeaderSub.TLabel", background=P["primario"], foreground="#C9DAEA")
    st.configure("Header.TButton", background=P["primario"], foreground="white", borderwidth=1,
                 bordercolor="#6F95BC", padding=(10, 3))
    st.map("Header.TButton", background=[("active", P["primario_hover"])])
    st.configure("Primario.TButton", background=P["primario"], foreground="white",
                 font=(FUENTE, 11, "bold"), padding=(20, 8), borderwidth=0)
    st.map("Primario.TButton", background=[("disabled", "#9FB3C8"), ("active", P["primario_hover"])])
    st.configure("TButton", padding=(10, 5))
    st.configure("TCombobox", padding=(8, 4), arrowsize=16)
    st.configure("TCheckbutton", background=P["tarjeta"])
    st.configure("Treeview", rowheight=30, background="white", fieldbackground="white",
                 bordercolor=P["borde"], font=(FUENTE, 10))
    st.configure("Treeview.Heading", font=(FUENTE, 10, "bold"), background="#E9EEF4",
                 relief="flat", padding=(6, 6))
    st.map("Treeview", background=[("selected", P["sel"])], foreground=[("selected", P["texto"])])


def _leer_estado_ui() -> dict:
    try:
        return json.loads(ESTADO_UI.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _guardar_estado_ui(datos: dict):
    try:
        ESTADO_UI.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _a_id(texto: str):
    """Texto de un campo ID -> null, entero o texto (como se guarda en config)."""
    texto = texto.strip()
    if not texto:
        return None
    return int(texto) if texto.isdigit() else texto


def _treeview(padre, columnas, alto=None) -> ttk.Treeview:
    marco = ttk.Frame(padre, style="Tarjeta.TFrame")
    marco.pack(fill="both", expand=True)
    tv = ttk.Treeview(marco, columns=[c[0] for c in columnas], show="headings",
                      **({"height": alto} if alto else {}))
    for clave, titulo, ancho, *resto in columnas:
        tv.heading(clave, text=titulo, anchor="w")
        tv.column(clave, width=ancho, anchor=resto[0] if resto else "w",
                  stretch=clave == columnas[-1][0])
    sy = ttk.Scrollbar(marco, orient="vertical", command=tv.yview)
    tv.configure(yscrollcommand=sy.set)
    tv.grid(row=0, column=0, sticky="nsew")
    sy.grid(row=0, column=1, sticky="ns")
    marco.rowconfigure(0, weight=1)
    marco.columnconfigure(0, weight=1)
    tv.tag_configure("par", background=P["zebra"])
    return tv


# ======================================================================
# Pantalla 1: quien procesa
# ======================================================================

class PantallaNombre(ttk.Frame):
    def __init__(self, app: "App"):
        super().__init__(app.contenedor)
        self.app = app
        tarjeta = ttk.Frame(self, style="Tarjeta.TFrame", padding=(40, 32))
        tarjeta.place(relx=0.5, rely=0.42, anchor="center")
        ttk.Label(tarjeta, text="¿Quién procesa los controles?", style="Titulo.TLabel").pack(anchor="w")
        ttk.Label(tarjeta, text="Este nombre se enviará a EDCNet como operador de los resultados.",
                  style="Sub.TLabel").pack(anchor="w", pady=(4, 18))
        self.var = tk.StringVar()
        # desplegable con los operadores anteriores; tambien se puede escribir uno nuevo
        self.entrada = ttk.Combobox(tarjeta, textvariable=self.var, width=34, font=(FUENTE, 13))
        self.entrada.pack(fill="x", ipady=4)
        self.entrada.bind("<Return>", lambda e: self._continuar())
        self.entrada.bind("<<ComboboxSelected>>", lambda e: self.error.configure(text=""))
        ttk.Label(tarjeta, text="Elija un nombre de la lista o escriba uno nuevo.",
                  style="Sub.TLabel").pack(anchor="w", pady=(4, 0))
        self.error = ttk.Label(tarjeta, text="", style="Tarjeta.TLabel", foreground=P["error"])
        self.error.pack(anchor="w", pady=(6, 0))
        ttk.Button(tarjeta, text="Continuar  →", style="Primario.TButton",
                   command=self._continuar).pack(anchor="e", pady=(12, 0))

    @staticmethod
    def _anteriores() -> list[str]:
        estado = _leer_estado_ui()
        nombres = estado.get("operadores") or ([estado["operador"]] if estado.get("operador") else [])
        # en mayusculas y sin repetidos (listas guardadas antes pueden traer "vivian" y "VIVIAN")
        return list(dict.fromkeys(" ".join(n.split()).upper() for n in nombres
                                  if isinstance(n, str) and n.strip()))

    def mostrar(self):
        self.error.configure(text="")
        anteriores = self._anteriores()
        self.entrada["values"] = anteriores
        if anteriores and not self.var.get():
            self.var.set(anteriores[0])   # el ultimo que proceso
        self.entrada.focus_set()
        self.entrada.select_range(0, "end")

    def _continuar(self):
        # siempre en mayusculas: EDCNet trata "vivian" y "VIVIAN" como operadores distintos
        nombre = " ".join(self.var.get().split()).upper()
        if not nombre:
            self.error.configure(text="Elija o escriba su nombre para continuar.")
            return
        otros = [n for n in self._anteriores() if n != nombre]   # el ultimo va primero
        _guardar_estado_ui({**_leer_estado_ui(), "operador": nombre,
                            "operadores": ([nombre] + otros)[:20]})
        self.app.iniciar_sesion(nombre)


# ======================================================================
# Pantalla 2: seleccion de controles a transmitir
# ======================================================================

class PantallaSeleccion(ttk.Frame):
    COLUMNAS = [
        ("sel", MARCADO, 44, "center"), ("hora", "Hora", 90), ("ensayo", "Prueba", 150),
        ("tipo", "Tipo", 60), ("lote", "Lote reactivo", 105), ("valores", "Resultados", 300),
        ("estado", "Estado", 215), ("comentario", "Comentario (doble clic para editar)", 260),
    ]

    def __init__(self, app: "App"):
        super().__init__(app.contenedor, padding=12)
        self.app = app
        self.marcadas: dict[str, bool] = {}
        self.comentarios: dict[str, str] = {}
        self.id_nuevo: set[str] = set()   # reenviar con RecordID nuevo (borrado en EDCNet)
        self.por_id: dict[str, core.Fila] = {}
        self.avisos: list[str] = []
        self._editor = None

        # --- barra de filtros
        barra = ttk.Frame(self, style="Tarjeta.TFrame", padding=(14, 10))
        barra.pack(fill="x")
        ttk.Label(barra, text="Controles para transmitir", style="Seccion.TLabel").pack(side="left")
        self.btn_avisos = ttk.Button(barra, text="", style="Aviso.TButton", command=self._ver_avisos)
        self.lotes_nuevos: list[dict] = []
        self.btn_lotes = ttk.Button(barra, text="", style="Aviso.TButton",
                                    command=lambda: DialogoLotes(self.app, self.lotes_nuevos))
        self.var_todas_fechas = tk.BooleanVar(value=False)
        ttk.Checkbutton(barra, text="Todas las fechas", variable=self.var_todas_fechas,
                        command=self.refrescar).pack(side="right", padx=(10, 0))
        ttk.Button(barra, text="Hoy", width=5, command=lambda: self._ir_a(date.today())).pack(side="right")
        ttk.Button(barra, text="▶", width=3, command=lambda: self._mover_dia(1)).pack(side="right", padx=2)
        self.var_fecha = tk.StringVar(value=date.today().isoformat())
        ent = ttk.Entry(barra, textvariable=self.var_fecha, width=11, justify="center")
        ent.pack(side="right")
        ent.bind("<Return>", lambda e: self.refrescar())
        ttk.Button(barra, text="◀", width=3, command=lambda: self._mover_dia(-1)).pack(side="right", padx=2)
        ttk.Label(barra, text="Fecha:", style="Tarjeta.TLabel").pack(side="right", padx=(16, 4))
        self.var_ensayo = tk.StringVar(value="Todas las pruebas")
        self.cb_ensayo = ttk.Combobox(barra, textvariable=self.var_ensayo, state="readonly", width=22)
        self.cb_ensayo.pack(side="right")
        self.cb_ensayo.bind("<<ComboboxSelected>>", lambda e: self.refrescar())

        # --- barra de reenvio: ya enviados e ID nuevo
        reenvio = ttk.Frame(self, style="Tarjeta.TFrame", padding=(14, 0, 14, 10))
        reenvio.pack(fill="x")
        ttk.Checkbutton(reenvio, text="Mostrar ya enviados", variable=app.reprocesar,
                        command=app.recargar).pack(side="left")
        ttk.Button(reenvio, text="↻  Reenviar con ID nuevo",
                   command=self._boton_id_nuevo).pack(side="left", padx=(14, 8))
        ttk.Label(reenvio, text="Solo para resultados que se borraron en EDCNet: active “Mostrar ya "
                                "enviados”, seleccione las filas y pulse ↻.",
                  style="Sub.TLabel").pack(side="left")
        ttk.Button(reenvio, text="⟳  Actualizar", command=app.recargar).pack(side="right")
        self.var_actualizado = tk.StringVar()
        ttk.Label(reenvio, textvariable=self.var_actualizado, style="Sub.TLabel").pack(side="right", padx=8)

        # --- aviso de vencimiento (solo aparece cuando hay algo por vencer)
        self.var_banner = tk.StringVar()
        self.banner = tk.Label(self, textvariable=self.var_banner, background="#FFF3CD",
                               foreground="#7A4B00", font=(FUENTE, 10, "bold"), anchor="w",
                               padx=14, pady=8, wraplength=1200, justify="left")

        # --- tabla
        cuerpo = ttk.Frame(self, style="Tarjeta.TFrame", padding=1)
        cuerpo.pack(fill="both", expand=True, pady=10)
        self.cuerpo = cuerpo
        self.tv = _treeview(cuerpo, self.COLUMNAS)
        self.tv.tag_configure("fuera", foreground=P["error"])
        self.tv.tag_configure("alerta", foreground=P["aviso"])
        self.tv.tag_configure("nomarcada", foreground="#9AA4AF")
        self.tv.tag_configure("enviada", font=(FUENTE, 10, "italic"))
        self.tv.bind("<Button-1>", self._clic)
        self.tv.bind("<Double-1>", self._doble_clic)
        self.tv.bind("<space>", self._espacio)
        self.tv.bind("<Button-3>", self._menu_fila)
        self.menu_fila = tk.Menu(self, tearoff=False)
        self.menu_fila.add_command(label="Enviar con ID nuevo (el resultado se borró en EDCNet)",
                                   command=lambda: self._alternar_id_nuevo(True))
        self.menu_fila.add_command(label="Enviar con su ID original",
                                   command=lambda: self._alternar_id_nuevo(False))
        self.vacio = ttk.Label(cuerpo, text="", background="white", foreground=P["suave"],
                               font=(FUENTE, 11))

        # --- barra inferior
        pie = ttk.Frame(self, style="Tarjeta.TFrame", padding=(14, 10))
        pie.pack(fill="x")
        ttk.Button(pie, text=f"{MARCADO}  Marcar todas", command=lambda: self._marcar_visibles(True)).pack(side="left")
        ttk.Button(pie, text=f"{DESMARCADO}  Ninguna", command=lambda: self._marcar_visibles(False)).pack(side="left", padx=(6, 18))
        ttk.Label(pie, text="Comentario para las marcadas:", style="Tarjeta.TLabel").pack(side="left")
        self.var_com = tk.StringVar()
        e = ttk.Entry(pie, textvariable=self.var_com, width=30)
        e.pack(side="left", padx=6)
        e.bind("<Return>", lambda ev: self._comentar_marcadas())
        ttk.Button(pie, text="Aplicar", command=self._comentar_marcadas).pack(side="left")
        self.btn_generar = ttk.Button(pie, text="Generar archivo  →", style="Primario.TButton",
                                      command=self._generar)
        self.btn_generar.pack(side="right")
        self.var_resumen = tk.StringVar()
        ttk.Label(pie, textvariable=self.var_resumen, style="Sub.TLabel").pack(side="right", padx=14)

    # ---------------- datos

    def cargar(self, filas: list[core.Fila], avisos: list[str], lotes_nuevos=(), vencen=()):
        self._mostrar_vencimientos(vencen)
        self.lotes_nuevos = list(lotes_nuevos)
        if self.lotes_nuevos:
            self.btn_lotes.configure(text=f"⚠  Asignar {len(self.lotes_nuevos)} lote(s) EQC nuevo(s)")
            self.btn_lotes.pack(side="left", padx=(14, 0))
        else:
            self.btn_lotes.pack_forget()
        self.por_id = {f.datos["RecordID"]: f for f in filas}
        # lo pendiente va marcado; lo ya enviado (visible con "Mostrar ya enviados") no
        self.marcadas = {rid: self.marcadas.get(rid, not self._enviada(f))
                         for rid, f in self.por_id.items()}
        # filas nuevas: comentario automatico del parser (ej. controles de la tarde);
        # las ya vistas conservan lo que el usuario haya escrito
        self.comentarios = {rid: self.comentarios.get(rid, f.datos.get("Comment", ""))
                            for rid, f in self.por_id.items()}
        self.id_nuevo &= set(self.por_id)
        self.avisos = avisos
        self.var_actualizado.set(f"Leído a las {datetime.now():%H:%M}")
        nombres = sorted({self._nombre_ensayo(f.ensayo) for f in filas})
        self.cb_ensayo["values"] = ["Todas las pruebas"] + nombres
        if self.var_ensayo.get() not in self.cb_ensayo["values"]:
            self.var_ensayo.set("Todas las pruebas")
        if avisos:
            self.btn_avisos.configure(text=f"⚠  {len(avisos)} aviso(s)")
            self.btn_avisos.pack(side="left", padx=14)
        else:
            self.btn_avisos.pack_forget()
        self.refrescar()

    def _mostrar_vencimientos(self, vencen):
        if not vencen:
            self.banner.pack_forget()
            return
        partes = []
        for v in vencen:
            cuando = ("VENCIÓ" if v["faltan"] < 0 else "vence HOY" if v["faltan"] == 0
                      else "vence mañana" if v["faltan"] == 1 else f"vence en {v['faltan']} días")
            partes.append(f"{v['control']} (lote {v['lote']}) {cuando} — {v['vence']:%d/%m/%Y}")
        self.var_banner.set("⚠  Control por vencer:   " + "     ·     ".join(partes))
        self.banner.pack(fill="x", before=self.cuerpo)

    def _enviada(self, f: core.Fila) -> bool:
        return any(g in self.app.procesados for g in f.guids)

    def _nombre_ensayo(self, codigo: str) -> str:
        return self.app.cfg.get("ensayos", {}).get(codigo, {}).get("nombre") or codigo

    def _fecha_filtro(self) -> str | None:
        if self.var_todas_fechas.get():
            return None
        try:
            return datetime.strptime(self.var_fecha.get().strip(), "%Y-%m-%d").date().isoformat()
        except ValueError:
            self.var_fecha.set(date.today().isoformat())
            return date.today().isoformat()

    def _visibles(self) -> list[core.Fila]:
        dia = self._fecha_filtro()
        ens = self.var_ensayo.get()
        return [f for f in self.por_id.values()
                if (dia is None or f.datos["Date"] == dia)
                and (ens == "Todas las pruebas" or self._nombre_ensayo(f.ensayo) == ens)]

    def _mover_dia(self, delta: int):
        try:
            actual = datetime.strptime(self.var_fecha.get().strip(), "%Y-%m-%d").date()
        except ValueError:
            actual = date.today()
        self._ir_a(actual + timedelta(days=delta))

    def _ir_a(self, dia: date):
        self.var_fecha.set(dia.isoformat())
        self.var_todas_fechas.set(False)
        self.refrescar()

    def _texto_valores(self, f: core.Fila) -> str:
        d = f.datos
        if d["ResultType"] == "1":
            nombre = d.get("EQCNameOrEQCLotNumberID", "")
            meta = core.metadata_ensayo(self.app.cfg, self.app.cfg["ensayos"][f.ensayo])
            for l in meta.get("EQCLotNumbersMetadata", []):
                if str(l["ID"]) == nombre:
                    nombre = l["Name"]
            return f"{nombre} = {d.get('Value', '')}"
        return "   ·   ".join(f"{k[:-6]} {v}" for k, v in d.items() if k.endswith(" Value") and v)

    # ---------------- tabla

    def refrescar(self):
        self._cerrar_editor()
        tv = self.tv
        tv.delete(*tv.get_children())
        # mismo orden en que se suben: kit, EQC, EQC de la tarde
        visibles = sorted(self._visibles(), key=lambda f: core.orden_subida(self.app.cfg, f))
        todas = self.var_todas_fechas.get()
        for i, f in enumerate(visibles):
            rid = f.datos["RecordID"]
            marcada = self.marcadas.get(rid, True)
            tags = ["par"] if i % 2 else []
            if f.datos["Valid"] != "true":
                tags.append("fuera")
            elif f.alertas:
                tags.append("alerta")
            if not marcada:
                tags.append("nomarcada")
            if self._enviada(f):
                tags.append("enviada")
            hora = "" if f.fecha is None else f.fecha.strftime("%d/%m %H:%M" if todas else "%H:%M")
            tv.insert("", "end", iid=rid, tags=tags, values=(
                MARCADO if marcada else DESMARCADO, hora, self._nombre_ensayo(f.ensayo),
                "EQC" if f.datos["ResultType"] == "1" else "Kit",
                f.datos.get("Detection KitLotNumber", ""), self._texto_valores(f),
                self._texto_estado(f), self.comentarios.get(rid, "")))
        if visibles:
            self.vacio.place_forget()
        else:
            dia = "" if todas else f" del {self.var_fecha.get()}"
            self.vacio.configure(text=f"No hay controles pendientes{dia}.")
            self.vacio.place(relx=0.5, rely=0.45, anchor="center")
        self._actualizar_resumen(visibles)

    def _actualizar_resumen(self, visibles=None):
        visibles = self._visibles() if visibles is None else visibles
        n = sum(self.marcadas.get(f.datos["RecordID"], True) for f in visibles)
        texto = f"{n} de {len(visibles)} marcadas"
        otros = len(self.por_id) - len(visibles)
        if otros and not self.var_todas_fechas.get():
            texto += f"   ·   {otros} pendiente(s) en otras fechas/pruebas"
        self.var_resumen.set(texto)
        self.btn_generar.state(["!disabled"] if n else ["disabled"])

    def _texto_estado(self, f: core.Fila) -> str:
        texto = "✔  En rango" if f.datos["Valid"] == "true" else "✖  Fuera de rango"
        if f.alertas:
            texto += "  ·  ⚠ límite EDCNet"
        if self._enviada(f):
            texto += "  ·  ya enviado"
        if f.datos["RecordID"] in self.id_nuevo:
            texto += "  ·  ↻ ID nuevo"
        return texto

    def _menu_fila(self, e):
        fila = self.tv.identify_row(e.y)
        if not fila:
            return
        if fila not in self.tv.selection():
            self.tv.selection_set(fila)
        self.menu_fila.tk_popup(e.x_root, e.y_root)

    def _boton_id_nuevo(self):
        sel = self.tv.selection()
        if not sel:
            messagebox.showinfo("Reenviar con ID nuevo", (
                "¿Cuándo usarlo?\n"
                "Cuando un resultado ya se subió, se borró en EDCNet y hay que volver a "
                "subirlo. EDCNet recuerda el ID de cada resultado aunque se borre, y sin ID "
                "nuevo lo rechaza como “Duplicate”.\n\n"
                "Cómo:\n"
                "1. Active “Mostrar ya enviados”.\n"
                "2. Seleccione las filas (clic; Ctrl+clic para varias).\n"
                "3. Pulse ↻ Reenviar con ID nuevo y luego Generar archivo.\n\n"
                "No lo use si el resultado sigue en EDCNet: quedaría duplicado."), parent=self)
            return
        if all(rid in self.id_nuevo for rid in sel):
            self._alternar_id_nuevo(False)   # el boton tambien sirve para deshacer
            return
        if messagebox.askyesno("Reenviar con ID nuevo", (
                f"{len(sel)} fila(s) se enviarán con un ID nuevo.\n\n"
                "Use esto solo si esos resultados se BORRARON en EDCNet. Si todavía "
                "existen, quedarán duplicados.\n\n¿Continuar?"), parent=self):
            self._alternar_id_nuevo(True)

    def _alternar_id_nuevo(self, valor: bool):
        for rid in self.tv.selection():
            if valor:
                self.id_nuevo.add(rid)
                self.marcadas[rid] = True     # si se reenvia, va marcada
            else:
                self.id_nuevo.discard(rid)
            self.tv.set(rid, "estado", self._texto_estado(self.por_id[rid]))
            self._pintar_fila(rid)
        self._actualizar_resumen()

    def _alternar(self, rids):
        for rid in rids:
            self.marcadas[rid] = not self.marcadas.get(rid, True)
            self._pintar_fila(rid)
        self._actualizar_resumen()

    def _pintar_fila(self, rid: str):
        marcada = self.marcadas[rid]
        self.tv.set(rid, "sel", MARCADO if marcada else DESMARCADO)
        tags = [t for t in self.tv.item(rid, "tags") if t != "nomarcada"]
        self.tv.item(rid, tags=tags + ([] if marcada else ["nomarcada"]))

    def _marcar_visibles(self, valor: bool):
        for f in self._visibles():
            self.marcadas[f.datos["RecordID"]] = valor
        self.refrescar()

    def _clic(self, e):
        region = self.tv.identify_region(e.x, e.y)
        col = self.tv.identify_column(e.x)
        if region == "heading" and col == "#1":
            visibles = self._visibles()
            self._marcar_visibles(not all(self.marcadas.get(f.datos["RecordID"], True) for f in visibles))
            return "break"
        if region == "cell" and col == "#1":
            fila = self.tv.identify_row(e.y)
            if fila:
                self._alternar([fila])
            return "break"

    def _espacio(self, _e):
        self._alternar(self.tv.selection())
        return "break"

    def _doble_clic(self, e):
        fila = self.tv.identify_row(e.y)
        if not fila or self.tv.identify_region(e.x, e.y) != "cell":
            return
        if self.tv.identify_column(e.x) == f"#{len(self.COLUMNAS)}":
            self._editar_comentario(fila)
        elif self.tv.identify_column(e.x) != "#1":
            self._detalle(fila)

    def _editar_comentario(self, rid: str):
        self._cerrar_editor()
        col = f"#{len(self.COLUMNAS)}"
        caja = self.tv.bbox(rid, col)
        if not caja:
            return
        x, y, w, h = caja
        var = tk.StringVar(value=self.comentarios.get(rid, ""))
        ent = ttk.Entry(self.tv, textvariable=var)
        ent.place(x=x, y=y, width=w, height=h)
        ent.focus_set()
        ent.icursor("end")

        def guardar(_e=None):
            self.comentarios[rid] = " ".join(var.get().split())
            if self.tv.exists(rid):
                self.tv.set(rid, "comentario", self.comentarios[rid])
            self._cerrar_editor()
        ent.bind("<Return>", guardar)
        ent.bind("<FocusOut>", guardar)
        ent.bind("<Escape>", lambda _e: self._cerrar_editor())
        self._editor = ent

    def _cerrar_editor(self):
        if self._editor is not None:
            ed, self._editor = self._editor, None
            ed.destroy()

    def _comentar_marcadas(self):
        texto = " ".join(self.var_com.get().split())
        for f in self._visibles():
            rid = f.datos["RecordID"]
            if self.marcadas.get(rid, True):
                self.comentarios[rid] = texto
        self.var_com.set("")
        self.refrescar()

    def _detalle(self, rid: str):
        f = self.por_id[rid]
        columnas = core.encabezado_ensayo(self.app.cfg, self.app.cfg["ensayos"][f.ensayo])
        datos = self._datos_envio(f)
        texto = "\n".join(f"{c}:  {datos.get(c, '')}" for c in columnas)
        if f.alertas:
            texto += "\n\n⚠  EDCNet lo marcará como advertencia:\n" + "\n".join(
                f"   • {a}" for a in f.alertas)
        messagebox.showinfo(self._nombre_ensayo(f.ensayo), texto, parent=self)

    def _ver_avisos(self):
        win = tk.Toplevel(self)
        win.title("Avisos")
        win.geometry("760x320")
        win.transient(self)
        txt = tk.Text(win, wrap="word", font=(FUENTE, 10), padx=12, pady=10, relief="flat")
        txt.insert("1.0", "\n\n".join(f"⚠  {a}" for a in self.avisos))
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True)

    # ---------------- generar

    def _datos_envio(self, f: core.Fila) -> dict:
        d = dict(f.datos)
        d["Detection Operator"] = self.app.operador
        # sin comas ni saltos: EDCNet separa campos por coma
        d["Comment"] = self.comentarios.get(d["RecordID"], "").replace(",", ";")
        if d["RecordID"] in self.id_nuevo:
            # EDCNet recuerda los ClientResultID aunque se borre el resultado
            d["RecordID"] += f"-R{datetime.now():%m%d%H%M}"
        return d

    def _generar(self):
        elegidas = [f for f in self._visibles() if self.marcadas.get(f.datos["RecordID"], True)]
        if not elegidas:
            return
        kit = sum(f.datos["ResultType"] == "2" for f in elegidas)
        fuera = sum(f.datos["Valid"] != "true" for f in elegidas)
        msg = (f"Se generará un archivo con {len(elegidas)} resultado(s):\n"
               f"      {kit} de controles de kit y {len(elegidas) - kit} EQC\n\n"
               f"Operador:  {self.app.operador}\n"
               f"Destino:  {self.app.cfg['carpeta_salida']}")
        if fuera:
            msg += f"\n\n⚠  {fuera} resultado(s) fuera de rango se enviarán como Valid = false."
        con_alerta = [f for f in elegidas if f.alertas]
        if con_alerta:
            msg += (f"\n\n⚠  {len(con_alerta)} resultado(s) fuera de los límites de EDCNet "
                    f"(se guardarán como advertencia):")
            for f in con_alerta[:5]:
                msg += f"\n   • {self._nombre_ensayo(f.ensayo)}: {f.alertas[0]}"
            if len(con_alerta) > 5:
                msg += f"\n   • ... y {len(con_alerta) - 5} más"
        if not messagebox.askyesno("Generar archivo", msg + "\n\n¿Generar el archivo?", parent=self):
            return
        filas = [core.Fila(f.ensayo, f.guids, self._datos_envio(f), f.fecha) for f in elegidas]
        cfg, procesados = self.app.cfg, self.app.procesados

        def trabajo():
            destino = core.confirmar(cfg, filas, procesados, False)
            if not cfg.get("subir_inmediato", True):
                return destino, {"estado": "desactivado", "detalle": "", "resultado": None,
                                 "devueltas": [], "lineas": []}
            return destino, core.subir_y_verificar(cfg, destino)

        def listo(valor):
            destino, envio = valor
            for f in elegidas:
                self.comentarios.pop(f.datos["RecordID"], None)
                self.id_nuevo.discard(f.datos["RecordID"])
                self.marcadas.pop(f.datos["RecordID"], None)   # si reaparece, como enviada
            self.app.reprocesar.set(False)   # volver a mostrar solo lo pendiente
            DialogoResultado(self, destino.name, envio)
            self.app.recargar()
        self.app.en_hilo(trabajo, listo, "Generando archivo y subiendo a EDCNet (puede tardar hasta 1 minuto)...")


class DialogoLotes(tk.Toplevel):
    """Asigna los lotes nuevos de controles EQC a un material registrado en EDCNet."""

    ELEGIR = "(elegir material...)"
    NO_ENVIAR = "\u2014 No enviar este lote \u2014"

    def __init__(self, app: "App", lotes: list[dict]):
        super().__init__(app)
        self.app = app
        self.lotes = lotes
        self.title("Asignar lotes EQC")
        self.transient(app)
        self.configure(background=P["tarjeta"])
        frm = ttk.Frame(self, style="Tarjeta.TFrame", padding=18)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Lotes EQC nuevos", style="Seccion.TLabel").pack(anchor="w")
        ttk.Label(frm, text="Estos lotes llegaron del equipo y no est\u00e1n asignados a un material de EDCNet. "
                            "Elija el material correcto (solo se listan los registrados en todas las pruebas "
                            "donde aparece el lote). Sus resultados quedar\u00e1n listos para enviar.",
                  style="Sub.TLabel", wraplength=680).pack(anchor="w", pady=(4, 12))
        self.combos = []
        for lote in lotes:
            caja = ttk.Frame(frm, style="Tarjeta.TFrame")
            caja.pack(fill="x", pady=6)
            nombres = ", ".join(app.pant_sel._nombre_ensayo(c) for c in lote["ensayos"])
            ttk.Label(caja, text=f"{lote['control']}  \u00b7  lote {lote['lote']}",
                      style="Tarjeta.TLabel", font=(FUENTE, 11, "bold")).pack(anchor="w")
            ttk.Label(caja, text=f"{nombres}  \u00b7  {lote['resultados']} resultado(s) pendiente(s)",
                      style="Sub.TLabel").pack(anchor="w")
            valores = [self.ELEGIR] + [n for _, n in lote["opciones"]] + [self.NO_ENVIAR]
            var = tk.StringVar(value=next((n for i, n in lote["opciones"] if i == lote["sugerido"]),
                                          self.ELEGIR))
            cb = ttk.Combobox(caja, textvariable=var, values=valores, state="readonly", width=58)
            cb.pack(anchor="w", pady=(4, 0))
            if lote["sugerido"]:
                ttk.Label(caja, text="Sugerido por coincidencia del n\u00famero de lote: verif\u00edquelo.",
                          style="Sub.TLabel", foreground=P["aviso"]).pack(anchor="w")
            self.combos.append(var)
        botones = ttk.Frame(frm, style="Tarjeta.TFrame")
        botones.pack(fill="x", pady=(14, 0))
        ttk.Button(botones, text="Guardar", style="Primario.TButton", command=self._guardar).pack(side="right")
        ttk.Button(botones, text="Cancelar", command=self.destroy).pack(side="right", padx=8)
        self.grab_set()

    def _guardar(self):
        asignaciones = []
        for lote, var in zip(self.lotes, self.combos):
            elegido = var.get()
            if elegido == self.ELEGIR:
                continue
            valor = None if elegido == self.NO_ENVIAR else \
                next(i for i, n in lote["opciones"] if n == elegido)
            asignaciones.append((lote["control"], lote["lote"], valor, lote["destino"]))
        if not asignaciones:
            self.destroy()
            return
        try:
            core.asignar_lotes_eqc(self.app.ruta_config, asignaciones)
        except (OSError, ValueError, KeyError) as e:
            messagebox.showerror(TITULO, f"No se pudo guardar la configuraci\u00f3n:\n{e}", parent=self)
            return
        self.destroy()
        self.app.recargar()


class DialogoResultado(tk.Toplevel):
    """Resultado de EDCNet para uno o varios archivos enviados."""

    TITULOS = {
        "error": ("⚠  No se pudo pedir la subida inmediata",
                  "El archivo quedó en Ready for Upload y se subirá en el próximo ciclo del agente. "
                  "Revise que el servicio EDCNetAgent esté iniciado."),
        "pendiente": ("El agente todavía no tomó el archivo",
                      "Se subirá en su próximo ciclo; el resultado se consultará al actualizar."),
        "desactivado": ("Archivo generado", "El agente de EDCNet lo subirá en su próximo ciclo."),
        "sin_resultado": ("Archivo subido; EDCNet aún lo procesa",
                          "El resultado se consultará automáticamente al actualizar."),
    }

    def __init__(self, padre, archivo: str, envio: dict, titulo_ventana="Resultado del envío"):
        super().__init__(padre)
        self.title(titulo_ventana)
        self.transient(padre)
        self.configure(background=P["tarjeta"])
        self.geometry("720x460")
        frm = ttk.Frame(self, style="Tarjeta.TFrame", padding=18)
        frm.pack(fill="both", expand=True)

        r = envio.get("resultado")
        if r is None:
            clave = envio["estado"] if envio["estado"] in self.TITULOS else "sin_resultado"
            titulo, sub = self.TITULOS[clave]
            if envio.get("detalle"):
                sub += f"\n\nDetalle: {envio['detalle']}"
        elif r["fallas"]:
            titulo = f"⚠  EDCNet rechazó {r['fallas']} resultado(s)"
            sub = (f"{len(envio['devueltas'])} volvieron a pendientes: corrija la causa y vuelva a enviarlos."
                   if envio["devueltas"] else "Revise el detalle abajo.")
        else:
            titulo = "✔  EDCNet recibió el archivo"
            sub = "Sin fallas." if not r["duplicados"] else \
                "Los duplicados ya estaban en EDCNet y no se volvieron a guardar."
        ttk.Label(frm, text=titulo, style="Seccion.TLabel").pack(anchor="w")
        ttk.Label(frm, text=sub, style="Sub.TLabel", wraplength=660).pack(anchor="w", pady=(4, 10))

        if r is not None:
            fila = ttk.Frame(frm, style="Tarjeta.TFrame")
            fila.pack(fill="x", pady=(0, 10))
            for nombre, valor, color in (("Guardados", r["guardados"], P["ok"]),
                                         ("Duplicados", r["duplicados"], P["primario"]),
                                         ("Advertencias", r["advertencias"], P["aviso"]),
                                         ("Fallas", r["fallas"], P["error"])):
                caja = tk.Frame(fila, background=P["zebra"], highlightthickness=1,
                                highlightbackground=P["borde"], padx=16, pady=8)
                caja.pack(side="left", padx=(0, 10))
                tk.Label(caja, text=str(valor), font=(FUENTE, 18, "bold"), background=P["zebra"],
                         foreground=color if valor else P["suave"]).pack()
                tk.Label(caja, text=nombre, background=P["zebra"], foreground=P["suave"]).pack()
            if envio.get("lineas"):
                txt = tk.Text(frm, wrap="word", height=8, font=(FUENTE, 10), relief="flat",
                              background=P["zebra"], padx=10, pady=8)
                txt.insert("1.0", "\n\n".join(envio["lineas"]))
                txt.configure(state="disabled")
                txt.pack(fill="both", expand=True)

        ttk.Label(frm, text=archivo, style="Sub.TLabel").pack(anchor="w", pady=(10, 0))
        ttk.Button(frm, text="Cerrar", style="Primario.TButton", command=self.destroy).pack(anchor="e", pady=(8, 0))
        self.grab_set()


# ======================================================================
# Ventanas de Herramientas
# ======================================================================

class VentanaControles(tk.Toplevel):
    COLS = [
        ("fecha", "Fecha", 130), ("ensayo", "Ensayo", 55), ("nombre", "Nombre", 110),
        ("control", "Control", 160), ("tipo", "Tipo", 75), ("lote_control", "Lote control", 110),
        ("lote_reactivo", "Lote reactivo", 95), ("valor", "Valor", 70), ("rango", "Rango", 110),
        ("operador", "Operador", 90), ("estado", "Estado", 90),
    ]

    def __init__(self, app: "App"):
        super().__init__(app)
        self.title("Controles leídos de los logs")
        self.geometry("1150x560")
        self.configure(background=P["fondo"])
        marco = ttk.Frame(self, padding=10)
        marco.pack(fill="both", expand=True)
        ttk.Label(marco, text="Todos los resultados QC encontrados en los logs, incluidos los "
                              "excluidos y ya procesados.", style="Suave.TLabel").pack(anchor="w", pady=(0, 6))
        tv = _treeview(marco, self.COLS)
        tv.tag_configure("fuera", foreground=P["error"])
        tv.tag_configure("gris", foreground="#9AA4AF")
        cfg = app.cfg
        for i, r in enumerate(reversed(app.resultados)):
            ens = cfg.get("ensayos", {}).get(r.ensayo, {})
            eqc = {**cfg.get("controles_eqc", {}), **ens.get("controles_eqc", {})}
            if r.control in ens.get("controles_kit", {}):
                tipo = "Kit"
            elif r.control in eqc:
                tipo = "Excluido" if eqc[r.control] is None else "EQC"
            else:
                tipo = "Sin mapear"
            tags = ["par"] if i % 2 else []
            if not r.en_rango():
                tags.append("fuera")
            elif tipo in ("Excluido", "Sin mapear"):
                tags.append("gris")
            tv.insert("", "end", tags=tags, values=(
                f"{r.fecha:%Y-%m-%d %H:%M:%S}", r.ensayo, r.nombre_ensayo, r.control, tipo,
                r.lote_control, r.lote_reactivo, r.valor, r.rango, r.operador,
                "Procesado" if r.guid in app.procesados else "Pendiente"))


class DialogoEnsayo(tk.Toplevel):
    """Edita los datos EDCNet de un ensayo (assay_id, analyte_id, controles)."""

    def __init__(self, padre, codigo: str, ens: dict):
        super().__init__(padre)
        self.title(f"Ensayo {codigo}")
        self.transient(padre)
        self.resultado = None
        self.ens = ens

        frm = ttk.Frame(self, padding=14)
        frm.pack(fill="both", expand=True)
        self.vars = {}
        for fila, (clave, etiqueta) in enumerate([
                ("nombre", "Nombre"), ("assay_id", "AssayID (EDCNet)"),
                ("analyte_id", "AnalyteID"), ("instrumento", "Instrumento (opcional)")]):
            ttk.Label(frm, text=etiqueta).grid(row=fila, column=0, sticky="w", pady=2)
            v = tk.StringVar(value="" if ens.get(clave) is None else str(ens.get(clave)))
            ttk.Entry(frm, textvariable=v, width=40).grid(row=fila, column=1, sticky="ew", pady=2)
            self.vars[clave] = v

        ttk.Label(frm, text='Controles de kit  { "CONTROL_ALINITY": "Nivel EDCNet" }').grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(8, 2))
        self.txt_kit = tk.Text(frm, width=60, height=7, font=("Consolas", 9))
        self.txt_kit.insert("1.0", json.dumps(ens.get("controles_kit", {}), indent=2, ensure_ascii=False))
        self.txt_kit.grid(row=5, column=0, columnspan=2, sticky="nsew")

        ttk.Label(frm, text='Controles EQC propios del ensayo  { "CONTROL": "ID" }').grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(8, 2))
        self.txt_eqc = tk.Text(frm, width=60, height=4, font=("Consolas", 9))
        self.txt_eqc.insert("1.0", json.dumps(ens.get("controles_eqc", {}), indent=2, ensure_ascii=False))
        self.txt_eqc.grid(row=7, column=0, columnspan=2, sticky="nsew")

        botones = ttk.Frame(frm)
        botones.grid(row=8, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(botones, text="Aceptar", command=self._aceptar).pack(side="left", padx=4)
        ttk.Button(botones, text="Cancelar", command=self.destroy).pack(side="left")
        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(5, weight=1)

        self.grab_set()
        self.wait_window()

    def _aceptar(self):
        try:
            kit = json.loads(self.txt_kit.get("1.0", "end").strip() or "{}")
            eqc = json.loads(self.txt_eqc.get("1.0", "end").strip() or "{}")
            if not isinstance(kit, dict) or not isinstance(eqc, dict):
                raise ValueError("deben ser objetos JSON { ... }")
        except ValueError as e:
            messagebox.showerror("JSON inválido", str(e), parent=self)
            return
        nuevo = dict(self.ens)
        nuevo["nombre"] = self.vars["nombre"].get().strip()
        nuevo["assay_id"] = _a_id(self.vars["assay_id"].get())
        nuevo["analyte_id"] = self.vars["analyte_id"].get().strip()
        nuevo["instrumento"] = self.vars["instrumento"].get().strip()
        nuevo["controles_kit"] = kit
        if eqc:
            nuevo["controles_eqc"] = eqc
        else:
            nuevo.pop("controles_eqc", None)
        self.resultado = nuevo
        self.destroy()


class VentanaConfig(tk.Toplevel):
    CAMPOS = [
        ("carpeta_logs", "Carpeta de logs", "dir"),
        ("patron_logs", "Patrón de logs", None),
        ("dias_logs", "Días de logs a leer", "int"),
        ("carpeta_salida", "Carpeta Ready for Upload", "dir"),
        ("prefijo_archivo", "Prefijo del archivo", None),
        ("archivo_metadata", "Metadata EDCNet (.json)", "file"),
        ("operador", "Operador por defecto", None),
        ("aviso_vencimiento_dias", "Avisar vencimiento (días antes)", "int"),
        ("canal_instrumento", "Canal del instrumento", None),
        ("minutos_max_corrida", "Minutos máx. por corrida", "int"),
    ]
    COLS_ENSAYOS = [
        ("codigo", "Código", 60), ("nombre", "Nombre", 140), ("assay_id", "AssayID", 80),
        ("analyte_id", "AnalyteID", 80), ("kit", "Controles de kit", 520),
    ]

    def __init__(self, app: "App"):
        super().__init__(app)
        self.app = app
        self.title("Configuración")
        self.geometry("1100x640")
        self.configure(background=P["fondo"])
        self.cfg_raw = json.loads(app.ruta_config.read_text(encoding="utf-8"))

        tab = ttk.Frame(self, padding=10)
        tab.pack(fill="both", expand=True)
        gen = ttk.LabelFrame(tab, text="General", padding=8)
        gen.pack(fill="x")
        self.vars: dict[str, tk.StringVar] = {}
        for i, (clave, etiqueta, tipo) in enumerate(self.CAMPOS):
            fila, col = divmod(i, 2)
            ttk.Label(gen, text=etiqueta).grid(row=fila, column=col * 3, sticky="w",
                                               padx=(0 if col == 0 else 14, 4), pady=2)
            valor = self.cfg_raw.get(clave, "")
            v = tk.StringVar(value="" if valor is None else str(valor))
            ttk.Entry(gen, textvariable=v, width=44).grid(row=fila, column=col * 3 + 1, sticky="ew", pady=2)
            if tipo in ("dir", "file"):
                ttk.Button(gen, text="...", width=3,
                           command=lambda v=v, t=tipo: self._examinar(v, t)).grid(row=fila, column=col * 3 + 2)
            self.vars[clave] = v
        gen.columnconfigure(1, weight=1)
        gen.columnconfigure(4, weight=1)

        eqc = ttk.LabelFrame(tab, text='Controles EQC  { "CONTROL_ALINITY": {"por_lote": {"lote": "ID EDCNet"}} '
                                       '· null = no se envía }', padding=8)
        eqc.pack(fill="x", pady=8)
        self.txt_eqc = tk.Text(eqc, height=6, font=("Consolas", 9))
        self.txt_eqc.insert("1.0", json.dumps(self.cfg_raw.get("controles_eqc", {}), indent=2, ensure_ascii=False))
        self.txt_eqc.pack(fill="x")

        ens = ttk.LabelFrame(tab, text="Pruebas (doble clic para editar)", padding=8)
        ens.pack(fill="both", expand=True)
        botones = ttk.Frame(ens)
        botones.pack(fill="x", pady=(0, 6))
        ttk.Button(botones, text="Agregar", command=self._agregar).pack(side="left")
        ttk.Button(botones, text="Editar", command=self._editar).pack(side="left", padx=4)
        ttk.Button(botones, text="Eliminar", command=self._eliminar).pack(side="left")
        ttk.Button(botones, text="Guardar", style="Primario.TButton", command=self._guardar).pack(side="right")
        ttk.Button(botones, text="Cancelar", command=self.destroy).pack(side="right", padx=6)
        self.tv = _treeview(ens, self.COLS_ENSAYOS, alto=7)
        self.tv.tag_configure("sinid", background="#FFF3CD")
        self.tv.bind("<Double-1>", lambda e: self._editar())
        self._llenar()

    def _examinar(self, var, tipo):
        r = (filedialog.askdirectory(initialdir=var.get() or None, parent=self) if tipo == "dir"
             else filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("Todos", "*.*")], parent=self))
        if r:
            var.set(str(Path(r)))

    def _llenar(self):
        self.tv.delete(*self.tv.get_children())
        for i, (codigo, e) in enumerate(sorted(self.cfg_raw.get("ensayos", {}).items())):
            kit = ", ".join(f"{k} → {v}" for k, v in e.get("controles_kit", {}).items())
            tags = ["sinid"] if not e.get("assay_id") else (["par"] if i % 2 else [])
            self.tv.insert("", "end", iid=codigo, tags=tags, values=(
                codigo, e.get("nombre", ""), "" if e.get("assay_id") is None else e["assay_id"],
                e.get("analyte_id", ""), kit))

    def _editar(self, codigo=None):
        if codigo is None:
            sel = self.tv.selection()
            if not sel:
                return
            codigo = sel[0]
        d = DialogoEnsayo(self, codigo, self.cfg_raw.setdefault("ensayos", {}).get(codigo, {}))
        if d.resultado is not None:
            self.cfg_raw["ensayos"][codigo] = d.resultado
            self._llenar()

    def _agregar(self):
        codigo = simpledialog_codigo(self)
        if not codigo:
            return
        if codigo in self.cfg_raw.get("ensayos", {}):
            messagebox.showwarning(TITULO, f"La prueba {codigo} ya existe.", parent=self)
            return
        nombre = next((r.nombre_ensayo for r in self.app.resultados if r.ensayo == codigo), "")
        self.cfg_raw.setdefault("ensayos", {})[codigo] = {
            "nombre": nombre, "assay_id": None, "analyte_id": "", "instrumento": "", "controles_kit": {}}
        self._editar(codigo)

    def _eliminar(self):
        sel = self.tv.selection()
        if sel and messagebox.askyesno(TITULO, f"¿Eliminar la prueba {sel[0]}?", parent=self):
            del self.cfg_raw["ensayos"][sel[0]]
            self._llenar()

    def _guardar(self):
        nuevo = dict(self.cfg_raw)
        for clave, etiqueta, tipo in self.CAMPOS:
            texto = self.vars[clave].get().strip()
            if tipo == "int":
                if not texto.isdigit():
                    messagebox.showerror(TITULO, f'"{etiqueta}" debe ser un número entero.', parent=self)
                    return
                nuevo[clave] = int(texto)
            else:
                nuevo[clave] = texto
        try:
            eqc = json.loads(self.txt_eqc.get("1.0", "end").strip() or "{}")
            if not isinstance(eqc, dict):
                raise ValueError("debe ser un objeto JSON { ... }")
        except ValueError as e:
            messagebox.showerror(TITULO, f"Controles EQC: JSON inválido\n{e}", parent=self)
            return
        nuevo["controles_eqc"] = eqc
        ruta = self.app.ruta_config
        try:
            if ruta.exists():
                ruta.with_suffix(".json.bak").write_bytes(ruta.read_bytes())
            ruta.write_text(json.dumps(nuevo, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError as e:
            messagebox.showerror(TITULO, f"No se pudo guardar:\n{e}", parent=self)
            return
        self.destroy()
        self.app.recargar()


def simpledialog_codigo(padre) -> str:
    win = tk.Toplevel(padre)
    win.title("Nueva prueba")
    win.transient(padre)
    frm = ttk.Frame(win, padding=14)
    frm.pack()
    ttk.Label(frm, text="Código de ensayo Alinity (ej. 422):").pack(anchor="w")
    v = tk.StringVar()
    ent = ttk.Entry(frm, textvariable=v)
    ent.pack(fill="x", pady=6)
    ent.focus_set()
    resultado = []

    def ok(_e=None):
        resultado.append(v.get().strip())
        win.destroy()
    ent.bind("<Return>", ok)
    ttk.Button(frm, text="Aceptar", command=ok).pack(anchor="e")
    win.grab_set()
    win.wait_window()
    return resultado[0] if resultado else ""


# ======================================================================
# Aplicacion
# ======================================================================

class App(tk.Tk):
    def __init__(self, ruta_config: Path):
        super().__init__()
        self.title(TITULO)
        self.geometry("1280x740")
        self.minsize(1000, 560)
        aplicar_estilo(self)
        # icono: junto al programa, o dentro del .exe (PyInstaller lo extrae en _MEIPASS)
        for carpeta in (Path(getattr(sys, "_MEIPASS", core.BASE_DIR)), core.BASE_DIR):
            if (carpeta / "icono.ico").exists():
                try:
                    self.iconbitmap(default=str(carpeta / "icono.ico"))
                except tk.TclError:
                    pass
                break

        self.ruta_config = ruta_config
        self.cfg: dict = {"ensayos": {}}
        self.operador = ""
        self.logs_manual: list[Path] | None = None
        self.reprocesar = tk.BooleanVar(value=False)
        self.resultados: list[core.ResultadoQC] = []
        self.procesados: set[str] = set()
        self.cola: queue.Queue = queue.Queue()
        self.ocupado = False

        self._menu()
        self._encabezado()
        pie = ttk.Frame(self, padding=(12, 3))
        pie.pack(fill="x", side="bottom")
        self.estado = tk.StringVar(value="")
        ttk.Label(pie, textvariable=self.estado, style="Suave.TLabel").pack(side="left")
        ttk.Label(pie, text="Creado por Andrés Hernández  ·  Soporte 305 895 4155",
                  style="Suave.TLabel").pack(side="right")
        self.contenedor = ttk.Frame(self)
        self.contenedor.pack(fill="both", expand=True)
        self.pant_nombre = PantallaNombre(self)
        self.pant_sel = PantallaSeleccion(self)

        self.protocol("WM_DELETE_WINDOW", self._cerrar)
        self.after(100, self._atender_cola)
        self.mostrar_nombre()

    # ---------------- estructura

    def _menu(self):
        m = tk.Menu(self)
        archivo = tk.Menu(m, tearoff=False)
        archivo.add_command(label="Abrir logs...", command=self._elegir_logs)
        archivo.add_command(label="Usar carpeta de logs", command=self._usar_carpeta)
        archivo.add_command(label="Recargar", accelerator="F5", command=self.recargar)
        archivo.add_separator()
        archivo.add_command(label="Abrir carpeta de salida", command=self._abrir_salida)
        archivo.add_separator()
        archivo.add_command(label="Salir", command=self._cerrar)
        m.add_cascade(label="Archivo", menu=archivo)
        herr = tk.Menu(m, tearoff=False)
        herr.add_command(label="Controles leídos...", command=lambda: VentanaControles(self))
        herr.add_command(label="Configuración...", command=lambda: VentanaConfig(self))
        m.add_cascade(label="Herramientas", menu=herr)
        self.config(menu=m)
        self.bind("<F5>", lambda e: self.recargar())

    def _encabezado(self):
        h = ttk.Frame(self, style="Header.TFrame", padding=(18, 12))
        h.pack(fill="x")
        ttk.Label(h, text=TITULO, style="Header.TLabel").pack(side="left")
        ttk.Label(h, text="  Controles de calidad · Banco de Sangre", style="HeaderSub.TLabel").pack(side="left")
        self.btn_cambiar = ttk.Button(h, text="Cambiar", style="Header.TButton", command=self.mostrar_nombre)
        self.var_operador = tk.StringVar()
        self.lbl_operador = ttk.Label(h, textvariable=self.var_operador, style="HeaderSub.TLabel")

    def mostrar_nombre(self):
        self.pant_sel.pack_forget()
        self.btn_cambiar.pack_forget()
        self.lbl_operador.pack_forget()
        self.pant_nombre.pack(fill="both", expand=True)
        self.pant_nombre.mostrar()

    def iniciar_sesion(self, nombre: str):
        self.operador = nombre
        self.var_operador.set(f"Procesa:  {nombre}")
        self.btn_cambiar.pack(side="right")
        self.lbl_operador.pack(side="right", padx=10)
        self.pant_nombre.pack_forget()
        self.pant_sel.pack(fill="both", expand=True)
        self.recargar()

    # ---------------- hilos

    def en_hilo(self, trabajo, al_terminar, mensaje: str):
        if self.ocupado:
            return
        self.ocupado = True
        self.estado.set(mensaje)
        self.config(cursor="watch")

        def correr():
            try:
                self.cola.put((al_terminar, trabajo(), None))
            except Exception as e:  # se muestra en la interfaz
                self.cola.put((al_terminar, None, e))
        threading.Thread(target=correr, daemon=True).start()

    def _atender_cola(self):
        try:
            while True:
                al_terminar, valor, error = self.cola.get_nowait()
                self.ocupado = False
                self.config(cursor="")
                if error is not None:
                    self.estado.set(f"Error: {error}")
                    messagebox.showerror(TITULO, str(error))
                else:
                    al_terminar(valor)
        except queue.Empty:
            pass
        self.after(150, self._atender_cola)

    # ---------------- datos

    def recargar(self):
        if not self.operador:
            return
        reprocesar = self.reprocesar.get()
        manual = self.logs_manual

        def trabajo():
            cfg = core.cargar_config(self.ruta_config)
            # envios anteriores que EDCNet aun procesaba: las fallas vuelven a pendientes
            resueltos = core.revisar_envios_sin_resultado(cfg)
            logs = manual if manual is not None else core.logs_recientes(cfg)
            resultados, filas, avisos, procesados = core.preparar(cfg, logs, reprocesar)
            if reprocesar:   # conservar el historial al marcar lo nuevo
                procesados = core.cargar_estado()
            lotes = core.lotes_eqc_sin_asignar(cfg, resultados, procesados)
            cargados = []
            if lotes and cfg.get("asignar_lotes_auto", True):
                # lote nuevo con un unico material de EDCNet que coincide: se carga solo
                cargados = core.asignar_lotes_automatico(self.ruta_config, lotes)
                if cargados:
                    cfg = core.cargar_config(self.ruta_config)
                    resultados, filas, avisos, _ = core.preparar(cfg, logs, reprocesar)
                    lotes = core.lotes_eqc_sin_asignar(cfg, resultados, procesados)
            vencen = core.vencimientos_controles(resultados, int(cfg.get("aviso_vencimiento_dias", 1)))
            return cfg, logs, resultados, filas, avisos, procesados, resueltos, lotes, cargados, vencen

        def listo(valor):
            (self.cfg, logs, self.resultados, filas, avisos, self.procesados, resueltos, lotes,
             cargados, vencen) = valor
            self.pant_sel.cargar(filas, avisos, lotes, vencen)
            if cargados:
                messagebox.showinfo("Lote EQC nuevo cargado", "Se cargaron automáticamente:\n\n" + "\n".join(
                    f"• {l['control']} lote {l['lote']}  →  "
                    f"{dict(l['opciones']).get(l['sugerido'], l['sugerido'])}" for l in cargados)
                    + "\n\nEl número de lote coincide con un único material de EDCNet. Si no es "
                      "correcto, corríjalo en Herramientas → Configuración.")
            for archivo, resultado, devueltas in resueltos:
                if resultado["fallas"]:
                    DialogoResultado(self, archivo, {
                        "estado": "subido", "detalle": "", "resultado": resultado, "devueltas": devueltas,
                        "lineas": core.detalle_resultado(archivo, resultado)},
                        "Resultado de un envío anterior")
            origen = "logs seleccionados" if self.logs_manual is not None else "carpeta de logs"
            self.estado.set(f"{len(logs)} log(s) leídos de la {origen} · {len(self.resultados)} "
                            f"controles · {len(filas)} pendientes de envío · "
                            f"actualizado {datetime.now():%H:%M:%S}")
        self.en_hilo(trabajo, listo, "Leyendo logs...")

    def _elegir_logs(self):
        rutas = filedialog.askopenfilenames(
            title="Logs de la interfaz Alinity ci", initialdir=self.cfg.get("carpeta_logs") or None,
            filetypes=[("Logs", "*.txt"), ("Todos", "*.*")])
        if rutas:
            self.logs_manual = [Path(r) for r in rutas]
            self.recargar()

    def _usar_carpeta(self):
        self.logs_manual = None
        self.recargar()

    def _abrir_salida(self):
        carpeta = Path(self.cfg.get("carpeta_salida", ""))
        if carpeta.is_dir():
            os.startfile(carpeta)
        else:
            messagebox.showwarning(TITULO, f"No existe la carpeta:\n{carpeta}")

    def _cerrar(self):
        if self.ocupado and not messagebox.askyesno(
                TITULO, "Hay un envío en curso. ¿Cerrar de todos modos?"):
            return
        self.destroy()


def main():
    ap = argparse.ArgumentParser(description=TITULO)
    ap.add_argument("--config", type=Path, default=core.CONFIG_FILE)
    args = ap.parse_args()
    App(args.config).mainloop()


if __name__ == "__main__":
    main()
