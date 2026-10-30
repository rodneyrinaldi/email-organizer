# Decisões (ADR)

Formato: contexto curto → decisão → consequência. Nova decisão = nova entrada; nunca editar antigas, só marcar como substituída.

**ADR-001 — Arquivos + JSON, sem banco.** Uso pessoal, volume baixo. `metadata.json` por mensagem + `_index.json` reconstruível. Busca futura via rotinas.

**ADR-002 — Diretório fixo por conta e remetente.** `/output/<conta>/<remetente>/<data>_<assunto>_<id6>/` (enviadas: `<conta>/.enviadas/<destinatário>/…`). Conta = quem recebeu, pois o mesmo remetente escreve para várias contas. Categoria vive só no JSON; caminho só muda via `reorganizar` (mudança de layout), nunca por IA.

**ADR-003 — `.eml` bruto como base.** Toda fonte converge para `.eml`; todos os artefatos derivam dele. Garante PST = IMAP = Graph no resultado e reprocessamento sem origem.

**ADR-004 — PST via readpst.** Sem Outlook, sem compilar libpff. Limitação: MIME reconstruído.

**ADR-005 — Ollama remoto.** URL configurável; imagem Docker não carrega modelos.

**ADR-006 — Config único em `/output/config.json`.** Simplicidade; senhas locais em texto (uso pessoal), nunca versionado.

**ADR-007 — Jobs em thread única.** Sem fila. Lock de 1 job por vez, progresso por polling HTMX.

**ADR-008 — Thread por cabeçalho, IA como fallback.** Determinístico primeiro; `thread.metodo` registra a origem da inferência.

**ADR-009 — Compactados não processados.** zip/rar/7z: original salvo, `status: ignorado`, listado no relatório.

**ADR-011 — Dev e testes em container.** WeasyPrint/LibreOffice no Windows são frágeis; `docker compose --profile dev run --rm dev` roda pytest no mesmo ambiente da imagem final. Testes que dependem dessas libs são pulados fora do container.

**ADR-012 — Artefatos em processos paralelos.** Medido em PST real: 3,5 s/msg sequencial (53% download de imagens remotas, 40% WeasyPrint). Downloads paralelos com cliente HTTP compartilhado + `ProcessPoolExecutor` (spawn) para artefatos; captura segue sequencial. Resultado: 451 msgs de 26 min → 2 min.

**ADR-013 — CSS sem `overflow-wrap: anywhere`.** Causa layout patológico em tabelas no WeasyPrint (>60 s numa mensagem do e-SAJ vs 0,7 s com `break-word`). Render protegido por `SIGALRM` (`timeout_conversao_s`) nos workers.

**ADR-010 — E-mail anexado recursivo.** Subdiretório `anexos/emails/<id6>/` com estrutura e `metadata.json` próprios, referência ao pai.

**ADR-014 — Classificação depois dos artefatos, sequencial.** Artefatos e classificação gravam o mesmo `metadata.json`; rodar em sequência evita corrida sem lock. Ollama remoto atende uma requisição por vez de qualquer forma. Uma chamada por mensagem (classificação + thread fallback juntas), `format` = JSON Schema (structured output). Primeira falha de conexão → resto do lote fica `pendente` sem novas chamadas.

**ADR-015 — Segredos e estado fora do output.** Output fica em pasta sincronizada; `segredos.json`, tokens e estado incremental ficam em `ESTADO_DIR` (`EMAIL_ORGANIZER_ESTADO`). `config.json` continua na raiz do output (ADR-006), sem senhas.

**ADR-016 — UI sem estado próprio.** Jobs em memória (um por vez, thread) + relatórios em `_jobs/`; sem banco. HTMX e Tailwind via CDN (versões fixas; exige internet no navegador). Subdiretório de destino descartado: índice, dedup e estado são por raiz.
