# Arquitetura

Monólito Python em um container. Módulos:

```
web/          FastAPI + Jinja2 + HTMX (localhost:8080)
jobs/         execução em thread background, progresso, relatório
fontes/       imap, graph, pst  → produzem .eml bruto + info de origem
captura/      dedup, índice, criação do diretório, grava original/
artefatos/    pdf principal, markdown, corpo, anexos
ia/           cliente Ollama, classificação, thread fallback
repositorio/  leitura/escrita atômica de metadata.json, índice, paths
```

## Pipeline (por mensagem)

```
fonte → bytes .eml + origem
  → chave de dedup → já existe? registra em origens_adicionais e para
  → cria diretório, grava original/mensagem.eml (+sha256)
  → parse MIME
  → artefatos (pdf, md, corpo, anexos, emails aninhados)
  → thread por cabeçalho
  → IA (classificação, thread fallback)
  → metadata.json final + índice
```

Cada etapa grava seu status em `pipeline.<etapa>`. Falha numa etapa não aborta as demais nem o job; vira aviso no JSON e no relatório. Reprocessar = rodar etapas a partir do `.eml` já salvo.

PST e IMAP/Graph convergem no mesmo `.eml` → mesmo resultado final.

## Estrutura de saída

```
/output/
├── config.json
├── _index.json                      # chave_dedup → caminho; reconstruível
├── _jobs/<job-id>/{log.txt,relatorio.json,relatorio.md}
├── .lixeira/<lote>/{_lote.json,<caminho original>…}   # rotina de limpeza
(estado em ESTADO_DIR, fora do output: segredos, UIDs IMAP, ids Graph, tokens)
└── <conta>/                          # conta que recebeu: e-mail IMAP/Graph ou pst-<arquivo>
    ├── .enviadas/<destinatario-email>/   # só enviadas (remetente = conta própria)
    │   └── AAAA-MM-DD_HH-MM-SS_<assunto-slug>_<id6>/ …
    └── <remetente-email>/
        └── AAAA-MM-DD_HH-MM-SS_<assunto-slug>_<id6>/
            ├── metadata.json
            ├── original/mensagem.eml
            ├── mensagem.pdf
            ├── mensagem-texto.md
            ├── corpo/imagem-001.pdf …
            └── anexos/
                ├── originais/<nome>
                ├── <nome>.pdf
                └── emails/<id6>/        # e-mail anexado, mesma estrutura (recursivo)
```

### Nomes
- Conta: `origem.conta` da 1ª captura (e-mail IMAP/Graph, minúsculas); PST → `pst-<arquivo-slug>`; sem conta → `_sem-conta`. Mensagem que chega em várias contas fica **uma vez só** (dedup global), na pasta da 1ª conta que a capturou; as demais ficam em `origens_adicionais`.
- Remetente: e-mail em minúsculas, sanitizado. Sem remetente → `_sem-remetente`.
- Enviadas (`direcao: enviada`): `<conta>/.enviadas/<primeiro destinatário>/` — 1º do To, senão Cc, senão Bcc; nenhum → `_sem-destinatario`. `reorganizar [--confirmar]` move diretórios de layouts antigos.
- Assunto slug: ASCII, sem acentos, máx. 40 chars.
- `id6`: 6 primeiros hex do `id` (ver metadata.md).
- Caminho total limitado (~200 chars) por compatibilidade com Windows; trunca o slug.
- Data no nome: horário local da mensagem.
- Colisão de nomes de anexo: sufixo `-2`, `-3`.
- Colisão de `id6` entre mensagens distintas: usa o `id` completo (12) no nome.
- Diretório montado em `.tmp-<id>/` e renomeado no fim: nunca fica incompleto.
- Nome é determinístico: sem índice, a dedup ainda detecta o diretório existente.

## Dedup
- Chave: `Message-ID` normalizado; ausente → `sha256(remetente|data_utc|assunto)`.
- `id` = sha256 da chave (hex, 12 chars no JSON).
- Duplicata (ex.: mesmo e-mail em 2 PSTs, ou PST + IMAP) não gera diretório novo; acrescenta origem em `origens_adicionais`.
