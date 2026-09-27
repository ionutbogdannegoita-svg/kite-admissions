<#
.SYNOPSIS
  Prima installazione di KITE Admissions su questo PC.

.DESCRIPTION
  1. Crea l'ambiente Python isolato .venv nella cartella del programma (se manca).
  2. Installa le dipendenze fissate in requirements.txt (con -Dev anche quelle dei test).
  3. Crea il collegamento "Avvia KITE Admissions" (predefinito: sul Desktop).

  Non crea né modifica dati: il database nasce al primo avvio in
  %LOCALAPPDATA%\KITEAdmissions\data\admissions.sqlite3.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#>
param(
    [string]$ShortcutPath = (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Avvia KITE Admissions.lnk'),
    [switch]$NoShortcut,
    [switch]$Dev
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $repo '.venv'
$python = Join-Path $venv 'Scripts\python.exe'
$pythonw = Join-Path $venv 'Scripts\pythonw.exe'

function Assert-LastExit([string]$step) {
    if ($LASTEXITCODE -ne 0) { throw "Passo non riuscito: $step (codice $LASTEXITCODE)" }
}

if (-not (Test-Path $python)) {
    Write-Host 'Creo l''ambiente Python isolato (.venv)...'
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv $venv
    } else {
        & python -m venv $venv
    }
    Assert-LastExit 'creazione .venv'
}

& $python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
Assert-LastExit 'serve Python 3.11 o successivo'

$requirements = 'requirements.txt'
if ($Dev) { $requirements = 'requirements-dev.txt' }
Write-Host "Installo le dipendenze ($requirements)..."
& $python -m pip install --disable-pip-version-check -r (Join-Path $repo $requirements)
Assert-LastExit 'installazione dipendenze'

if (-not $NoShortcut) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($ShortcutPath)
    $link.TargetPath = $pythonw
    $link.Arguments = '-m kite_admissions'
    $link.WorkingDirectory = $repo
    $link.Description = 'Avvia KITE Admissions (uso locale su questo PC)'
    $link.IconLocation = "$pythonw,0"
    $link.Save()
    Write-Host "Collegamento creato: $ShortcutPath"
}

Write-Host 'Installazione completata. Avvia il programma con il collegamento "Avvia KITE Admissions".'
