# Oracle EPM Task Manager – Schedule Deployer

Windows desktop app that creates monthly Oracle EPM Task Manager schedules from one template.
Design and Oracle research: [docs/DESIGN.md](docs/DESIGN.md).

**User guide (with screenshots):** https://pushkaryovvd.github.io/oracle-epm-task-manager/
(source: [docs/user-guide/USER_GUIDE.md](docs/user-guide/USER_GUIDE.md)).

**Current stage: 4 – Oracle integration (REST, basic authentication).** Not yet verified against a real pod:
run [docs/TEST_POD_CHECKLIST.md](docs/TEST_POD_CHECKLIST.md) on a **test** environment first.

Oracle is contacted only when you click **Test Connection**, **Check in Oracle** or **Deploy Schedules →
Confirm and Deploy**. Schedules are created only after the confirmation; nothing is ever deleted or opened.

Try the UI against a simulated Oracle (no network): `.venv\Scripts\python.exe tools\fake_oracle.py`.

## Run from source

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m app.main
```

Try it with `samples\Monthly_Close_Template.xlsx` (regenerate with `python tools\make_sample_template.py`).

## Where data is stored

| What | Where |
|---|---|
| Environments (name, service type, URL, user name, defaults) | `%APPDATA%\EPMTaskDeployer\environments.json` |
| Passwords | Windows Credential Manager, entry `EPMTaskDeployer:<environment id>` – never on disk |
| Logs (secrets masked) | `%APPDATA%\EPMTaskDeployer\logs\app.log` |
| Deployment history (prevents duplicates on retry) | `%APPDATA%\EPMTaskDeployer\ledger\<environment id>.json` |

Set `EPMTD_HOME` to use another folder (used by tests).

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Build the Windows executable

Target: Windows 10/11, standard (non-admin) user accounts, no Python on the PC. Output is a PyInstaller
**one-folder**, **windowed** build (`dist\EPMTaskDeployer\EPMTaskDeployer.exe`) plus a zip for distribution.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

`build.ps1` (Windows PowerShell 5.1 compatible) does, in order:

1. creates `.venv` (Python 3.12) if missing and installs `requirements.txt` + `requirements-dev.txt`;
2. runs `pytest` – a failing test fails the build (`-SkipTests` skips it);
3. generates `assets\app.ico` with `tools\make_icon.py` if missing;
4. runs `tools\build_user_guide.py` if it exists; without `docs\user-guide\USER_GUIDE.html` the build continues with a warning;
5. removes `build\` and `dist\`, runs PyInstaller with `EPMTaskDeployer.spec` (UPX off, version resource from `version_info.txt`);
6. optional signing: `-SignCertThumbprint <sha1>` signs the exe with `signtool` (SHA-256, timestamp from `-TimestampUrl`);
   without the parameter this step is skipped;
7. copies `packaging\READ_ME_FIRST.txt` into the folder as `READ ME FIRST.txt`;
8. runs `EPMTaskDeployer.exe --self-check` and prints the report (see below); a failed self-check fails the build;
9. creates `dist\EPMTaskDeployer-1.0.0-win64.zip` and prints the SHA-256 of the exe and the zip.

Everything is installed into `.venv`; nothing is installed system-wide.

**Self-check.** `EPMTaskDeployer.exe --self-check` opens no window. It verifies the configuration files, that the
keyring backend is the Windows Credential Manager one (no credential is read or written), that customtkinter and its
themes load, and that the user guide and icon are present (warnings only). The report goes to
`%APPDATA%\EPMTaskDeployer\logs\self_check.txt` (or `EPMTD_HOME\logs\` when set) and the exit code is 0 or 1.

**Code signing and SmartScreen.** An unsigned exe, or one signed with a certificate that Windows has no reputation for,
can show "Windows protected your PC". Users then click "More info" -> "Run anyway". A publicly trusted code-signing
certificate reduces the warning; antivirus heuristics can still flag PyInstaller bundles, so compare the published
SHA-256 values and report false positives to the AV vendor. To sign:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1 -SignCertThumbprint <THUMBPRINT>
```

**Files involved:** `EPMTaskDeployer.spec`, `launcher.py`, `version_info.txt`, `requirements-dev.txt`, `build.ps1`,
`packaging\READ_ME_FIRST.txt`, `tools\make_icon.py`, `assets\app.ico`, `app\self_check.py`.

## Configuration (`config/`)

- `service_types.json` – Oracle services and what the app can do with each (ARCS has no Task Manager).
- `period_mappings/*.json` – calendar month → Oracle Year/Period members. Add a file for a custom fiscal calendar.
- `tm_columns.json` – recognised Excel column headers (provisional until checked against a real Task Manager export).
- `naming_rules.json` – schedule name limits (provisional until confirmed on a test pod; warnings only).
- `oracle_messages.json` – Oracle error texts meaning "schedule already exists" / "template not found" (provisional).
