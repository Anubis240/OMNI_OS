; ============================================================
;  S.E.R.A.P.H installer — edit the #define block to rebrand.
;  Build: open this file in Inno Setup Compiler and press F9,
;  or run:  iscc installer.iss
; ============================================================

#define AppName "S.E.R.A.P.H"
#define AppVersion "1.1.0"
#define AppPublisher "Kondux"
#define AppURL "https://kondux.io"
#define AppExeName "S.E.R.A.P.H.exe"

[Setup]
AppId={{6C2F9E4A-8B41-4C7E-9D2A-SERAPH000001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
; Per-user install, not Program Files: the app writes its config
; (API keys), logs, memory, certs, and trader ledger directly next to the
; exe (see main.py's BASE_DIR / get_base_dir()) on every run, not just
; during setup. An admin-elevated, Program-Files install only works for
; the installer's own post-install launch (which inherits the elevation);
; every later double-click from the Start Menu / desktop runs as the
; plain user and hits PermissionError writing to Program Files. Installing
; per-user sidesteps this entirely — matches how VS Code, Slack, etc.
; default their Windows installers.
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
OutputDir=output
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern dark
SetupIconFile=..\assets\icon.ico
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; The built PyInstaller onedir app (output of: pyinstaller seraph.spec).
; Excludes config\* deliberately — that's runtime-generated state (Gemini
; key, Claude Code CLI paths, self-signed cert, trader ledger), created
; fresh by the app itself on first launch. Bundling whatever happens to be
; sitting in the dev machine's dist\ folder at build time would leak the
; developer's own API key and personal file paths into every install.
Source: "..\dist\S.E.R.A.P.H\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion; Excludes: "config\*"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; \
  Flags: nowait postinstall skipifsilent

[Code]
// ---------------------------------------------------------------
// Colors, panels, and contrast are handled by Inno Setup's own
// built-in "dark" custom style (WizardStyle=modern dark, above).
// This just customizes wording and adds one cyan accent touch.
// NOTE: this Font.Color property still uses legacy TColor order
// ($BBGGRR), unlike the new WizardBackColor-style directives.
// ---------------------------------------------------------------
procedure InitializeWizard;
begin
  WizardForm.PageNameLabel.Font.Color := $FFC800; // primary accent #00C8FF (this app's HUD blue)

  WizardForm.WelcomeLabel1.Caption := 'Welcome to the S.E.R.A.P.H Setup Wizard';
  WizardForm.WelcomeLabel2.Caption :=
    'This will install S.E.R.A.P.H on your computer — a voice AI assistant ' +
    'with a custom wake word, real-time conversation, and an optional built-in ' +
    'crypto trading panel.'#13#10#13#10 +
    'You''ll need a free Gemini API key on first launch — S.E.R.A.P.H will ' +
    'prompt for it. Click Next to continue.';
  WizardForm.FinishedHeadingLabel.Caption := 'S.E.R.A.P.H is ready';
  WizardForm.FinishedLabel.Caption :=
    'Setup has finished installing S.E.R.A.P.H on your computer. ' +
    'On first launch, enter your Gemini API key when prompted, then say ' +
    '"Seraph" to start talking.';
end;
