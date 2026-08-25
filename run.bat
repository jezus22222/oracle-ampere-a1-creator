@echo off
REM Oracle Cloud Ampere A1 Instance Creator - One-command startup (Windows)

echo ==========================================
echo Oracle Cloud Ampere A1 Instance Creator
echo ==========================================

REM Check for Python
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: python not found. Please install Python 3.8+
    exit /b 1
)

REM Check for virtual environment
if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Install/upgrade dependencies
echo Installing dependencies...
pip install --upgrade pip -q
pip install -r requirements.txt -q

REM Check for OCI config
if not exist "%USERPROFILE%\.oci\config" (
    echo Warning: %%USERPROFILE%%\.oci\config not found.
    echo Please run 'oci setup config' first.
    echo Or copy oci_config.example to %%USERPROFILE%%\.oci\config and fill in your credentials.
)

REM Check for SSH private key file
if not defined SSH_PRIVATE_KEY_FILE set SSH_PRIVATE_KEY_FILE=%USERPROFILE%\.ssh\id_rsa
if not exist "%SSH_PRIVATE_KEY_FILE%" (
    echo Warning: SSH private key not found at %SSH_PRIVATE_KEY_FILE%
    echo Set SSH_PRIVATE_KEY_FILE or place your key there.
)

echo.
echo Starting dashboard on http://localhost:5050 ...
start /B python dashboard.py

REM Give dashboard time to start
timeout /t 2 /nobreak >nul

echo Starting instance creator...
echo Dashboard available at: http://localhost:5050
echo Press Ctrl+C to stop
echo.

REM Run instance creator
python create_ampere_a1.py

REM Note: Dashboard runs in background, close window to stop