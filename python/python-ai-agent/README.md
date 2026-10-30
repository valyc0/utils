# Python Coding Agent

Port Python di `spring-boot-ai-agent`: un coding agent da riga di comando che lavora come un agente "vero".
Gli dai un compito in linguaggio naturale, lui esplora una directory, legge, crea e modifica file, lancia
comandi, legge gli errori e corregge. Serve solo un endpoint LLM compatibile con l'API OpenAI
(Ollama, DeepSeek, Groq, OpenRouter, ...). Tutto il codice è in [`agent.py`](agent.py).

## Come funziona

1. Scrivi una richiesta nel terminale.
2. Il modello decide quale tool usare; l'agente lo esegue e restituisce il risultato al modello. La risposta
   arriva in streaming. Se il modello espone il reasoning (`delta.reasoning_content`, es. DeepSeek) e
   `AGENT_REASONING=true`, viene mostrato in corsivo con prefisso `[reasoning]`, separato dalla risposta (`[risposta]`).
3. Il ciclo si ripete finché il modello risponde senza chiamare altri tool.
4. La conversazione resta in memoria (ultimi `MAX_MESSAGGI`=1000 messaggi).

Premi **ESC** per interrompere: la richiesta (e il comando shell in corso) viene annullata, il turno non
viene salvato in cronologia. Richiede un terminale Unix con stdin TTY; altrimenti usa Ctrl+C.

## Tool

| Tool | Cosa fa |
|------|---------|
| `listFiles` | Elenca i file di una directory (ricorsivo, ignora `.git` e `target`, max 2000) |
| `readFile` | Legge un file |
| `writeFile` | Crea o sovrascrive un file (crea le directory mancanti) |
| `editFile` | Sostituisce un testo esatto (deve comparire una sola volta) |
| `searchInFiles` | Regex case-insensitive nei file di testo, filtro glob, 2 righe di contesto, max 500 risultati (ignora `.git`, `.idea`, `target`, `build`, `node_modules`, `generated`) |
| `gitDiff` | `git diff HEAD`, anche per un solo file |
| `runCommand` | Comando shell nella directory di lavoro (timeout 5 min, output max 50000 caratteri). **Chiede conferma `[s/N]`**, salvo `AGENT_AUTO=true` |
| `webSearch` | DuckDuckGo senza API key, primi 10 risultati. Solo con `AGENT_WEBSEARCH=true` |
| `readWebPage` | Scarica una pagina e ne restituisce il testo (max 30000 caratteri). Solo con `AGENT_WEBSEARCH=true` |

Gli errori dei tool vengono restituiti al modello come testo. I percorsi fuori dalla directory di lavoro sono rifiutati.

## Requisiti e avvio

- Python 3.10+ (nessuna dipendenza esterna: solo libreria standard)
- Un endpoint LLM OpenAI-compatibile **con tool calling**

Copia `.env.example` in `.env` (nella directory da cui lanci l'agente):

| Variabile | Significato |
|-----------|-------------|
| `LLM_URL` | URL base **senza** `/v1` (viene aggiunto `/v1/chat/completions`) |
| `LLM_MODEL` | Nome del modello |
| `LLM_KEY` | API key (qualsiasi valore se non richiesta) |
| `AGENT_AUTO` | `true` = nessuna conferma per `runCommand` |
| `AGENT_REASONING` | `true` = mostra il reasoning |
| `AGENT_WEBSEARCH` | `true` = abilita `webSearch` e `readWebPage` |

Le variabili d'ambiente hanno la precedenza sul file `.env`.

```bash
python3 agent.py /percorso/del/progetto
./run.sh /percorso/del/progetto     # con AGENT_AUTO=true e AGENT_WEBSEARCH=true
```

## AGENT.md

Se nella directory di lavoro esiste `AGENT.md`, il contenuto viene aggiunto al system prompt.

## Note

- `runCommand` esegue **qualsiasi** comando: usalo su directory fidate, meglio con un repo git.
- In caso di errore 429 l'agente aspetta 15 secondi e riprova (max 6 volte).
- Max 200 chiamate ai tool per richiesta (`MAX_ITERAZIONI`).
