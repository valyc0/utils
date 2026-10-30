#!/usr/bin/env bash
# Avvia l'agente con AGENT_AUTO=true (nessuna conferma per runCommand) e AGENT_WEBSEARCH=true (ricerca web).
# Uso: ./run.sh [directory-di-lavoro]
set -euo pipefail

workdir="${1:-$PWD}"
cd "$(dirname "$0")"

export AGENT_AUTO=true
export AGENT_WEBSEARCH=true
exec python3 agent.py "$workdir"
