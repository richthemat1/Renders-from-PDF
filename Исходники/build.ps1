param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$taskBuild = Join-Path $PSScriptRoot '.build'
$taskVenv = Join-Path $taskBuild 'venv'
if (-not (Test-Path -LiteralPath $taskVenv)) {
    & $Python -X utf8 -m venv $taskVenv
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать среду сборки.' }
}
$taskBuildPython = Join-Path $taskVenv 'Scripts\python.exe'
$taskSource = Get-Content -Raw -Encoding utf8 -LiteralPath (Join-Path $PSScriptRoot 'photo_export.py')
if ($taskSource -notmatch "(?m)^VERSION = '([0-9]+(?:\.[0-9]+){1,2})'") { throw 'Не найдена версия программы.' }
$taskVersion = $Matches[1]
$taskFrontend = Join-Path $PSScriptRoot 'frontend'
Push-Location $taskFrontend
try {
    & npm.cmd ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости интерфейса. Для сборки нужен Node.js 24 LTS.' }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось собрать интерфейс React.' }
    & npm.cmd test
    if ($LASTEXITCODE -ne 0) { throw 'Проверки редактора не пройдены.' }
} finally { Pop-Location }
& $taskBuildPython -X utf8 -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot 'requirements.txt') 'pyinstaller==6.22.3'
if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости сборки.' }
& $taskBuildPython -X utf8 -m PyInstaller --noconfirm --onefile --console --hide-console hide-early --noupx --name "PDF-фото $taskVersion" --add-data ((Join-Path $PSScriptRoot 'assets') + ';assets') --add-data ((Join-Path $taskFrontend 'dist') + ';frontend/dist') --collect-all webview --hidden-import pythonnet --hidden-import clr_loader --distpath (Split-Path $PSScriptRoot -Parent) --workpath (Join-Path $taskBuild 'work') --specpath $taskBuild (Join-Path $PSScriptRoot 'photo_app.py')
if ($LASTEXITCODE -ne 0) { throw 'Не удалось собрать EXE.' }
