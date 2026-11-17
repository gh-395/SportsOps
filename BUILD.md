# Building the SportsOps Windows application

SportsOps is a native PySide6 application. The recommended distribution is a
PyInstaller one-folder bundle; `SportsOps.exe` must remain beside the generated
`_internal` folder and other bundle files. The app creates its mutable SQLite
database under `%LOCALAPPDATA%\SportsOps`, not in the bundle.

## Python and dependency compatibility

Use 64-bit CPython on Windows. Python 3.12 is the preferred stable build
interpreter. The build configuration pins PyInstaller 6.22.3, whose published
support list includes CPython 3.8–3.15 and PySide6 collection. PySide6 and
SQLAlchemy versions are resolved from `requirements.txt`.

The Phase 5B environment had CPython 3.15.2, PySide6 6.12.0, and SQLAlchemy
2.1.8; CPython 3.12 was not installed. The current PyInstaller support list
includes 3.15, so a build can be attempted with the existing project virtual
environment. Prefer a separate Python 3.12 build environment for long-term
reproducibility; do not replace or reconfigure an unrelated Python installation.

### Build with an available Python 3.12

Install the official 64-bit Python 3.12 release for the current Windows user.
In its installer, use **Customize installation**, leave **Install for all
users** and **Add Python to PATH** unchecked, and allow the Python Launcher
registration. This keeps Python out of system-wide PATH and avoids changing
other Python environments.

From PowerShell in the project directory, create and use a project-local
environment explicitly:

```powershell
py -0p
py -3.12 -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install --upgrade pip
.\.venv-build\Scripts\python.exe -m pip install -r requirements-build.txt
```

If `py -3.12` is not recognized after installing the official per-user
installer, reopen PowerShell and check `py -0p`. The launcher selects 3.12
without changing the active interpreter or PATH for other projects.

### Build using the current project environment

When Python 3.12 is not installed, the current `.venv` is isolated to SportsOps
and can be used if its Python and dependencies are supported by the pinned
PyInstaller release:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
```

This installs build tools into the project virtual environment only; it does
not install PostgreSQL or alter system Python. PyInstaller 6.22.3 advertises
support for Python 3.15, but a successful build still requires a real build and
runtime test on that interpreter.

## Create the one-folder bundle

Build from the project root:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm sportsops.spec
```

For the isolated Python 3.12 environment, replace `.venv` with `.venv-build`.
The expected output is:

```text
dist\SportsOps\SportsOps.exe
dist\SportsOps\_internal\
```

PyInstaller's PySide6 hooks collect the required Qt libraries and the Windows
platform plugin. The spec includes SportsOps submodules and SQLAlchemy's SQLite
dialect, plus the OpenFootball manifest and CSV files. No mutable database is
shipped; Python seed modules initialize synthetic demo records and import
8,676 historical results into the user's data directory at first run. SportsOps
does not need a PostgreSQL installation, paid service, or internet connection.

Do not copy only `SportsOps.exe`; distribute the entire `dist\SportsOps`
directory, for example as a ZIP that retains this folder structure. Users may
extract it to a writable folder such as `%LOCALAPPDATA%\Programs\SportsOps`.
An installer is not required for this build.

## First launch, upgrades, and demo reset

On first launch the app creates `%LOCALAPPDATA%\SportsOps\sportsops_demo.sqlite3`
and initializes synthetic demo records, then imports the bundled historical
CSV files into separate historical tables. Repeated launches are idempotent.
Updates to the application bundle keep using that same user-data database and
run additive schema migrations; the bundle directory is not used for database
writes. Back up this folder before manual database maintenance.

**File > Create fresh demo profile...** creates a new, separately named SQLite
database and selects it for the next launch. It does not delete or modify the
current database; imported historical rows and other user records remain in
the previous database file. The fresh profile is synthetic demo data only.
This option is disabled when `SPORTSOPS_DATABASE_URL` configures a custom
database. Existing profile files and the active-profile marker are in
`%LOCALAPPDATA%\SportsOps`.

## Validation before sharing a build

Run tests and SQL validation before packaging:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts\validate_sql.py
```

After building, launch `dist\SportsOps\SportsOps.exe`, verify the UI and
both workspaces, including Historical Data pagination and Markets risk/audit
panels, and test a copy of the complete bundle from outside the source
directory. Verify that the SQLite database contains 8,676 historical matches.
Use a disposable user-data directory or test Windows account so the smoke test
does not modify a personal SportsOps database. Confirm first-run database
creation and the persistent path before sharing the bundle.

## SmartScreen and code signing

An unsigned executable may trigger Microsoft Defender SmartScreen or another
security product. Do not disable Defender/SmartScreen, bypass a warning, or
instruct recipients to choose **Run anyway**. Distribute only a build from a
trusted source, publish a cryptographic checksum through a trusted channel, and
use a reputable code-signing certificate if a signed public release is needed.
Signing and reputation are not included in this development build.

## References

- [PyInstaller supported platforms and Python versions](https://pyinstaller.org/en/stable/requirements.html)
- [PyInstaller usage and one-folder mode](https://pyinstaller.org/en/stable/usage.html)
- [Qt for Python deployment documentation](https://doc.qt.io/qtforpython-6/deployment/index.html)
