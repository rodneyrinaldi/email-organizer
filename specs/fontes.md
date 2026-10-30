# Fontes

Toda fonte implementa a mesma interface e entrega `(bytes_eml, origem)`. Nenhuma fonte grava artefato; quem grava é `captura/`.

```python
class Fonte(Protocol):
    def listar_pastas(self) -> list[str]: ...
    def mensagens(self, pastas: list[str]) -> Iterator[MensagemBruta]: ...
```

## Execução (`processar.py`)
Por pasta: lê → filtro local (`aceita_todos`) → `capturar` → `leitor.registrar` → artefatos (se nova ou etapa `artefatos` ≠ ok). Erro numa mensagem/pasta/fonte não derruba o resto. Saída em `_jobs/<job>/{log.txt,relatorio.json,relatorio.md}`.

CLI: `python -m email_organizer [--output /output] processar [--fonte NOME]... [--sem-artefatos] | pastas FONTE | conectar FONTE | classificar [--todas] | reconstruir-indice | reiniciar [--confirmar]`.

`reiniciar`: apaga acervo + `_index.json` + `_jobs/` + estado incremental; preserva `config.json`, `segredos.json`, `tokens/`. Sem `--confirmar` só lista.

## IMAP
- `imaplib` stdlib, SSL. `select(pasta, readonly=True)`, `FETCH BODY.PEEK[]`. Nunca STORE/COPY/MOVE/EXPUNGE.
- Incremental: `.estado/imap/<conta>/<pasta>.json` com `uidvalidity` e `capturados[]` (UIDs já capturados, novos ou duplicados). Filtradas **não** entram → mudar o filtro reavalia. Mudou `uidvalidity` → varre tudo (dedup evita duplicatas).
- Pré-filtro no servidor só por data (`SENTSINCE`/`SENTBEFORE`, cabeçalho Date, ±1 dia de folga). Texto não vai ao servidor (não ignora acentos → falso negativo).
- Gmail: senha de app; pastas `[Gmail]/...` com nomes UTF-7 modificado (decodificar).
- Pastas de enviados entram normalmente.

## Microsoft Graph
- Hotmail/Outlook.com (tenant `consumers`) e Microsoft 365.
- Auth: MSAL device code flow; app registrado pelo usuário no Entra (gratuito). Escopo `Mail.Read` (somente leitura).
- Bruto: `GET /me/messages/{id}/$value` → MIME original.
- Pastas: árvore completa via `mailFolders` + `childFolders`, nomes como caminho (`Caixa de Entrada/Mariana`).
- Incremental: como IMAP — ids já capturados por pasta em `.estado/graph/<conta>/`. Pré-filtro só por `sentDateTime`.
- Retry em 429/503/504 respeitando `Retry-After`.
- Conectar: `python -m email_organizer conectar "<fonte>"` (exibe URL + código; token em `.estado/tokens/`).

### Registro do app (uma vez)
1. portal.azure.com → Microsoft Entra ID → Registros de aplicativo → Novo registro.
2. Tipos de conta: **Somente contas pessoais da Microsoft** (Hotmail) ou incluir organizacionais (365).
3. Autenticação → **Permitir fluxos de cliente público = Sim**.
4. Permissões de API → Microsoft Graph → Delegadas → `Mail.Read`.
5. Copiar *ID do aplicativo (cliente)* para `client_id`.

## PST
- `readpst -e -t e -8 -q -o <tmp> <pst>`: um `.eml` por mensagem, só e-mails, UTF-8. Sem `-b`: o corpo RTF vem como `rtf-body.rtf` (mensagens do Outlook só com RTF perderiam o corpo). Gera `<tmp>/<raiz do PST>/<pasta>/<n>.eml`; nome da pasta = caminho abaixo da raiz (ex. `Caixa de Entrada/Mariana`).
- Sem estado incremental: reexporta a cada execução; dedup evita duplicatas. Temporário removido ao fechar.
- Volume PST montado **somente leitura**.
- PSTs no OneDrive podem estar bloqueados pelo Outlook/sync → erro claro no relatório, continua os demais.
- Origem registra `arquivo` e `pasta` dentro do PST.
- Atenção: há PSTs sobrepostos (`X.pst` e `X-copia.pst`) → dedup obrigatória e eficiente.
- Limitação conhecida: `readpst` reconstrói o MIME (não existe MIME original em PST). Registrar isso em `origem.observacao`.
