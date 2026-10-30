# email-organizer

Repositório documental pessoal (uso jurídico) orientado a evidências. E-mail é só fonte de entrada.

**Unidade fundamental:** 1 mensagem = 1 diretório = 1 `metadata.json` = N artefatos derivados.

## Leia antes de qualquer trabalho
- `specs/README.md` — índice das specs e princípios
- `specs/ondas.md` — roadmap e status atual

## Regras invioláveis
- Origem é **somente leitura**: nunca apagar, mover, marcar como lida ou alterar mensagens (IMAP: `BODY.PEEK`, `readonly=True`).
- `original/mensagem.eml` é gravado byte a byte e nunca mais alterado. Todo artefato deriva dele.
- `metadata.json` é a fonte de verdade. Rotinas só **acrescentam/atualizam suas próprias chaves**; nunca removem chaves de outras rotinas.
- Escrita de JSON sempre atômica (tmp + rename).
- Reprocessamento nunca reacessa a caixa postal.
- IA só enriquece o JSON; nunca decide fluxo, caminho ou exclusão.
- Simplicidade: sem banco, fila, Redis, Celery, SPA, microsserviços. Ver `specs/decisoes.md`.

## Stack
Python 3.12 · FastAPI · Jinja2 · HTMX · WeasyPrint · PyMuPDF · LibreOffice headless · readpst (libpst) · Ollama (remoto, URL em config).

## Layout
- `src/email_organizer/` — código (pacote)
- `tests/` — pytest; fixtures `.eml` em `tests/fixtures/`
- `specs/` — specs funcionais/técnicas e ADRs
- `.claude/skills/` — fluxos do projeto (`onda`, `rotina`, `spec`)

## Comandos
```
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/pytest -q                              # local: pula testes de WeasyPrint/LibreOffice
docker compose --profile dev run --rm dev            # suíte completa no container
.venv/Scripts/ruff check . && .venv/Scripts/ruff format .
docker compose up --build        # http://localhost:8080 (pastas em .env: OUTPUT_DIR, PST_DIR)
```

## Convenções
- Tudo em português (identificadores, chaves do `metadata.json`, UI, logs, specs), exceto termos técnicos consagrados.
- Tipagem completa; modelos com Pydantic v2.
- Toda mudança de schema do `metadata.json` incrementa `schema_version` e atualiza `specs/metadata.md`.
- Comunicação com o usuário: curta, direta, dev para dev.
