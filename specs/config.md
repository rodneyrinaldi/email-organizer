# config.json

Local: raiz do output (`/output/config.json`). Único arquivo de configuração.

Pastas do host montadas no container: `.env` (modelo `.env.example`, não versionado) — `OUTPUT_DIR` (raiz do output, hoje `D:/Sincronizado/Emails`) `PST_DIR` (PSTs, somente leitura) e `ESTADO_DIR` (hoje `D:/.email-organizer-estado`, montado em `/estado`, fora da pasta sincronizada). Fora do container: `--output <pasta> --estado <pasta>`.

`config.json` não tem senhas (pode ser sincronizado/backup). Pasta de estado (descartável, exceto `segredos.json`):
- `segredos.json` — `{ "<nome da fonte IMAP>": "<senha de app>" }`; backup = gerenciador de senhas.
- `tokens/` (Graph; refaz com `conectar`), `imap/`, `graph/` (incremental; perder só faz reler, dedup evita duplicatas).
Modelo: `config.example.json` na raiz do repositório.

## Status das fontes
Toda entrada de `imap[]`, `graph[]` e `pst[]` tem `ativo: true|false`. Só fontes ativas aparecem na UI e são processadas. `pastas[]` não vazio restringe o processamento às pastas listadas (comparação sem diferenciar maiúsculas; labels Gmail aninhados via nome completo, ex. `Clientes/mariana`).

## Filtros
Selecionam o que é lido/processado. Mensagem filtrada não é capturada; só conta no relatório (`filtradas`).

Critérios (E entre critérios; OU dentro de lista; texto por trecho, sem maiúsculas/acentos):
`data_de`, `data_ate` (inclusivas, data local da mensagem), `remetente[]`, `destinatario[]` (To/Cc/Bcc), `assunto[]`, `corpo[]`, `com_anexo` (bool), `excluir_remetente[]`. Mensagem sem data é rejeitada por filtro de data.

Onde (mais específico vence):
1. Pasta: `"pastas": [{"nome": "mariana", "filtro": {...} | "<nome>"}]` (string simples = sem filtro próprio).
2. Fonte: `"filtro": {...} | "<nome>"` na entrada imap/graph/pst — vale para pastas sem filtro próprio.
3. Reutilizável: `"filtros": {"<nome>": {...}}` na raiz, referenciado por nome. Nome inexistente = erro ao carregar.
4. Avulso (UI): combinado com E ao filtro resolvido.

Execução: fontes podem pré-filtrar no servidor (IMAP `SEARCH`, Graph `$filter`), mas a decisão final é sempre a avaliação local (`filtro.py`) → PST e IMAP dão o mesmo resultado. Filtros aplicados ficam em `origem.filtro`.

## Seções
- `contas_proprias[]` — e-mails do usuário (define `direcao: enviada`). Inclui automaticamente as contas abaixo.
- `imap[]` — `nome`, `host`, `porta`, `usuario`, `ssl` (senha em `segredos.json`), `pastas[]` (vazio = listar na UI).
- `graph[]` — `nome`, `usuario`, `client_id`, `tenant` (`consumers` p/ Hotmail/Outlook.com, `organizations` ou id p/ 365), `pastas[]`.
- `pst[]` — `ativo`, `nome`, `caminho` (um arquivo por entrada), `pastas[]` (vazio = todas).
- `ollama` — `url`, `modelo`, `timeout_s`, `habilitado`.
- `opcoes` — `pdf_principal`, `markdown`, `classificacao`, `baixar_imagens_remotas`, `timeout_conversao_s` (LibreOffice e render WeasyPrint), `trabalhadores` (processos de artefatos; 0 = metade dos núcleos).
- `categorias[]` — lista de categorias da IA.

## Segredos
- Senhas IMAP em `<ESTADO_DIR>/segredos.json`, por nome de fonte. `config.json` sem senhas.
- Tokens Graph em `<ESTADO_DIR>/tokens/`.
- Logs nunca imprimem senha/token.
