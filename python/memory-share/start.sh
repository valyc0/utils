#!/bin/bash

# Memory Share - Script di avvio
# Usage: ./start.sh [porta]

PORT=${1:-5000}
SUB_PATH=${2:-}
VENV_DIR="venv"

echo "==================================="
echo "  Memory Share - Avvio Server"
echo "==================================="
echo ""
echo "ℹ️  Esempi:"
echo "   ./start.sh                    # porta 5000, nessun sub-path"
echo "   ./start.sh 8080               # porta 8080"
echo "   ./start.sh 5000 /memory       # porta 5000, sub-path /memory"
echo ""

# Verifica se Python è installato
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 non trovato. Installalo prima di continuare."
    exit 1
fi

# Crea virtual environment se non esiste
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creazione virtual environment..."
    python3 -m venv $VENV_DIR
    echo "✅ Virtual environment creato"
fi

# Attiva virtual environment
source $VENV_DIR/bin/activate

# Verifica se le dipendenze sono installate
if ! python3 -c "import flask" 2>/dev/null; then
    echo "📦 Installazione dipendenze..."
    pip install -r requirements.txt
    echo "✅ Dipendenze installate"
    echo ""
fi

echo "🚀 Avvio server sulla porta $PORT..."
echo "🌐 Accedi a: http://localhost:$PORT"
echo ""
echo "Premi Ctrl+C per fermare il server"
echo ""

SUB_PATH=$SUB_PATH python3 app.py $PORT
