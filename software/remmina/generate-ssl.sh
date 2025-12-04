#!/bin/bash

# Crea la directory per i certificati SSL
mkdir -p ssl

# Genera certificato self-signed
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout ssl/key.pem \
  -out ssl/cert.pem \
  -subj "/C=IT/ST=Italy/L=Rome/O=Remmina/CN=localhost"

echo "Certificati SSL generati in ./ssl/"
