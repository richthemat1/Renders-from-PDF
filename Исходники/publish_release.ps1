param([string]$Python = 'python', [string]$NsisCompiler)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
Push-Location $taskRoot
try {
    $taskLogin = & gh api user --jq .login
    if ($LASTEXITCODE -ne 0 -or $taskLogin.Trim() -ne 'richthemat1') { throw 'Нужна авторизация GitHub CLI как richthemat1.' }
    $taskStatus = & git status --porcelain
    if ($LASTEXITCODE -ne 0 -or $taskStatus) { throw 'Сначала сохраните изменения в Git; рабочая копия должна быть чистой.' }
    $taskBranch = & git branch --show-current
    if ($taskBranch.Trim() -ne 'main') { throw 'Выпуск выполняется из ветки main.' }
    & (Join-Path $PSScriptRoot 'build_release.ps1') -Python $Python -NsisCompiler $NsisCompiler
    $taskSource = Get-Content -Raw -Encoding utf8 -LiteralPath (Join-Path $PSScriptRoot 'photo_export.py')
    if ($taskSource -notmatch "(?m)^VERSION = '([0-9]+(?:\.[0-9]+){1,2})'") { throw 'Не найдена версия программы.' }
    $taskVersion = $Matches[1]
    $taskRelease = Join-Path $taskRoot "Релизы\$taskVersion"
    $taskTag = "v$taskVersion"
    & git -c user.name=richthemat1 -c user.email=215496850+richthemat1@users.noreply.github.com tag -a $taskTag -F (Join-Path $taskRelease 'Release-notes.md')
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать тег; уже опубликованные выпуски не заменяются.' }
    & git push --atomic origin main $taskTag
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось отправить коммит и тег.' }
    $taskAssets = @('PDF-Photos-Setup.exe','PDF-Photos-Portable.zip','PDF-Photos.exe','SHA256SUMS.txt') | ForEach-Object { Join-Path $taskRelease $_ }
    & gh release create $taskTag --repo richthemat1/Renders-from-PDF --verify-tag --title "Фото из PDF $taskVersion" --notes-file (Join-Path $taskRelease 'Release-notes.md') --latest @taskAssets
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось опубликовать релиз.' }
} finally { Pop-Location }
