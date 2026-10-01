"""
Middleware Alinity ci -> EDCNet (NRL QConnect).

Lee los logs de la interfaz THARSIS (trama ASTM LIS2-A2 del Alinity ci),
extrae los resultados de control de calidad (ordenes con codigo de accion
"Q") y genera los .csv que el EDCNet Integration Agent sube desde su
carpeta "Ready for Upload", segun la EDCNet Integration Guide:

  - Controles de kit (fabricante)  -> ResultType 2, una fila por corrida
    con el valor de cada nivel en su columna "<Nombre> Value".
  - Controles externos (EQC)       -> ResultType 1, una fila por resultado.

Uso:
  python alinity_edcnet.py LOG [LOG ...]         genera el .csv con lo nuevo
  python alinity_edcnet.py LOG --listar          solo muestra los controles
  python alinity_edcnet.py --vigilar             revisa carpeta_logs cada N s
  opciones: --prueba (antepone "test_" al archivo, EDCNet valida sin grabar)
            --config RUTA  --reprocesar (ignora procesados.json)
"""

import argparse
import csv
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

CONFIG_FILE = BASE_DIR / "config.json"
ESTADO_FILE = BASE_DIR / "procesados.json"
ENVIOS_FILE = BASE_DIR / "envios.json"   # archivo -> {RecordID: GUIDs} y resultado de EDCNet

# Columnas fijas de la guia (Appendix A, CSVHeader). Las columnas de
# controles de kit ("<Nombre> Value") van entre el canal y el AnalyteID.
COLUMNAS_INICIO = [
    "AssayID", "RecordID", "Date", "Comment", "Valid", "ResultType",
    "Detection Operator", "Detection KitLotNumber",
    "Detection InstrumentIDOrInstrumentNameAndSerialNumber",
    "Detection InstrumentChannelID",
]
COLUMNAS_FIN = ["AnalyteID", "EQCNameOrEQCLotNumberID", "Value"]

# "10:30:50:186 Se recibio <STX>1H|...<ETX>35"  (ETB = trama intermedia)
FRAME_RE = re.compile(r"Se recibio \x02(\d)(.*?)\r?([\x03\x17])")
RANGO_RE = re.compile(r"^\s*(-?[\d.]+)\s*-\s*(-?[\d.]+)\s*$")


@dataclass
class ResultadoQC:
    guid: str
    muestra: str          # ID de la muestra de control (ej. 83108BE001)
    ensayo: str           # codigo de ensayo Alinity (ej. 422)
    nombre_ensayo: str
    control: str          # nombre del material de control (ej. HIV_LEVEL1)
    lote_control: str
    lote_reactivo: str
    valor: str
    unidades: str
    rango: str
    operador: str
    fecha: datetime       # fecha/hora de finalizacion del resultado
    modulo: str           # serie del modulo (ej. AI01900)
    vence_control: str = ""   # vencimiento del lote de control (AAAAMMDD)

    def en_rango(self) -> bool:
        m = RANGO_RE.match(self.rango or "")
        if not m:
            return True
        try:
            return float(m.group(1)) <= float(self.valor) <= float(m.group(2))
        except ValueError:
            return False


# --------------------------------------------------------------------------
# Lectura del log
# --------------------------------------------------------------------------

def _decodificar(linea: bytes) -> str:
    # El log mezcla latin-1 (mensajes del programa) y UTF-8 (datos del equipo).
    try:
        return linea.decode("utf-8")
    except UnicodeDecodeError:
        return linea.decode("latin-1")


def leer_registros(ruta: Path):
    """Devuelve los registros ASTM (texto sin STX/ETX/checksum) del log."""
    pendiente = ""
    for cruda in ruta.read_bytes().split(b"\n"):
        m = FRAME_RE.search(_decodificar(cruda))
        if not m:
            continue
        pendiente += m.group(2)
        if m.group(3) == "\x03":
            for registro in pendiente.split("\r"):
                if registro:
                    yield registro
            pendiente = ""


def _fecha_astm(texto: str) -> datetime | None:
    try:
        return datetime.strptime(texto[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return None


def extraer_controles(ruta: Path) -> list[ResultadoQC]:
    resultados: list[ResultadoQC] = []
    orden: dict | None = None

    def cerrar_orden():
        nonlocal orden
        o = orden
        orden = None
        if not o or o["accion"] != "Q" or "valor" not in o or "guid" not in o:
            return
        resultados.append(ResultadoQC(
            guid=o["guid"], muestra=o["muestra"], ensayo=o["ensayo"],
            nombre_ensayo=o["nombre_ensayo"], control=o.get("control", ""),
            lote_control=o.get("lote_control", ""),
            lote_reactivo=o.get("lote_reactivo", ""), valor=o["valor"],
            unidades=o["unidades"], rango=o["rango"], operador=o["operador"],
            fecha=o["fecha"], modulo=o["modulo"], vence_control=o.get("vence_control", ""),
        ))

    for reg in leer_registros(ruta):
        campos = reg.split("|")
        tipo = campos[0][:1]
        if tipo in ("H", "P", "L"):
            cerrar_orden()
        elif tipo == "O" and len(campos) > 11:
            cerrar_orden()
            test = campos[4].split("^")
            orden = {
                "muestra": campos[2],
                "ensayo": test[3] if len(test) > 3 else "",
                "nombre_ensayo": test[4] if len(test) > 4 else "",
                "accion": campos[11],
            }
        elif orden is None:
            continue
        elif tipo == "M" and len(campos) > 8 and campos[2] == "INV":
            if campos[4] == "CO":        # material de control: nombre, vence, lote
                orden["control"] = campos[3]
                orden["lote_control"] = campos[8]
                orden["vence_control"] = campos[6]
            elif campos[4] == "CA":      # cartucho de reactivo
                orden["lote_reactivo"] = campos[8]
        elif tipo == "R" and len(campos) > 13:
            clase = campos[2].split("^")[-1]
            if clase == "F":             # resultado final (S/CO, etc.)
                fecha = _fecha_astm(campos[12])
                if fecha is None:
                    continue
                orden.update(
                    valor=campos[3], unidades=campos[4], rango=campos[5],
                    operador=campos[10].split("^")[0], fecha=fecha,
                    modulo=campos[13],
                )
            elif clase == "G":           # identificador unico del resultado
                orden["guid"] = campos[3]
    cerrar_orden()
    return resultados


def cargar_controles(rutas: list[Path]) -> list[ResultadoQC]:
    """Une varios logs y quita retransmisiones (mismo GUID)."""
    vistos: dict[str, ResultadoQC] = {}
    for ruta in rutas:
        for r in extraer_controles(ruta):
            vistos.setdefault(r.guid, r)
    return sorted(vistos.values(), key=lambda r: (r.fecha, r.ensayo, r.muestra))


# --------------------------------------------------------------------------
# Configuracion y metadata de EDCNet
# --------------------------------------------------------------------------

def cargar_config(ruta: Path) -> dict:
    cfg = json.loads(ruta.read_text(encoding="utf-8"))
    cfg["_encabezados"] = {}
    cfg["_metadata"] = {}
    meta = cfg.get("archivo_metadata")
    if meta:
        ruta_meta = Path(meta)
        if not ruta_meta.is_absolute():
            ruta_meta = ruta.parent / ruta_meta
        datos = json.loads(ruta_meta.read_text(encoding="utf-8-sig"))
        # EDCNet entrega una lista de ensayos o {"AssaysMetadata": [...]}
        ensayos = datos if isinstance(datos, list) else datos.get("AssaysMetadata", [])
        for ensayo in ensayos:
            cfg["_metadata"][str(ensayo["AssayID"])] = ensayo
            cfg["_encabezados"][str(ensayo["AssayID"])] = [
                c.strip() for c in ensayo["CSVHeader"].split(",")
            ]
    return cfg


def metadata_ensayo(cfg: dict, ens_cfg: dict) -> dict:
    return cfg.get("_metadata", {}).get(str(ens_cfg.get("assay_id")), {})


def _proceso_deteccion(meta: dict) -> dict:
    for tp in meta.get("TestProcessesMetadata", []):
        if tp.get("IsDetectionTestProcess"):
            return tp
    return {}


def instrumento_para(cfg: dict, ens_cfg: dict, modulo: str) -> tuple[str, str]:
    """(instrumento, canal) para el modulo que corrio el control. Prioridad:
    config del ensayo, InstrumentID de la metadata por numero de serie, config global."""
    canal = str(cfg.get("canal_instrumento", "1"))
    if ens_cfg.get("instrumento"):
        return str(ens_cfg["instrumento"]), canal
    for inst in _proceso_deteccion(metadata_ensayo(cfg, ens_cfg)).get("InstrumentsMetadata", []):
        if str(inst.get("SerialNumber", "")).upper() == modulo.upper():
            canales = inst.get("InstrumentChannelsMetadata") or [{"ID": canal}]
            return str(inst["InstrumentID"]), str(canales[0]["ID"])
    return str(cfg.get("instrumento", "")), canal


def lote_cumple_mascara(lote: str, mascara: str) -> bool:
    """Mascara de EDCNet: '#' = digito, '?' = letra o digito, otro = literal."""
    if not mascara:
        return True
    if len(lote) != len(mascara):
        return False
    for c, m in zip(lote, mascara):
        if (m == "#" and not c.isdigit()) or (m == "?" and not c.isalnum()) \
                or (m not in "#?" and c != m):
            return False
    return True


def _nombre_lote(nombre_meta: str) -> str:
    """"Optitrol Yellow (DM25143)" -> "Optitrol Yellow:DM25143" (formato de la guia)."""
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", nombre_meta)
    return f"{m.group(1)}:{m.group(2)}" if m else nombre_meta


def eqc_valido(meta: dict, valor: str) -> bool:
    """El EQC debe existir en la metadata: por ID o como "Nombre:Lote"."""
    lotes = meta.get("EQCLotNumbersMetadata")
    if lotes is None:
        return True   # sin metadata no se puede validar
    return any(valor in (str(l["ID"]), _nombre_lote(l["Name"])) for l in lotes)


def eqc_para_csv(cfg: dict, meta: dict, valor: str) -> str:
    """Con eqc_formato = "nombre" el ID se envia como "Nombre:Lote".
    EDCNet lo rechaza ("Invalid EQCLotNumberID") salvo que exista un alias en el
    agente, por eso el valor por defecto es el ID."""
    if cfg.get("eqc_formato", "id") != "nombre":
        return valor
    for l in meta.get("EQCLotNumbersMetadata", []):
        if str(l["ID"]) == valor:
            return _nombre_lote(l["Name"])
    return valor


def encabezado_ensayo(cfg: dict, ens_cfg: dict) -> list[str]:
    """Columnas del CSV para el ensayo: las de la metadata si esta cargada."""
    enc = cfg["_encabezados"].get(str(ens_cfg["assay_id"]))
    if enc:
        return enc
    niveles = list(dict.fromkeys(ens_cfg.get("controles_kit", {}).values()))
    return COLUMNAS_INICIO + [f"{n} Value" for n in niveles] + COLUMNAS_FIN


def _valor_eqc(regla, lote: str) -> str | None:
    """Valor EQC para el lote; None si ese lote esta excluido (null en por_lote)."""
    if isinstance(regla, dict):
        por_lote = regla.get("por_lote", {})
        if lote in por_lote and por_lote[lote] is None:
            return None
        return str(por_lote.get(lote, regla.get("eqc", ""))).format(lote=lote)
    return str(regla).format(lote=lote)


def _fuera(valor: str, bajo, alto) -> bool:
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return False
    return (bajo is not None and v < bajo) or (alto is not None and v > alto)


def _rango(bajo, alto) -> str:
    if bajo is None:
        return f"≤ {alto}"
    if alto is None:
        return f"≥ {bajo}"
    return f"{bajo} – {alto}"


def alertas_edcnet(cfg: dict, ens_cfg: dict, datos: dict) -> list[str]:
    """Valores que EDCNet marcara como advertencia segun los limites de su
    metadata: KitControlsMetadata para kit, NRL/Site del lote para EQC."""
    meta = metadata_ensayo(cfg, ens_cfg)
    alertas = []
    for k in meta.get("KitControlsMetadata", []):
        valor = datos.get(f"{k['Name']} Value", "")
        if valor and _fuera(valor, k.get("LowerLimit"), k.get("UpperLimit")):
            alertas.append(f"{k['Name']} {valor} fuera del límite EDCNet "
                           f"{_rango(k.get('LowerLimit'), k.get('UpperLimit'))}")
    lote_id, valor = str(datos.get("EQCNameOrEQCLotNumberID", "")), datos.get("Value", "")
    if lote_id and valor:
        for analito in meta.get("AnalytesMetadata", []):
            for v in analito.get("EQCValidationsMetadata", []):
                if str(v.get("EQCLotNumberID")) != lote_id:
                    continue
                for tipo in ("NRL", "Site"):
                    bajo, alto = v.get(f"{tipo}LowerLimit"), v.get(f"{tipo}UpperLimit")
                    if (bajo is not None or alto is not None) and _fuera(valor, bajo, alto):
                        alertas.append(f"EQC {valor} fuera del límite {tipo} {_rango(bajo, alto)}")
    return alertas


def lotes_eqc_sin_asignar(cfg: dict, resultados: list, procesados: set[str]) -> list[dict]:
    """Lotes de controles EQC pendientes cuyo valor no existe en la metadata
    (tipicamente un lote nuevo de Optitrol). Para asignarlos desde la GUI."""
    eqc_global = cfg.get("controles_eqc", {})
    grupos: dict[tuple, dict] = {}
    for r in resultados:
        ens_cfg = cfg.get("ensayos", {}).get(r.ensayo)
        if r.guid in procesados or not ens_cfg or not ens_cfg.get("assay_id"):
            continue
        eqc = {**eqc_global, **ens_cfg.get("controles_eqc", {})}
        if eqc.get(r.control) is None:   # no es EQC, o esta excluido
            continue
        lote = re.sub(r"^[A-Za-z_]+", "", r.lote_control)
        meta = metadata_ensayo(cfg, ens_cfg)
        valor = _valor_eqc(eqc[r.control], lote)
        if valor is None or eqc_valido(meta, valor):
            continue
        g = grupos.setdefault((r.control, lote), {"control": r.control, "lote": lote,
                                                  "ensayos": set(), "resultados": 0})
        g["ensayos"].add(r.ensayo)
        g["resultados"] += 1

    lotes = []
    for g in grupos.values():
        # materiales validos en TODAS las pruebas donde aparece el lote
        comunes = None
        nombres = {}
        for codigo in g["ensayos"]:
            meta = metadata_ensayo(cfg, cfg["ensayos"][codigo])
            ids = {str(l["ID"]) for l in meta.get("EQCLotNumbersMetadata", [])}
            nombres.update({str(l["ID"]): l["Name"] for l in meta.get("EQCLotNumbersMetadata", [])})
            comunes = ids if comunes is None else comunes & ids
        opciones = sorted(((i, nombres[i]) for i in (comunes or set())), key=lambda x: x[1])
        # sugerencia: el UNICO material cuyo lote termina igual, ej. 25143 -> "(DM25143)"
        coinciden = [i for i, n in opciones if re.search(re.escape(g["lote"]) + r"\)\s*$", n)]
        sugerido = coinciden[0] if len(coinciden) == 1 else None
        destino = next((c for c in g["ensayos"]
                        if g["control"] in cfg["ensayos"][c].get("controles_eqc", {})), None)
        lotes.append({**g, "ensayos": sorted(g["ensayos"]), "opciones": opciones,
                      "sugerido": sugerido, "destino": destino})
    return sorted(lotes, key=lambda x: (x["control"], x["lote"]))


def asignar_lotes_automatico(ruta_config: Path, lotes: list[dict]) -> list[dict]:
    """Carga solos los lotes nuevos cuyo numero coincide con un unico material de
    EDCNet (ej. CIP 24105 -> Optitrol Chagas-1 (DM24105)). Devuelve los cargados."""
    auto = [l for l in lotes if l.get("sugerido")]
    if auto:
        asignar_lotes_eqc(ruta_config, [(l["control"], l["lote"], l["sugerido"], l["destino"]) for l in auto])
    return auto


def vencimientos_controles(resultados: list, dias_aviso: int, hoy: date | None = None) -> list[dict]:
    """Lote en uso de cada material de control (el del resultado mas reciente) que
    vence dentro de dias_aviso dias o ya vencio."""
    hoy = hoy or date.today()
    en_uso: dict[str, ResultadoQC] = {}
    for r in resultados:
        if r.vence_control and (r.control not in en_uso or r.fecha > en_uso[r.control].fecha):
            en_uso[r.control] = r
    avisos = []
    for control, r in sorted(en_uso.items()):
        try:
            vence = datetime.strptime(r.vence_control[:8], "%Y%m%d").date()
        except ValueError:
            continue
        faltan = (vence - hoy).days
        if faltan <= dias_aviso:
            avisos.append({"control": control, "lote": r.lote_control, "vence": vence, "faltan": faltan})
    return avisos


def asignar_lotes_eqc(ruta_config: Path, asignaciones: list[tuple]) -> None:
    """Guarda en config.json: [(control, lote, ID o None, ensayo_destino o None)].
    ID None = ese lote no se envia."""
    raw = json.loads(ruta_config.read_text(encoding="utf-8"))
    for control, lote, valor, destino in asignaciones:
        contenedor = (raw["ensayos"][destino] if destino else raw).setdefault("controles_eqc", {})
        regla = contenedor.get(control)
        if not isinstance(regla, dict):
            regla = {"eqc": "" if regla is None else str(regla), "por_lote": {}}
        regla.setdefault("por_lote", {})[lote] = valor
        contenedor[control] = regla
    ruta_config.with_suffix(".json.bak").write_bytes(ruta_config.read_bytes())
    ruta_config.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Armado de filas
# --------------------------------------------------------------------------

@dataclass
class Fila:
    ensayo: str
    guids: list[str]
    datos: dict = field(default_factory=dict)
    fecha: datetime | None = None   # hora del (primer) resultado, para mostrar
    alertas: list[str] = field(default_factory=list)   # fuera de limites EDCNet


def es_tarde(cfg: dict, fecha: datetime | None) -> bool:
    """Resultado despues de la hora de "comentario_tarde" (controles de 100 pruebas)."""
    regla = cfg.get("comentario_tarde") or {}
    try:
        desde = datetime.strptime(regla.get("desde", ""), "%H:%M").time()
    except ValueError:
        return False
    return fecha is not None and fecha.time() >= desde


def comentario_auto(cfg: dict, fecha: datetime) -> str:
    """Comentario por hora del resultado, ej. los controles de la tarde son de
    100 pruebas: "comentario_tarde": {"desde": "15:00", "texto": "..."}."""
    return (cfg.get("comentario_tarde") or {}).get("texto", "") if es_tarde(cfg, fecha) else ""


def orden_subida(cfg: dict, f: "Fila") -> tuple:
    """Orden en el archivo, por dia: kit (interno), EQC (externo), EQC de la tarde."""
    grupo = 0 if f.datos.get("ResultType") == "2" else 2 if es_tarde(cfg, f.fecha) else 1
    return (f.datos.get("Date", ""), grupo, f.fecha or datetime.min, f.ensayo)


def _fila_base(cfg, ens_cfg, r: ResultadoQC, record_id, comentario, valido, tipo) -> dict:
    instrumento, canal = instrumento_para(cfg, ens_cfg, r.modulo)
    analito = ens_cfg.get("analyte_id") or ""
    analitos = metadata_ensayo(cfg, ens_cfg).get("AnalytesMetadata", [])
    if not analito and len(analitos) == 1:
        analito = analitos[0]["AnalyteID"]
    return {
        "AssayID": str(ens_cfg["assay_id"]),
        "RecordID": record_id,
        "Date": r.fecha.strftime("%Y-%m-%d"),
        "Comment": comentario,
        "Valid": "true" if valido else "false",
        "ResultType": str(tipo),
        # operador fijo de config (el del equipo varia segun quien cargo el control)
        "Detection Operator": (cfg.get("operador") or r.operador).upper(),
        "Detection KitLotNumber": r.lote_reactivo,
        "Detection InstrumentIDOrInstrumentNameAndSerialNumber": instrumento,
        "Detection InstrumentChannelID": canal,
        "AnalyteID": str(analito),
    }


def _agrupar_corridas(resultados, ens_cfg, max_min):
    """Agrupa controles de kit del mismo ensayo/lote/modulo en corridas:
    se abre una nueva cuando un nivel se repite o pasan max_min minutos."""
    grupos: dict[tuple, list[list[ResultadoQC]]] = {}
    for r in resultados:
        clave = (r.lote_reactivo, r.modulo)
        corridas = grupos.setdefault(clave, [])
        actual = corridas[-1] if corridas else None
        nivel = ens_cfg["controles_kit"][r.control]
        if (actual is None
                or nivel in {ens_cfg["controles_kit"][x.control] for x in actual}
                or r.fecha - actual[0].fecha > timedelta(minutes=max_min)):
            corridas.append([r])
        else:
            actual.append(r)
    return [c for corridas in grupos.values() for c in corridas]


def construir_filas(cfg: dict, resultados: list[ResultadoQC], ahora: datetime,
                    avisos: list[str]) -> list[Fila]:
    filas: list[Fila] = []
    eqc_global = cfg.get("controles_eqc", {})
    max_min = int(cfg.get("minutos_max_corrida", 120))
    sin_mapear: set[str] = set()

    for codigo in sorted({r.ensayo for r in resultados}):
        ens_cfg = cfg.get("ensayos", {}).get(codigo)
        del_ensayo = [r for r in resultados if r.ensayo == codigo]
        if not ens_cfg or not ens_cfg.get("assay_id"):
            avisos.append(f"Ensayo {codigo} ({del_ensayo[0].nombre_ensayo}) sin assay_id "
                          f"en config: {len(del_ensayo)} controles pendientes.")
            continue
        kit = ens_cfg.get("controles_kit", {})
        eqc = {**eqc_global, **ens_cfg.get("controles_eqc", {})}
        meta = metadata_ensayo(cfg, ens_cfg)
        if cfg["_metadata"] and not meta:
            avisos.append(f"Ensayo {codigo}: AssayID {ens_cfg['assay_id']} no esta en la metadata.")
        niveles_meta = {k["Name"] for k in meta.get("KitControlsMetadata", [])}
        for nivel in sorted(set(kit.values()) - niveles_meta if niveles_meta else ()):
            avisos.append(f"Ensayo {codigo}: el nivel de kit \"{nivel}\" no existe en EDCNet "
                          f"({', '.join(sorted(niveles_meta))}).")
        mascara = _proceso_deteccion(meta).get("KitLotNumberMask", "")
        lotes_malos = sorted({r.lote_reactivo for r in del_ensayo
                              if not lote_cumple_mascara(r.lote_reactivo, mascara)})
        if lotes_malos:
            avisos.append(f"Ensayo {codigo}: lote(s) de reactivo {', '.join(lotes_malos) or '(vacio)'} "
                          f"no cumplen la mascara {mascara}; esos controles quedan pendientes.")
        del_ensayo = [r for r in del_ensayo if r.lote_reactivo not in lotes_malos]

        # EQC: una fila por resultado
        eqc_malos: set[str] = set()
        for r in del_ensayo:
            if r.control in eqc and eqc[r.control] is None:
                continue  # control excluido a proposito (null en config)
            if r.control in eqc:
                lote = re.sub(r"^[A-Za-z_]+", "", r.lote_control)
                valor_eqc = _valor_eqc(eqc[r.control], lote)
                if valor_eqc is None:
                    continue  # ese lote se marco como "no enviar"
                if not eqc_valido(meta, valor_eqc):
                    eqc_malos.add(f"{r.control} lote {lote}")
                    continue
                d = _fila_base(cfg, ens_cfg, r,
                               f"EQC-{codigo}-{r.muestra}-{r.fecha:%Y%m%d%H%M%S}",
                               comentario_auto(cfg, r.fecha), r.en_rango(), 1)
                d["EQCNameOrEQCLotNumberID"] = valor_eqc
                d["Value"] = r.valor
                alertas = alertas_edcnet(cfg, ens_cfg, d)
                d["EQCNameOrEQCLotNumberID"] = eqc_para_csv(cfg, meta, valor_eqc)
                filas.append(Fila(codigo, [r.guid], d, r.fecha, alertas))
            elif r.control not in kit:
                sin_mapear.add(f"{codigo}/{r.control}")
        for x in sorted(eqc_malos):
            avisos.append(f"Ensayo {codigo}: {x} sin asignar a un EQC de EDCNet; quedan pendientes "
                          f"(Asignar lotes EQC).")

        # Controles de kit: una fila por corrida
        de_kit = [r for r in del_ensayo if r.control in kit]
        niveles_esperados = set(kit.values())
        for corrida in _agrupar_corridas(de_kit, ens_cfg, max_min):
            niveles = {kit[r.control]: r for r in corrida}
            completa = set(niveles) >= niveles_esperados
            vieja = ahora - corrida[-1].fecha > timedelta(minutes=max_min)
            if not completa and not vieja:
                continue  # esperar a que lleguen los demas niveles
            primero = corrida[0]
            d = _fila_base(cfg, ens_cfg, primero,
                           f"KC-{codigo}-{primero.lote_reactivo}-{primero.fecha:%Y%m%d%H%M%S}",
                           comentario_auto(cfg, primero.fecha),
                           all(r.en_rango() for r in corrida), 2)
            for nivel, r in niveles.items():
                d[f"{nivel} Value"] = r.valor
            d["AnalyteID"] = ""  # en filas solo-kit AnalyteID/EQC/Value van vacios
            filas.append(Fila(codigo, [r.guid for r in corrida], d, primero.fecha,
                              alertas_edcnet(cfg, ens_cfg, d)))

    for x in sorted(sin_mapear):
        avisos.append(f"Control sin mapear (ni kit ni EQC): {x}")
    return filas


# --------------------------------------------------------------------------
# Escritura
# --------------------------------------------------------------------------

def cargar_estado() -> set[str]:
    if ESTADO_FILE.exists():
        try:
            return set(json.loads(ESTADO_FILE.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            pass
    return set()


def guardar_estado(guids: set[str]) -> None:
    ESTADO_FILE.write_text(json.dumps(sorted(guids), indent=0), encoding="utf-8")


def escribir_csv(cfg: dict, filas: list[Fila], prueba: bool) -> Path:
    carpeta = Path(cfg["carpeta_salida"])
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre = f"{cfg.get('prefijo_archivo', 'Alinity ci')} {datetime.now():%Y%m%d_%H%M%S}.csv"
    if prueba:
        # el agente 1.5 usa "test_" (TestFilePrefix en su .config), no "test" como dice la guia
        nombre = "test_" + nombre
    destino = carpeta / nombre
    temporal = destino.with_suffix(".tmp")  # el agente solo toma *.csv
    with temporal.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        for f in sorted(filas, key=lambda f: orden_subida(cfg, f)):
            ens_cfg = cfg["ensayos"][f.ensayo]
            w.writerow([f.datos.get(c, "") for c in encabezado_ensayo(cfg, ens_cfg)])
    temporal.replace(destino)
    return destino


# --------------------------------------------------------------------------
# Subida inmediata: se le pide al servicio del agente EDCNet lo mismo que su
# boton "Upload Outbox Files Now", usando el cliente de pipe de su propia DLL.
# --------------------------------------------------------------------------

AGENTE_DIR = Path(r"C:\Program Files (x86)\EDCNetAgent")
AGENTE_CONFIG = Path(r"C:\ProgramData\NRL\EDCAgent\agentconfig.xml")
_PS_SUBIR = (
    "$ErrorActionPreference='Stop';"
    "[void][Reflection.Assembly]::LoadFrom('{dll}');"
    "(New-Object EDCNet.Client.Service.AgentServiceCommunication.NRLPipe).SendCommand("
    "'NRLAgentToServicePipe',(New-Object EDCNet.Client.Service.Commands.UploadOutboxFilesCommand))"
)


def subir_ahora(cfg: dict) -> str | None:
    """Pide al servicio EDCNetAgent que suba ya la carpeta Ready for Upload.
    Devuelve None si la orden llego, o el texto del error."""
    dll = Path(cfg.get("agente_dir") or AGENTE_DIR) / "EDCNet.Client.Service.dll"
    if not dll.exists():
        return f"no se encontro {dll}"
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             _PS_SUBIR.format(dll=str(dll).replace("'", "''"))],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return "el servicio EDCNetAgent no respondio (esta detenido?)"
    except OSError as e:
        return str(e)
    if r.returncode != 0:
        return (r.stderr or r.stdout).strip().splitlines()[0] if (r.stderr or r.stdout).strip() \
            else f"codigo {r.returncode}"
    return None


def carpetas_agente(cfg: dict) -> dict:
    """Carpetas del dispositivo configurado en el agente (agentconfig.xml)."""
    try:
        raiz = ET.parse(Path(cfg.get("agente_config") or AGENTE_CONFIG)).getroot()
        dev = raiz.find("MappedDevices")[0]
    except (OSError, ET.ParseError, IndexError, TypeError):
        return {}
    return {k: dev.findtext(k) or "" for k in
            ("OutboxFolderPath", "SuccessFolderPath", "FailFolderPath", "UnknownFolderPath")}


def subir_y_seguir(cfg: dict, archivo: Path, espera: int = 90) -> tuple[str, str]:
    """Dispara la subida y espera a que el agente ubique el archivo.
    Devuelve (estado, detalle); estado: subido | rechazado | procesando | pendiente | error."""
    error = subir_ahora(cfg)
    if error:
        return "error", error
    carpetas = carpetas_agente(cfg)

    def donde() -> str:
        for clave, estado in (("SuccessFolderPath", "subido"), ("FailFolderPath", "rechazado"),
                              ("UnknownFolderPath", "procesando")):
            if carpetas.get(clave) and (Path(carpetas[clave]) / archivo.name).exists():
                return estado
        return "pendiente"

    fin = time.monotonic() + espera
    otra_orden = time.monotonic() + 8
    while time.monotonic() < fin:
        estado = donde()
        if estado in ("subido", "rechazado"):
            return estado, ""
        # el agente deja el archivo en Processing y lo ubica en la pasada siguiente
        if estado == "procesando" and time.monotonic() >= otra_orden:
            subir_ahora(cfg)
            otra_orden = time.monotonic() + 8
        time.sleep(2)
    return donde(), ""


def subir_y_verificar(cfg: dict, archivo: Path) -> dict:
    """Sube ya, espera a que el agente lo ubique y consulta el resultado en
    EDCNet; las filas con falla vuelven a pendientes.
    Devuelve {estado, detalle, resultado, devueltas, lineas}."""
    estado, detalle = subir_y_seguir(cfg, archivo)
    salida = {"estado": estado, "detalle": detalle, "resultado": None, "devueltas": [], "lineas": []}
    if estado in ("error", "pendiente"):
        return salida
    for _ in range(6):   # EDCNet puede tardar unos segundos en tener el resultado
        try:
            resultado = consultar_resultado(cfg, archivo.name)
        except (OSError, ValueError, ET.ParseError) as e:
            salida["detalle"] = f"no se pudo consultar el resultado: {e}"
            return salida
        if resultado is not None:
            salida["resultado"] = resultado
            salida["devueltas"] = aplicar_resultado(archivo.name, resultado)
            salida["lineas"] = detalle_resultado(archivo.name, resultado)
            return salida
        time.sleep(5)
    return salida


def logs_recientes(cfg: dict) -> list[Path]:
    """Logs de carpeta_logs que cumplen patron_logs y tienen menos de dias_logs."""
    carpeta = Path(cfg["carpeta_logs"])
    limite = time.time() - int(cfg.get("dias_logs", 2)) * 86400
    return sorted(p for p in carpeta.glob(cfg.get("patron_logs", "*.txt"))
                  if p.stat().st_mtime >= limite)


def preparar(cfg: dict, logs: list[Path], reprocesar: bool):
    """Lee los logs y arma las filas nuevas (sin escribir nada).
    Devuelve (resultados, filas, avisos, procesados)."""
    resultados = cargar_controles(logs)
    procesados = set() if reprocesar else cargar_estado()
    avisos: list[str] = []
    filas = [f for f in construir_filas(cfg, resultados, datetime.now(), avisos)
             if not any(g in procesados for g in f.guids)]
    return resultados, filas, avisos, procesados


def confirmar(cfg: dict, filas: list[Fila], procesados: set[str], prueba: bool) -> Path:
    """Escribe el .csv y, si no es prueba, marca los GUID como procesados."""
    destino = escribir_csv(cfg, filas, prueba)
    if not prueba:
        guardar_estado(procesados | {g for f in filas for g in f.guids})
        registrar_envio(destino.name, filas)
    return destino


# --------------------------------------------------------------------------
# Resultado del envio en EDCNet: se consulta igual que lo hace el servicio del
# agente (POST de formulario a UploadService/GetSiteUploadStatusByFileName).
# --------------------------------------------------------------------------

URL_ESTADO = "http://webapi.edcnet.nrlquality.org.au/api/UploadService/GetSiteUploadStatusByFileName"


def _leer_envios() -> dict:
    try:
        return json.loads(ENVIOS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _guardar_envios(envios: dict) -> None:
    # solo los ultimos 300 archivos
    recientes = dict(sorted(envios.items(), key=lambda kv: kv[1].get("fecha", ""))[-300:])
    ENVIOS_FILE.write_text(json.dumps(recientes, indent=1, ensure_ascii=False), encoding="utf-8")


def registrar_envio(nombre_archivo: str, filas: list[Fila]) -> None:
    envios = _leer_envios()
    envios[nombre_archivo] = {
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "filas": {f.datos["RecordID"]: f.guids for f in filas},
        "resultado": None,
    }
    _guardar_envios(envios)


def consultar_resultado(cfg: dict, nombre_archivo: str) -> dict | None:
    """Resultado de EDCNet para un archivo, o None si aun no lo proceso.
    Lanza OSError/ValueError si no hay conexion o credenciales."""
    raiz = ET.parse(Path(cfg.get("agente_config") or AGENTE_CONFIG)).getroot()
    clave = (raiz.findtext("SiteKey") or "").strip()
    if not clave:
        raise ValueError("el agente no tiene Site Key configurada")
    datos = urllib.parse.urlencode({"siteKey": clave, "fileName": nombre_archivo}).encode()
    with urllib.request.urlopen(urllib.request.Request(URL_ESTADO, data=datos), timeout=20) as r:
        resp = json.loads(r.read().decode("utf-8"))
    if resp.get("result") not in (0, None):
        raise ValueError(resp.get("message") or f"EDCNet respondio codigo {resp.get('result')}")
    res = resp.get("dataImportResults") or []
    if not res:
        return None
    ultimo = max(res, key=lambda x: x.get("createDate", ""))
    return {
        "guardados": ultimo.get("savedCount", 0), "advertencias": ultimo.get("warningCount", 0),
        "duplicados": ultimo.get("duplicateCount", 0), "fallas": ultimo.get("failureCount", 0),
        "mensaje": ultimo.get("resultMessage") or "",
    }


def _clasificar(mensaje: str, record_ids) -> tuple[dict, list[str]]:
    """Separa el resultMessage por registro: {RecordID: (tipo, texto)} y lineas sin ID.
    tipo: falla | duplicado | advertencia."""
    ids = sorted(record_ids, key=len, reverse=True)   # "...-R0930" antes que su base
    por_id, sueltas = {}, []
    for entrada in [e for e in re.split(r"\r?\n", mensaje) if e.strip()]:
        texto = re.sub(r"\s*<br\s*/?>\s*", " · ", entrada).strip(" ·")
        rid = next((i for i in ids if re.search(re.escape(i) + r"(?=[,\s<]|$)", entrada)), None)
        if "duplicate" in entrada.lower():
            tipo = "duplicado"
        elif "validation" in entrada.lower() and "exception" not in entrada.lower():
            tipo = "advertencia"   # se guarda, pero fuera de limites
        else:
            tipo = "falla"
        if rid:
            por_id[rid] = (tipo, texto)
        else:
            sueltas.append(texto)
    return por_id, sueltas


def aplicar_resultado(nombre_archivo: str, resultado: dict) -> list[str]:
    """Guarda el resultado y devuelve a pendientes las filas que fallaron.
    Devuelve los RecordID devueltos a pendientes."""
    envios = _leer_envios()
    envio = envios.get(nombre_archivo)
    if envio is None:
        return []
    por_id, _ = _clasificar(resultado["mensaje"], envio["filas"])
    fallidas = [rid for rid, (tipo, _) in por_id.items() if tipo == "falla"]
    if fallidas:
        quitar = {g for rid in fallidas for g in envio["filas"].get(rid, [])}
        guardar_estado(cargar_estado() - quitar)
    envio["resultado"] = {**resultado, "devueltas": fallidas,
                          "consultado": datetime.now().isoformat(timespec="seconds")}
    _guardar_envios(envios)
    return fallidas


def detalle_resultado(nombre_archivo: str, resultado: dict) -> list[str]:
    """Lineas legibles: una por registro con falla/duplicado/advertencia."""
    filas = _leer_envios().get(nombre_archivo, {}).get("filas", {})
    por_id, sueltas = _clasificar(resultado["mensaje"], filas)
    nombres = {"falla": "✖ Falla", "duplicado": "• Duplicado", "advertencia": "⚠ Advertencia"}
    lineas = []
    for rid, (tipo, texto) in por_id.items():
        motivo = texto.split(" · Original Record")[0].split(" · Suggestion")[0]
        lineas.append(f"{nombres[tipo]}  {rid}\n      {motivo[:220]}")
    return lineas + [s[:240] for s in sueltas]


def revisar_envios_sin_resultado(cfg: dict, dias: int = 3) -> list[tuple[str, dict, list[str]]]:
    """Consulta los envios recientes que quedaron sin resultado (EDCNet aun
    procesaba). Devuelve (archivo, resultado, devueltas) de los que ya tienen."""
    limite = (datetime.now() - timedelta(days=dias)).isoformat()
    listos = []
    for nombre, envio in _leer_envios().items():
        if envio.get("resultado") is None and envio.get("fecha", "") >= limite:
            try:
                resultado = consultar_resultado(cfg, nombre)
            except (OSError, ValueError):
                break   # sin conexion: se intenta en la proxima lectura
            except ET.ParseError:
                break
            if resultado is not None:
                listos.append((nombre, resultado, aplicar_resultado(nombre, resultado)))
    return listos


def procesar(cfg: dict, logs: list[Path], prueba: bool, reprocesar: bool) -> Path | None:
    resultados, filas, avisos, procesados = preparar(cfg, logs, reprocesar)
    for a in avisos:
        print("AVISO:", a)
    if not filas:
        print(f"{len(resultados)} controles leidos, nada nuevo para subir.")
        return None
    destino = confirmar(cfg, filas, procesados, prueba)
    print(f"{len(resultados)} controles leidos, {len(filas)} filas -> {destino}")
    if cfg.get("subir_inmediato", True):
        s = subir_y_verificar(cfg, destino)
        print(f"Subida EDCNet: {s['estado']} {s['detalle']}".rstrip())
        if s["resultado"]:
            r = s["resultado"]
            print(f"  guardados {r['guardados']}, duplicados {r['duplicados']}, "
                  f"advertencias {r['advertencias']}, fallas {r['fallas']}")
            for linea in s["lineas"]:
                print("  " + linea)
            if s["devueltas"]:
                print(f"  {len(s['devueltas'])} fila(s) con falla volvieron a pendientes.")
    return destino


def listar(logs: list[Path]) -> None:
    w = csv.writer(sys.stdout, lineterminator="\n")
    w.writerow(["fecha", "ensayo", "nombre", "muestra", "control", "lote_control",
                "lote_reactivo", "valor", "unidades", "rango", "en_rango", "operador", "modulo"])
    for r in cargar_controles(logs):
        w.writerow([f"{r.fecha:%Y-%m-%d %H:%M:%S}", r.ensayo, r.nombre_ensayo, r.muestra,
                    r.control, r.lote_control, r.lote_reactivo, r.valor, r.unidades,
                    r.rango, "si" if r.en_rango() else "NO", r.operador, r.modulo])


def main() -> None:
    ap = argparse.ArgumentParser(description="Convierte logs del Alinity ci a CSV de EDCNet.")
    ap.add_argument("logs", nargs="*", type=Path)
    ap.add_argument("--config", type=Path, default=CONFIG_FILE)
    ap.add_argument("--listar", action="store_true", help="solo lista los controles encontrados")
    ap.add_argument("--prueba", action="store_true", help='archivo "test..." (EDCNet valida sin grabar)')
    ap.add_argument("--reprocesar", action="store_true", help="ignora procesados.json")
    ap.add_argument("--vigilar", action="store_true", help="revisa carpeta_logs periodicamente")
    args = ap.parse_args()

    if args.listar:
        listar(args.logs)
        return

    cfg = cargar_config(args.config)
    if not args.vigilar:
        if not args.logs:
            ap.error("indique uno o mas logs, o use --vigilar")
        procesar(cfg, args.logs, args.prueba, args.reprocesar)
        return

    intervalo = int(cfg.get("intervalo_segundos", 300))
    print(f"Vigilando {Path(cfg['carpeta_logs']) / cfg.get('patron_logs', '*.txt')} "
          f"cada {intervalo} s (Ctrl+C para salir)")
    while True:
        try:
            procesar(cfg, logs_recientes(cfg), args.prueba, False)
        except Exception as e:  # que un log corrupto no detenga el servicio
            print("ERROR:", e)
        time.sleep(intervalo)


if __name__ == "__main__":
    main()
