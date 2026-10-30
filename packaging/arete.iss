; Inno Setup script: turns dist\Arete (from packaging\arete.spec) into dist\Arete-Setup.exe.
; Installs for the current user only (no admin prompt) into %LOCALAPPDATA%\Programs\Arete,
; with a Start menu shortcut, an optional desktop shortcut and an uninstaller.
; The user's data (%APPDATA%\Arete) is never touched by installing or uninstalling.
; Build (on Windows):  set ARETE_VERSION=1.1.0  then  iscc packaging\arete.iss

#define AppVersion GetEnv("ARETE_VERSION")
#if AppVersion == ""
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6E7B3C5A-4F0E-4D2B-9A51-3C8E2F1D7A44}
AppName=Arete
AppVersion={#AppVersion}
AppVerName=Arete {#AppVersion}
AppPublisher=Arete
AppPublisherURL=https://github.com/spencertomj1-hue/arete-app
DefaultDirName={localappdata}\Programs\Arete
DefaultGroupName=Arete
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Arete-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\Arete.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\Arete\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Arete"; Filename: "{app}\Arete.exe"
Name: "{autodesktop}\Arete"; Filename: "{app}\Arete.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Arete.exe"; Description: "Open Arete"; Flags: nowait postinstall skipifsilent
