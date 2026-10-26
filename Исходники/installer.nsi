# -*- coding: utf-8 -*-
Unicode True
!include "MUI2.nsh"
!include "x64.nsh"
!include "WinVer.nsh"

!ifndef APP_VERSION
  !error "APP_VERSION is required"
!endif
!ifndef SOURCE_DIR
  !error "SOURCE_DIR is required"
!endif
!ifndef EXE_FILE
  !error "EXE_FILE is required"
!endif
!ifndef OUT_FILE
  !error "OUT_FILE is required"
!endif

!define APP_NAME "Фото из PDF"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\PDF-Photos"

Name "${APP_NAME} ${APP_VERSION}"
OutFile "${OUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\PDF-Photos"
RequestExecutionLevel user
ManifestDPIAware True
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show
VIProductVersion "${APP_FILE_VERSION}"
VIAddVersionKey /LANG=1049 "ProductName" "${APP_NAME}"
VIAddVersionKey /LANG=1049 "ProductVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=1049 "FileVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=1049 "FileDescription" "Установка программы «Фото из PDF»"
VIAddVersionKey /LANG=1049 "LegalCopyright" "richthemat1"

!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\PDF-Photos.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Запустить «Фото из PDF»"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Russian"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "Для этой версии нужна 64-разрядная Windows 10 или 11."
    Abort
  ${EndIf}
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_ICONSTOP "Для этой версии нужна Windows 10 или 11."
    Abort
  ${EndIf}
  SetShellVarContext current
  SetRegView 64
  ReadRegStr $0 HKCU "${UNINSTALL_KEY}" "InstallLocation"
  ${If} $0 != ""
    StrCpy $INSTDIR $0
  ${EndIf}
FunctionEnd

Section "Фото из PDF (обязательно)" Application
  SectionIn RO
  SetOutPath "$INSTDIR"
  SetOverwrite on
  File /oname=PDF-Photos.exe "${EXE_FILE}"
  File "${SOURCE_DIR}\Как пользоваться.txt"
  File "${SOURCE_DIR}\Лицензии компонентов.txt"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\Фото из PDF"
  CreateShortcut "$SMPROGRAMS\Фото из PDF\Фото из PDF.lnk" "$INSTDIR\PDF-Photos.exe"
  CreateShortcut "$SMPROGRAMS\Фото из PDF\Совместимый интерфейс.lnk" "$INSTDIR\PDF-Photos.exe" "--legacy-ui"
  CreateShortcut "$SMPROGRAMS\Фото из PDF\Как пользоваться.lnk" "$INSTDIR\Как пользоваться.txt"
  WriteINIStr "$INSTDIR\Обновления.url" "InternetShortcut" "URL" "https://github.com/richthemat1/Renders-from-PDF/releases/latest"
  CreateShortcut "$SMPROGRAMS\Фото из PDF\Обновления.lnk" "$INSTDIR\Обновления.url"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "richthemat1"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\PDF-Photos.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '$\"$INSTDIR\Uninstall.exe$\" /S'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "URLInfoAbout" "https://github.com/richthemat1/Renders-from-PDF"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "URLUpdateInfo" "https://github.com/richthemat1/Renders-from-PDF/releases/latest"
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
  ReadRegStr $1 HKCU "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${If} $1 == ""
    SetRegView 32
    ReadRegStr $1 HKLM "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
    SetRegView 64
  ${EndIf}
  ${If} $1 == ""
    IfSilent +2
    MessageBox MB_ICONINFORMATION "Для нового интерфейса нужен Microsoft Edge WebView2 Runtime. Официальный установщик Microsoft доступен на developer.microsoft.com/microsoft-edge/webview2/. Без Runtime используйте ярлык «Совместимый интерфейс» в меню «Пуск»."
  ${EndIf}
SectionEnd

Section /o "Ярлык на рабочем столе" DesktopShortcut
  CreateShortcut "$DESKTOP\Фото из PDF.lnk" "$INSTDIR\PDF-Photos.exe"
SectionEnd

Function un.onInit
  SetShellVarContext current
  SetRegView 64
FunctionEnd

Section "Uninstall"
  Delete "$INSTDIR\PDF-Photos.exe"
  Delete "$INSTDIR\Как пользоваться.txt"
  Delete "$INSTDIR\Лицензии компонентов.txt"
  Delete "$INSTDIR\Обновления.url"
  Delete "$INSTDIR\Uninstall.exe"
  Delete "$SMPROGRAMS\Фото из PDF\Фото из PDF.lnk"
  Delete "$SMPROGRAMS\Фото из PDF\Совместимый интерфейс.lnk"
  Delete "$SMPROGRAMS\Фото из PDF\Как пользоваться.lnk"
  Delete "$SMPROGRAMS\Фото из PDF\Обновления.lnk"
  RMDir "$SMPROGRAMS\Фото из PDF"
  Delete "$DESKTOP\Фото из PDF.lnk"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  RMDir "$INSTDIR"
SectionEnd
