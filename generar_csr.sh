#!/usr/bin/env bash
# Genera la clave privada y el CSR para pedir el certificado a ARCA.
# Uso:  ./generar_csr.sh homo    (homologación / pruebas)
#       ./generar_csr.sh prod    (producción)
set -euo pipefail

ENTORNO="${1:-homo}"
cd "$(dirname "$0")"
mkdir -p certs

read -rp "Tu CUIT (sin guiones): " CUIT
read -rp "Tu nombre o razón social (como figura en AFIP): " NOMBRE

KEY="certs/${ENTORNO}.key"
CSR="certs/${ENTORNO}.csr"

if [[ -f "$KEY" ]]; then
  read -rp "Ya existe $KEY. ¿Sobrescribir? (s/N): " ok
  [[ "$ok" == "s" ]] || { echo "Cancelado."; exit 1; }
fi

openssl genrsa -out "$KEY" 2048
openssl req -new -key "$KEY" \
  -subj "/C=AR/O=${NOMBRE}/CN=facturador/serialNumber=CUIT ${CUIT}" \
  -out "$CSR"

echo
echo "✅ Listo:"
echo "   Clave privada (SECRETA, no la compartas): $KEY"
echo "   CSR para subir a ARCA:                    $CSR"
echo
echo "Siguiente paso: subí el contenido de $CSR a ARCA y descargá el"
echo "certificado como certs/${ENTORNO}.crt  (ver README, sección 2)."
echo
echo "Contenido del CSR (copialo):"
echo "----------------------------------------"
cat "$CSR"
echo "----------------------------------------"
