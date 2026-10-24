ORACLE EPM TASK MANAGER - SCHEDULE DEPLOYER  (version 1.0.0)
=============================================================

HOW TO START
------------
1. Unzip the whole folder "EPMTaskDeployer" anywhere you can write to (for example Documents or Desktop).
   Do not run the program from inside the zip file.
2. Double-click EPMTaskDeployer.exe.
   No installation and no administrator rights are needed. Python is not required.
3. If Windows SmartScreen shows "Windows protected your PC": click "More info", then "Run anyway".
   This happens because the program is not digitally signed by a public publisher. Only run the copy
   that was issued by Finance IT (check the SHA-256 value that Finance IT published with the zip).

Keep the program folder together: EPMTaskDeployer.exe needs the files in its _internal folder.

WHERE YOUR DATA IS STORED
-------------------------
  Environments and settings   %APPDATA%\EPMTaskDeployer\environments.json
  Passwords                   Windows Credential Manager (entries named EPMTaskDeployer:<environment>).
                              Passwords are never written to a file.
  Logs (passwords masked)     %APPDATA%\EPMTaskDeployer\logs\app.log
  Deployment history          %APPDATA%\EPMTaskDeployer\ledger\
  Self-check result           %APPDATA%\EPMTaskDeployer\logs\self_check.txt

Paste "%APPDATA%\EPMTaskDeployer" into the Windows Explorer address bar to open it.

ORACLE CONNECTION
-----------------
The program contacts Oracle EPM only when you click one of these buttons:
  - Test Connection
  - Check in Oracle
  - Deploy Schedules -> Confirm and Deploy
It never deletes or opens schedules. Schedules are created only after you confirm.

HELP
----
Open the user guide from the Help menu in the program, or open this file in your browser:
  _internal\docs\user-guide\USER_GUIDE.html
(If that file is missing, ask Finance IT for the current guide.)

SUPPORT
-------
If something goes wrong, send Finance IT the file %APPDATA%\EPMTaskDeployer\logs\app.log
(passwords are masked in it) together with a short description of what you clicked.

---------------------------------------------------------------------------------------------

КРАТКО (RU)
-----------
Запуск: распакуйте папку EPMTaskDeployer (не запускайте из архива) и дважды щёлкните EPMTaskDeployer.exe.
Установка и права администратора не нужны, Python не нужен.
Если SmartScreen показывает предупреждение: "Подробнее" -> "Выполнить в любом случае".

Данные: %APPDATA%\EPMTaskDeployer (настройки и журналы). Пароли хранятся в Диспетчере учётных данных Windows,
в файлах программы не сохраняются.
Oracle программа вызывает только по нажатию кнопок Test Connection, Check in Oracle и
Deploy Schedules -> Confirm and Deploy. Расписания не удаляются и не открываются.
Справка: меню Help в программе или файл _internal\docs\user-guide\USER_GUIDE.html.
