#define AppName "MailFlow Archivist"
#define AppPublisher "Balz Metal Sa"
#define AppExeName "MailFlow-Archivist.exe"

#ifndef AppVersion
#define AppVersion "0.0.0"
#endif

#ifndef SourceDir
#define SourceDir "..\..\dist\MailFlow-Archivist"
#endif

#ifndef OutputDir
#define OutputDir "."
#endif

#ifndef OutputBaseFilename
#define OutputBaseFilename "MailFlow-Archivist-Setup"
#endif

[Setup]
AppId={{6A1D221D-41A1-4B4F-8D6D-8A8B1F82B214}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://github.com/LionelMds/MailFlow-Archivist
AppSupportURL=https://github.com/LionelMds/MailFlow-Archivist/issues
AppUpdatesURL=https://github.com/LionelMds/MailFlow-Archivist/releases/latest
DefaultDirName={localappdata}\Programs\MailFlow Archivist
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#AppExeName}
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Creer une icone sur le Bureau"; GroupDescription: "Raccourcis:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Lancer {#AppName}"; Flags: nowait postinstall skipifsilent
