@echo off
chcp 65001 >nul
if exist "%~dp0PDF-Photos.exe" (
  start "" "%~dp0PDF-Photos.exe" %*
) else (
  start "" "%~dp0PDF-фото 3.28.exe" %*
)
