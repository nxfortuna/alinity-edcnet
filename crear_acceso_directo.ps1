# Crea el acceso directo "EDCNet Alinity" en el escritorio del usuario.
# Usa el .exe si existe; si no, abre el programa con pythonw.
$carpeta = $PSScriptRoot
$exe = Join-Path $carpeta "EDCNet Alinity.exe"
$escritorio = [Environment]::GetFolderPath("Desktop")
$acceso = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $escritorio "EDCNet Alinity.lnk"))

if (Test-Path $exe) {
    $acceso.TargetPath = $exe
} else {
    $acceso.TargetPath = (Get-Command pythonw.exe -ErrorAction Stop).Source
    $acceso.Arguments = '"' + (Join-Path $carpeta "alinity_edcnet_gui.py") + '"'
}
$acceso.WorkingDirectory = $carpeta
$acceso.IconLocation = (Join-Path $carpeta "icono.ico") + ",0"
$acceso.Description = "Controles de calidad Alinity ci -> EDCNet"
$acceso.Save()
Write-Output "Acceso directo creado en $escritorio -> $($acceso.TargetPath)"
