; Установщик Windows для AITU Rubezh. Собирает installer/build.py, руками:
;   ISCC /DVersion=0.9.0 installer\setup.iss
;
; Ставится в папку пользователя, без прав администратора: туда же ложатся
; сессии, ключи и собранный дашборд (подпапка rubezh\), профили дайджеста
; (tg-digest\). Раскладка та же, что в репозитории, поэтому код не различает,
; откуда его запустили.
;
; Что делает сам установщик, чтобы мастеру setup осталось только войти:
;   - ярлыки: рабочий стол и меню Пуск (rubezh.exe open, окно свёрнуто)
;   - схема rubezh:// в HKCU — кнопка «Войти» на дашборде
;   - на последнем экране предлагает запустить мастер
; При удалении снимает задачи планировщика и спрашивает, стирать ли данные.

#ifndef Version
  #define Version "0.0.0"
#endif
#define AppName "AITU Rubezh"
#define Dist "dist\AITU Rubezh"

[Setup]
AppId={{6C1D7A0E-4B2F-4E3A-9C58-2A7F1B0D9E11}
AppName={#AppName}
AppVersion={#Version}
AppVerName={#AppName} {#Version}
AppPublisher=AITU Rubezh
AppPublisherURL=https://github.com/dias-2008/AITU_Rubezh
AppSupportURL=https://github.com/dias-2008/AITU_Rubezh/issues
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=AITU-Rubezh-Setup-{#Version}
SetupIconFile=..\rubezh\icon.ico
UninstallDisplayIcon={app}\rubezh.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
ShowLanguageDialog=auto

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
ru.DashboardDesc=Дашборд: дедлайны, оценки, расписание, рубежи
ru.DigestDesc=tg-digest: выжимка из университетских Telegram-групп
ru.TypeFull=Дашборд и дайджест Telegram
ru.TypeCompact=Только дашборд
ru.TypeCustom=Выборочно
ru.RunSetup=Настроить сейчас: войти в Moodle и портал (5 минут)
ru.RunDigestSetup=Настроить tg-digest (ключи Telegram, группы)
ru.SetupShortcut=Настройка {#AppName}
ru.DigestShortcut=Настройка tg-digest
ru.OpenDesc=Пересобрать дашборд и открыть в браузере
ru.GroupData=Я из группы CS-2606: положить расписание и силлабусы группы
ru.DeleteData=Удалить и данные — сессии Moodle и портала, ключи, собранный дашборд, профили Telegram?%n%nЭто полный доступ к твоим университетским аккаунтам. Если ставишь заново, можно оставить.
en.DashboardDesc=Dashboard: deadlines, grades, timetable, midterm thresholds
en.DigestDesc=tg-digest: summary of university Telegram groups
en.TypeFull=Dashboard and Telegram digest
en.TypeCompact=Dashboard only
en.TypeCustom=Custom
en.RunSetup=Set up now: sign in to Moodle and the portal (5 minutes)
en.RunDigestSetup=Set up tg-digest (Telegram keys, groups)
en.SetupShortcut={#AppName} setup
en.DigestShortcut=tg-digest setup
en.OpenDesc=Rebuild the dashboard and open it in the browser
en.GroupData=I am in group CS-2606: install the group's timetable and syllabi
en.DeleteData=Also delete data — Moodle and portal sessions, keys, the built dashboard, Telegram profiles?%n%nThese give full access to your university accounts. Keep them if you are reinstalling.

[Types]
Name: "full"; Description: "{cm:TypeFull}"
Name: "compact"; Description: "{cm:TypeCompact}"
Name: "custom"; Description: "{cm:TypeCustom}"; Flags: iscustom

[Components]
Name: "rubezh"; Description: "{cm:DashboardDesc}"; Types: full compact custom; Flags: fixed
Name: "digest"; Description: "{cm:DigestDesc}"; Types: full

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "cs2606"; Description: "{cm:GroupData}"; Flags: unchecked

[Files]
Source: "{#Dist}\*"; DestDir: "{app}"; Excludes: "digest.exe,digestw.exe"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: rubezh
Source: "{#Dist}\digest.exe"; DestDir: "{app}"; Flags: ignoreversion; Components: digest
Source: "{#Dist}\digestw.exe"; DestDir: "{app}"; Flags: ignoreversion; Components: digest
; Данные группы из groups/<группа>/ — только по галочке и только если файлов ещё нет:
; правки студента в syllabus.json обновление трогать не должно.
Source: "..\groups\CS-2606\*.json"; DestDir: "{app}\rubezh"; Flags: onlyifdoesntexist uninsneveruninstall; Tasks: cs2606

[Dirs]
Name: "{app}\rubezh"
Name: "{app}\tg-digest"; Components: digest

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\rubezh.exe"; Parameters: "open"; WorkingDir: "{app}\rubezh"; IconFilename: "{app}\icon.ico"; Comment: "{cm:OpenDesc}"; Flags: runminimized
Name: "{group}\{cm:SetupShortcut}"; Filename: "{app}\rubezh.exe"; Parameters: "setup"; WorkingDir: "{app}\rubezh"; IconFilename: "{app}\icon.ico"
Name: "{group}\{cm:DigestShortcut}"; Filename: "{app}\digest.exe"; Parameters: "setup"; WorkingDir: "{app}\tg-digest"; IconFilename: "{app}\icon.ico"; Components: digest
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\rubezh.exe"; Parameters: "open"; WorkingDir: "{app}\rubezh"; IconFilename: "{app}\icon.ico"; Comment: "{cm:OpenDesc}"; Tasks: desktopicon; Flags: runminimized

[Registry]
; Схема rubezh:// — кнопка «Войти» на дашборде. Только HKCU, без администратора.
Root: HKCU; Subkey: "Software\Classes\rubezh"; ValueType: string; ValueName: ""; ValueData: "URL:AITU Rubezh"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\rubezh"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\rubezh\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\icon.ico"
Root: HKCU; Subkey: "Software\Classes\rubezh\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\rubezhw.exe"" protocol ""%1"""

[Run]
Filename: "{app}\rubezh.exe"; Parameters: "setup"; WorkingDir: "{app}\rubezh"; Description: "{cm:RunSetup}"; Flags: postinstall nowait skipifsilent
Filename: "{app}\digest.exe"; Parameters: "setup"; WorkingDir: "{app}\tg-digest"; Description: "{cm:RunDigestSetup}"; Flags: postinstall nowait skipifsilent unchecked; Components: digest

[UninstallRun]
; Задачи планировщика ставит мастер setup, а не установщик, но снять их при
; удалении обязан установщик — иначе раз в час будет запускаться пустота.
Filename: "schtasks"; Parameters: "/delete /tn ""AITU Rubezh"" /f"; Flags: runhidden; RunOnceId: "TaskRubezh"
Filename: "schtasks"; Parameters: "/delete /tn ""TG Digest"" /f"; Flags: runhidden; RunOnceId: "TaskDigest"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if MsgBox(CustomMessage('DeleteData'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    begin
      DelTree(ExpandConstant('{app}\rubezh'), True, True, True);
      DelTree(ExpandConstant('{app}\tg-digest'), True, True, True);
      RemoveDir(ExpandConstant('{app}'));
    end;
  end;
end;
