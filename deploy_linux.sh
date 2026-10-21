#!/usr/bin/env bash
# ==============================================================================
# Script de Despliegue Automático de SIDI en Servidor Linux (Ubuntu / Debian)
# ==============================================================================
set -e

echo "=========================================================="
echo "  Iniciando despliegue de SIDI (Departamento de Inventarios)"
echo "=========================================================="

if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    if command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
    else
        echo "Error: sudo no está instalado y no eres root."
        exit 1
    fi
fi

# 1. Actualización de paquetes del sistema
echo "[1/6] Actualizando repositorios e instalando dependencias base..."
$SUDO apt-get update -y
$SUDO apt-get install -y python3 python3-pip python3-venv git curl

# 2. Configurar entorno virtual de Python
echo "[2/6] Creando entorno virtual de Python..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate

# 3. Instalar librerías de requirements.txt
echo "[3/6] Instalando dependencias de Python..."
pip install --upgrade pip
pip install -r requirements.txt

# 4. Instalar Chromium y dependencias de sistema para Playwright
echo "[4/6] Instalando Playwright y Chromium para el bot de extracción..."
playwright install chromium
$SUDO playwright install-deps chromium

# 5. Asegurar permisos y carpetas necesarias
echo "[5/6] Preparando directorios del sistema..."
mkdir -p data storage/temp app/static/uploads/formatos app/static/uploads/logos
chmod -R 755 storage data app/static

# 6. Configurar servicio systemd (opcional pero recomendado para 24/7)
echo "[6/6] Verificando servicio del sistema..."
APP_DIR=$(pwd)
sed -i "s|/opt/sidi|$APP_DIR|g" sidi.service 2>/dev/null || true

echo "=========================================================="
echo "  ¡Despliegue completado con éxito!"
echo "=========================================================="
echo "Para arrancar SIDI manualmente:"
echo "  source venv/bin/activate && python main.py"
echo ""
echo "Para instalarlo como servicio 24/7 con systemd:"
echo "  sudo cp sidi.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable --now sidi"
echo "=========================================================="
