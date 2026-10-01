# AI-Copilot: Private Engineering Knowledge Assistant

A fully on-premise assistant that answers questions about your C# codebase and engineering documents. No cloud services are used. Everything runs on your own machine.

- **Code questions:** "What does X do?", "Explain the CreateLot workflow", "Where is CreateLot used?", "Which classes interact with EquipmentController?"
- **Document questions:** answers from Markdown, TXT, PDF and DOCX files, including text found in diagrams and screenshots.
- **Cited answers:** every answer ends with a **Sources** list (`repo/path:lines` or `file > Page N`).

---

## Architecture

```
OpenWebUI (Docker, :3000)
      │  OpenAI-compatible API
      ▼
RAG service (Python / FastAPI, host, :8000)
      │                │
      │ search         │ generate
      ▼                ▼
Qdrant (Docker, :6333)   Ollama (host, :11434)
      ▲                    ├── qwen3:4b          chat and code explanation
      │                    ├── nomic-embed-text  embeddings (768 dims)
Ingestion (Python, host)   └── qwen2.5vl:3b      image / diagram descriptions
      ├── C# repos  (tree-sitter: namespace, class, method, property + call data)
      └── PDF / DOCX / MD / TXT
```

How a question is answered:

1. Identifiers in the question (e.g. `CreateLot`, `OrderProcessor`) trigger **exact lookups** in Qdrant's metadata: callers, dependents, class members.
2. The question is also embedded for **semantic search** over code and documents.
3. The results are combined into a context block (about 14,000 characters) and sent to `qwen3:4b` with a "use only this context and cite sources" prompt.
4. Sources are appended to the reply.

Two Qdrant collections:

| Collection | Contents |
|---|---|
| `engineering_code` | One point per namespace-level type, method, constructor and property, with signature, doc comments, body, call graph (`calls`, `called_names`, `created_types`), `dependencies`, `base_types` |
| `engineering_docs` | Chunks of Markdown, TXT, PDF and DOCX files, with headings, page numbers and image descriptions |

---

## Requirements

- Windows with Docker Desktop (WSL2 backend)
- Python 3.10+ (Windows Python, used for a virtual environment)
- [Ollama](https://ollama.com) running locally
- Enough RAM for a 4B model (a GPU helps a lot, but is not required)

Pull the models once:

```powershell
ollama pull qwen3:4b
ollama pull nomic-embed-text
ollama pull qwen2.5vl:3b
```

---

## Folder structure

```
AI-Copilot/
├── data/
│   ├── repos/<RepoName>/        C# repositories to index
│   └── docs/...                 PDF, DOCX, MD, TXT (any subfolders)
├── docker/
│   └── docker-compose.yml       Qdrant + OpenWebUI
├── ingestion/
│   ├── embeddings.py            Ollama embedding calls
│   ├── store.py                 Qdrant client and collection setup
│   ├── chunking.py              Markdown / text chunker
│   ├── csharp_parser.py         tree-sitter C# symbol + call extraction
│   ├── repo_ingest.py           Full C# repo indexing
│   ├── text_ingest.py           MD / TXT ingestion
│   ├── pdf_ingest.py            PDF ingestion (+ optional vision)
│   ├── docx_ingest.py           DOCX ingestion (+ optional vision)
│   ├── vision.py                qwen2.5vl image descriptions (cached)
│   ├── docs_common.py           Shared document storage code
│   ├── ingest_docs.py           Full document ingestion
│   ├── sync.py                  Incremental sync (use this day to day)
│   ├── code_search.py           Command-line code index queries
│   └── search_test.py           Command-line document search
├── rag/
│   └── rag_service.py           OpenAI-compatible RAG API
├── qdrant/                      Qdrant data (created by Docker)
├── openwebui/                   OpenWebUI data (created by Docker)
├── vision_cache/                Cached image descriptions (created on demand)
├── index_state.db               Sync manifest (created by sync.py)
└── .venv/                       Python virtual environment
```

---

## Setup

### 1. Docker services

`docker/docker-compose.yml`:

```yaml
services:
  qdrant:
    image: qdrant/qdrant:latest
    container_name: qdrant
    restart: unless-stopped
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - ../qdrant:/qdrant/storage

  openwebui:
    image: ghcr.io/open-webui/open-webui:main
    container_name: openwebui
    restart: unless-stopped
    ports:
      - "3000:8080"
    environment:
      - OLLAMA_BASE_URL=http://host.docker.internal:11434
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - ../openwebui:/app/backend/data
```

```powershell
cd AI-Copilot\docker
docker compose up -d
docker ps          # qdrant and openwebui should be running
```

Once everything works, pin the image tags to specific versions so an update can't surprise you.

### 2. Python environment

```powershell
cd AI-Copilot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install qdrant-client requests tree-sitter tree-sitter-c-sharp pymupdf python-docx fastapi uvicorn
```

Do not name any file `qdrant_client.py`. It would shadow the real package.

### 3. Add your data

- C# repos: `data\repos\<RepoName>\...`
- Documents: `data\docs\...`

### 4. First index

```powershell
cd ingestion
python store.py                        # creates the two collections
python sync.py --full                  # indexes everything (use --vision for images, slower)
```

### 5. Start the RAG service

```powershell
cd ..\rag
uvicorn rag_service:app --host 127.0.0.1 --port 8000 --reload
```

### 6. Connect OpenWebUI

1. Open `http://localhost:3000` and create the local admin account.
2. Admin Panel → Settings → Connections → OpenAI API → **+**
3. URL: `http://host.docker.internal:8000/v1`, key: `local`
4. Start a new chat and select **engineering-assistant**.

If OpenWebUI cannot connect, restart uvicorn with `--host 0.0.0.0`.

---

## Daily use

### Updating the index

`sync.py` only processes new, changed or deleted files:

```powershell
cd ingestion
python sync.py                    # docs + code
python sync.py code               # only C# repos
python sync.py docs --vision      # docs, with image descriptions
python sync.py --dry-run          # show the plan, change nothing
python sync.py --full             # re-index everything in scope
python sync.py --adopt            # mark current files as already indexed (no re-embedding)
```

Notes:

- Change detection uses file size and modified time, then a content hash, so a `git checkout` that doesn't change content is skipped.
- A failed file is not recorded, so the next run retries it.
- A `sync.lock` file stops two syncs from running at once. If a crashed run leaves it behind, delete it.
- `--vision` adds descriptions of images, scanned pages and vector diagrams using `qwen2.5vl:3b`. It's slow on CPU (about 20 to 60 seconds per image), but results are cached in `vision_cache/`, so each image is only processed once.
- If you change the chunking, the parser or the embedding model, bump `PIPELINE_VERSION` in `sync.py`. The next sync then re-indexes everything. A different embedding model also means new collections with the new vector size.

`ingest_docs.py` and `repo_ingest.py` do full rebuilds. After one, run `python sync.py --adopt` to refresh the manifest.

### Debugging retrieval from the command line

```powershell
python code_search.py class OrderService         # a class and its members
python code_search.py usages CreateLot           # who calls this method name
python code_search.py uses EquipmentController   # who depends on, creates or inherits this type
python code_search.py search "how is configuration loaded"
python search_test.py "a question about your documents"
```

The RAG service also logs `[rag] q=... -> N sources` for each question. If an answer is poor, check these first. Missing retrieval is a more common cause than a weak model.

### Example questions

- "What does OrderProcessor do?"
- "Explain the CreateLot workflow."
- "Where is CreateLot used?"
- "Which classes interact with EquipmentController?"
- "How is configuration loaded?"
- Anything your PDFs and documents cover, such as SOPs and manuals

Identifiers in PascalCase or camelCase trigger the exact call-graph lookups. Questions without an identifier use semantic search only.

---

## Running in the background

Keep using a terminal with `--reload` while tuning. Once it's stable:

**Docker services:** In Docker Desktop → Settings → General, enable **Start Docker Desktop when you sign in**. The `restart: unless-stopped` policy brings Qdrant and OpenWebUI back automatically. Ollama already starts from the tray.

**RAG service:** create `run-rag.ps1`:

```powershell
Set-Location C:\AI-Copilot\rag
& C:\AI-Copilot\.venv\Scripts\python.exe -m uvicorn rag_service:app --host 127.0.0.1 --port 8000 *>> C:\AI-Copilot\rag_service.log
```

```powershell
$action  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument '-WindowStyle Hidden -ExecutionPolicy Bypass -File C:\AI-Copilot\run-rag.ps1'
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "AI-Copilot RAG" -Action $action -Trigger $trigger
Start-ScheduledTask -TaskName "AI-Copilot RAG"
```

After code changes, restart with `Stop-ScheduledTask` then `Start-ScheduledTask` on the same task name.

**Nightly sync (optional):**

```powershell
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument '/c "C:\AI-Copilot\.venv\Scripts\python.exe sync.py --vision >> C:\AI-Copilot\sync.log 2>&1"' -WorkingDirectory "C:\AI-Copilot\ingestion"
$trigger = New-ScheduledTaskTrigger -Daily -At 2am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
Register-ScheduledTask -TaskName "AI-Copilot Sync" -Action $action -Trigger $trigger -Settings $settings
```

---

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `failed to connect to the docker API ... dockerDesktopLinuxEngine` | Docker Desktop's engine isn't running. Start Docker Desktop and wait for "Engine running"; check `wsl --status` and virtualization if it won't start |
| OpenWebUI doesn't list `engineering-assistant` | RAG service isn't running, or the connection URL is wrong. Test `Invoke-RestMethod http://127.0.0.1:8000/v1/models`; use `--host 0.0.0.0` if the container can't reach it |
| OpenWebUI doesn't show Ollama models | Admin Settings → Connections → Ollama must be `http://host.docker.internal:11434` |
| Answers invent classes or ignore your code | Check the `[rag]` log line and `code_search.py` output. If the symbol isn't in the index, re-run `sync.py code` |
| "Where is X used" mixes up same-named methods | `usages` matches by method name only (see limitations) |
| Answers start with a `<think>` block | Ollama is too old to support `think: false`; update it |
| `sync.py` says another sync is running | Delete `sync.lock` if no sync is actually running |
| Ingestion returns `FAILED <file>` | Typically a password-protected PDF or a legacy `.doc`. Convert or unlock it |
| Slow vision pass | Expected on CPU. Don't chat while it runs; Ollama swaps models and both slow down |

---

## Known limitations

- **Method-name call matching:** callers are found by method name, not by resolved type. Two classes with a `CreateLot` method can show each other's callers. The stored `calls` text (e.g. `_lotService.CreateLot`) helps you tell them apart.
- **Model size:** `qwen3:4b` is solid on single-class explanations but weaker on long multi-method workflows. If hardware allows, set `CHAT_MODEL` in `rag_service.py` to a larger Qwen3.
- **Vision accuracy:** a 3B vision model transcribes text and describes diagrams reasonably, but misreads dense drawings and small callouts. Treat image chunks as searchable hints and open the cited page to confirm.
- **PDF tables** are indexed as plain text lines, not structured rows.
- **Context window:** `NUM_CTX = 8192` and a 14,000-character context budget are tuned for modest hardware. Raise both together if you have the memory.
- **Legacy formats:** `.doc` isn't supported; save as `.docx`.
- **Security:** the services listen on localhost only, with no authentication. That is intended for single-user local use. Don't expose ports 11434, 6333 or 8000 on a shared network without adding access control.

---

## Roadmap (not part of the MVP)

1. **Retrieval evaluation:** a small set of questions with expected sources, to measure quality when you change chunking, prompts or models.
2. **Type-aware call resolution:** use the field and constructor dependencies already stored to tell which class a call really targets.
3. **Hybrid search:** add keyword (BM25-style) matching alongside vectors in Qdrant.
4. **Reranking:** a local reranker model over the top results.
5. **Containerize the RAG service** once it's stable (environment variables for the Ollama and Qdrant hosts, a Dockerfile, and a compose entry).
6. **LangGraph agents:** separate code, document and vision agents behind a supervisor.
7. **PostgreSQL / pgvector:** only if you need relational data alongside vectors.
8. **Enterprise sources:** GitHub, SMB shares, SharePoint, SQL databases as new ingestion connectors, each feeding the same `sync.py` pattern.
