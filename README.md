# Alinity ci → EDCNet

Middleware que lee los logs de la interfaz THARSIS del **Abbott Alinity ci**, extrae los
resultados de **control de calidad** (controles de kit y EQC/QConnect) y los transmite a
**EDCNet (NRL QConnect)** a través del *EDCNet Integration Agent*, siguiendo la
*EDCNet Integration Guide*.

Creado por Andrés Hernández · Soporte 305 895 4155

---

## Qué hace

1. Lee las tramas ASTM LIS2-A2 de los logs (`* Log ALINITYCI*.txt`) y toma las órdenes de
   control (código de acción `Q`), con su lote de reactivo, lote de control, operador y módulo.
2. Arma las filas CSV con el formato de la metadata de cada ensayo:
   - **Controles de kit** → `ResultType 2`, una fila por corrida con los niveles en sus columnas.
   - **EQC** (CIP / Optitrol) → `ResultType 1`, una fila por resultado con el ID del lote EQC.
3. En la interfaz gráfica, quien procesa elige qué transmitir, agrega comentarios y genera el
   archivo en la carpeta *Ready for Upload* del agente.
4. Pide al agente que **suba en el momento** (sin esperar su ciclo) y **consulta el resultado**
   en EDCNet: guardados, duplicados, advertencias y fallas. Las filas con falla vuelven solas a
   pendientes.

## Funciones de la interfaz

- Selección del operador (lista de los anteriores; siempre en mayúsculas).
- Tabla de controles pendientes, todos marcados y filtrados por el día; filtros por fecha y prueba.
- Comentario por fila o para todas las marcadas; los controles desde las 15:00 llevan
  `CONTROL DE 100 PRUEBAS` automáticamente.
- Orden de subida por día: controles de kit, EQC, EQC de la tarde.
- Aviso de valores fuera de los **límites de EDCNet** (kit y NRL/Site) antes de enviar.
- **Mostrar ya enviados** y **Reenviar con ID nuevo** (para resultados borrados en EDCNet, que
  recuerda el `ClientResultID`).
- **Lotes EQC nuevos**: se cargan solos si el número de lote coincide con un único material de la
  metadata; si no, se asignan desde una lista.
- **Aviso de vencimiento** del lote de control en uso (por defecto, un día antes).
- Herramientas: controles leídos (incluye excluidos y procesados) y configuración.

## Requisitos

- Windows con el **EDCNet Integration Agent 1.5** instalado y configurado (carpetas y Site Key),
  y su servicio `EDCNetAgent` iniciado.
- Python 3.10 o superior (solo biblioteca estándar), **o** el ejecutable generado con PyInstaller.

## Instalación

```bat
git clone <este repositorio>
cd edcnet_middleware
```

Revisar `config.json` (rutas de logs y salida) y colocar la metadata de EDCNet en
`metadata_edcnet.json` (EDCNet → *Data Integration* → *Data Integration Details* → *Get Assays Metadata*).

### Ejecutar

```bat
pythonw alinity_edcnet_gui.py
```

### Generar el .exe y el acceso directo

```bat
build_exe.bat
powershell -ExecutionPolicy Bypass -File crear_acceso_directo.ps1
```

`build_exe.bat` instala PyInstaller y deja `EDCNet Alinity.exe` en esta misma carpeta: el
programa lee `config.json` y guarda su estado **junto al ejecutable**.

### Línea de comandos

```bat
python alinity_edcnet.py LOG [LOG ...]      genera el .csv con lo nuevo y lo sube
python alinity_edcnet.py LOG --listar       solo lista los controles encontrados
python alinity_edcnet.py LOG --prueba       archivo "test_..." (EDCNet valida sin grabar)
```

## Configuración (`config.json`)

| Clave | Descripción |
|---|---|
| `carpeta_logs`, `patron_logs`, `dias_logs` | Dónde y cuántos días de logs leer (7 por defecto) |
| `carpeta_salida` | Carpeta *Ready for Upload* del agente |
| `archivo_metadata` | Metadata de EDCNet (lista de ensayos o `{"AssaysMetadata": [...]}`) |
| `operador` | Operador por defecto para la línea de comandos |
| `comentario_tarde` | `{"desde": "15:00", "texto": "CONTROL DE 100 PRUEBAS"}` |
| `subir_inmediato` | Pedir la subida al agente al generar (true) |
| `asignar_lotes_auto` | Cargar solos los lotes EQC nuevos que coinciden (true) |
| `aviso_vencimiento_dias` | Días antes del vencimiento del control para avisar (1) |
| `eqc_formato` | `"id"` (recomendado) o `"nombre"` (requiere alias en el agente) |
| `controles_eqc` | Control del equipo → lote EQC de EDCNet: `{"CIP_LEVEL1": {"por_lote": {"25143": "1342"}}}`; `null` = no se envía |
| `ensayos` | Por código Alinity: `assay_id`, `analyte_id`, `controles_kit` (control → nivel EDCNet) |

## Detalles de integración con el agente EDCNet 1.5

Comprobados en esta instalación (difieren o no figuran en la guía):

- **Prefijo de prueba**: el agente reconoce `test_` (clave `TestFilePrefix` de su `.config`), no
  `test` como dice la guía. Un archivo `testXXX.csv` se graba como dato real.
- **Subida inmediata**: el servicio escucha en el pipe `\\.\pipe\NRLAgentToServicePipe`; el
  programa usa `NRLPipe.SendCommand` de `EDCNet.Client.Service.dll` con un
  `UploadOutboxFilesCommand` (lo mismo que el botón *Upload Outbox Files Now*).
- **Resultado**: `POST` de formulario (`siteKey`, `fileName`) a
  `.../api/UploadService/GetSiteUploadStatusByFileName`, con la Site Key del `agentconfig.xml`.
- El agente deja el archivo en *Processing* y lo mueve a *Successful*/*Rejected* en la pasada
  siguiente.
- EDCNet rechaza `Nombre:Lote` en el EQC salvo que exista un alias; se envía el ID del lote.
- Los duplicados se detectan por `ClientResultID` (= RecordID), aun si el resultado se borró.

## Archivos de estado (no versionados)

| Archivo | Contenido |
|---|---|
| `procesados.json` | GUIDs de resultados ya enviados |
| `envios.json` | Por archivo enviado: RecordIDs, GUIDs y resultado de EDCNet |
| `ui_estado.json` | Operadores usados en la interfaz |

## Estructura

```
alinity_edcnet.py         parser, armado del CSV, subida y consulta de resultados
alinity_edcnet_gui.py     interfaz gráfica (Tkinter)
config.json               configuración del sitio
metadata_edcnet.json      metadata de EDCNet de los ensayos
icono.ico                 icono (herramientas/crear_icono.py lo regenera)
build_exe.bat             genera EDCNet Alinity.exe
crear_acceso_directo.ps1  acceso directo en el escritorio
```
