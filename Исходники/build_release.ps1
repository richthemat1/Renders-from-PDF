param(
    [string]$Python = 'python',
    [string]$NsisCompiler,
    [switch]$SkipTests,
    [switch]$SkipBuild
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskSource = Get-Content -Raw -Encoding utf8 -LiteralPath (Join-Path $PSScriptRoot 'photo_export.py')
if ($taskSource -notmatch "(?m)^VERSION = '([0-9]+(?:\.[0-9]+){1,2})'") { throw 'Не найдена версия программы.' }
$taskVersion = $Matches[1]
$taskChangelog = Get-Content -Raw -Encoding utf8 -LiteralPath (Join-Path $taskRoot 'CHANGELOG.md')
$taskPattern = '(?ms)^## ' + [regex]::Escape($taskVersion) + ' — [^\r\n]+\r?\n(.*?)(?=^## |\z)'
if ($taskChangelog -notmatch $taskPattern -or -not $Matches[1].Trim()) {
    throw "Перед сборкой добавьте в CHANGELOG.md описание версии $taskVersion для пользователей."
}
$taskNotes = $Matches[1].Trim()
$taskBuildPython = Join-Path $PSScriptRoot '.build\venv\Scripts\python.exe'
if (-not $SkipBuild) { & (Join-Path $PSScriptRoot 'build.ps1') -Python $Python }
if (-not (Test-Path -LiteralPath $taskBuildPython)) { throw 'Сначала выполните build.ps1.' }
if (-not $SkipTests) {
    & $taskBuildPython -X utf8 -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot 'requirements-dev.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости проверок.' }
    & $taskBuildPython -X utf8 -m playwright install chromium --only-shell
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось подготовить браузер проверок.' }
    Push-Location $PSScriptRoot
    try {
        & $taskBuildPython -X utf8 -B -m unittest -v
        if ($LASTEXITCODE -ne 0) { throw 'Проверки не пройдены; выпуск остановлен.' }
    } finally { Pop-Location }
}
if (-not $NsisCompiler) {
    $taskCompiler = Get-Command makensis.exe -ErrorAction SilentlyContinue
    if ($taskCompiler) { $NsisCompiler = $taskCompiler.Source }
    else {
        $taskCandidates = @(
            (Join-Path $PSScriptRoot '.build\compiler\makensis.exe'),
            'C:\Program Files (x86)\NSIS\makensis.exe',
            'C:\Program Files\NSIS\makensis.exe'
        )
        $NsisCompiler = $taskCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    }
}
if (-not $NsisCompiler -or -not (Test-Path -LiteralPath $NsisCompiler)) {
    throw 'Нужен компилятор NSIS: укажите путь к makensis.exe через -NsisCompiler.'
}
$taskExecutable = Join-Path $taskRoot "PDF-фото $taskVersion.exe"
if (-not (Test-Path -LiteralPath $taskExecutable)) { throw "Не найден $taskExecutable" }
$taskRelease = Join-Path $taskRoot "Релизы\$taskVersion"
$taskPackage = Join-Path $PSScriptRoot ('.build\package-' + $taskVersion + '-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $taskRelease,$taskPackage -Force | Out-Null
$taskNames = @('Как пользоваться.txt','Лицензии компонентов.txt','Запустить.cmd')
Copy-Item -LiteralPath $taskExecutable -Destination (Join-Path $taskRelease 'PDF-Photos.exe') -Force
Copy-Item -LiteralPath $taskExecutable -Destination (Join-Path $taskPackage 'PDF-Photos.exe') -Force
foreach ($taskName in $taskNames) {
    Copy-Item -LiteralPath (Join-Path $taskRoot $taskName) -Destination (Join-Path $taskPackage $taskName) -Force
}
$taskZip = Join-Path $taskRelease 'PDF-Photos-Portable.zip'
if (Test-Path -LiteralPath $taskZip) { Remove-Item -LiteralPath $taskZip }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($taskPackage, $taskZip)
$taskSetup = Join-Path $taskRelease 'PDF-Photos-Setup.exe'
$taskVersionParts = @($taskVersion.Split('.'))
while ($taskVersionParts.Count -lt 4) { $taskVersionParts += '0' }
$taskFileVersion = $taskVersionParts -join '.'
& $NsisCompiler /V2 "/DAPP_VERSION=$taskVersion" "/DAPP_FILE_VERSION=$taskFileVersion" "/DSOURCE_DIR=$taskRoot" "/DEXE_FILE=$taskExecutable" "/DOUT_FILE=$taskSetup" (Join-Path $PSScriptRoot 'installer.nsi')
if ($LASTEXITCODE -ne 0) { throw 'Не удалось собрать установщик.' }
[IO.File]::WriteAllText((Join-Path $taskRelease 'Release-notes.md'), $taskNotes + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
$taskAssets = @('PDF-Photos-Setup.exe','PDF-Photos-Portable.zip','PDF-Photos.exe')
$taskChecksums = foreach ($taskName in $taskAssets) {
    $taskHash = (Get-FileHash -LiteralPath (Join-Path $taskRelease $taskName) -Algorithm SHA256).Hash.ToLowerInvariant()
    "$taskHash  $taskName"
}
$taskChecksums | Set-Content -LiteralPath (Join-Path $taskRelease 'SHA256SUMS.txt') -Encoding ascii
Write-Output "Готов выпуск $taskVersion`: $taskRelease"
