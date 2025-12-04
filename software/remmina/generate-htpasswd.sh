#!/bin/bash

# Script per generare il file .htpasswd per l'autenticazione Basic di Nginx

USERNAME=${1:-admin}
PASSWORD=${2:-password}

echo "Generazione file .htpasswd per utente: $USERNAME"

# Crea la directory conf se non esiste
mkdir -p conf

# Genera il file .htpasswd usando openssl
# Il formato è: username:password_hash
echo "$USERNAME:$(openssl passwd -apr1 $PASSWORD)" > conf/.htpasswd

echo "File .htpasswd generato in conf/.htpasswd"
echo "Username: $USERNAME"
echo "Password: $PASSWORD"
