#!/bin/bash
cd "$(dirname "$0")"
echo "=========================================="
echo "  ERP Stock — TeamBike / SportMed"
echo "=========================================="
# Instala dependências se faltarem
if ! python3 -c "import flask" 2>/dev/null; then
  echo "A instalar dependências..."
  pip3 install --break-system-packages -r requirements.txt 2>/dev/null || pip3 install -r requirements.txt
fi
echo "A iniciar em http://localhost:5001 ..."
echo "Se a porta 5001 estiver ocupada (AirPlay no macOS), use: python3 app.py 8000"
echo "Prima CTRL+C para parar"
echo ""
python3 app.py 5001
