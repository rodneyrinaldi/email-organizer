# Artefatos

Todos derivados de `original/mensagem.eml`. Parse com `email` stdlib (`policy.default`).

## mensagem.pdf (PDF principal)
- HTML gerado por template Jinja2 → WeasyPrint.
- Bloco de cabeçalhos: De, Para, Cc, Bcc, Data, Assunto, Message-ID.
- Corpo: parte HTML preferencial; fallback texto (escapado, `pre-wrap`).
- Imagens inline: `cid:` substituído por data URI na posição original e **maximizadas**: largura útil da A4 (proporção mantida, altura ≤ 250 mm), ignorando width/height do HTML (classe `eo-colada`). Imagens remotas/`data:` mantêm o tamanho do HTML.
- Imagens remotas: baixadas (timeout curto, tamanho máx.) e embutidas; falha → registra em `imagens_remotas` e mantém placeholder com URL.
- HTML sanitizado (sem script/iframe/object/embed). Render só aceita `data:` (WeasyPrint `URLFetcher(allowed_protocols={"data"})`): nenhum acesso externo no render.
- Template com autoescape forçado: cabeçalhos nunca viram HTML.
- Data exibida como `dd/mm/aaaa hh:mm:ss (UTC-03:00)`.
- Rodapé: lista de anexos (nome, tamanho, sha256 curto). Anexos **não** incorporados.
- Rodapé técnico: id, sha256 do `.eml`, data de geração.

## mensagem-texto.md
- Somente texto do corpo. Sem cabeçalhos, metadados, imagens, anexos.
- HTML → Markdown (`markdownify` ou equivalente leve); texto puro passa direto.
- Remove citações? **Não** — preserva o corpo integral (evidência).

## corpo/
- Partes inline (com `Content-ID` referenciado ou `Content-Disposition: inline` imagem).
- Cada uma → PDF individual (`imagem-001.pdf`, ordem de aparição) via PyMuPDF.
- Imagem → PDF (corpo/ e anexos/): uma página **A4** por quadro, retrato ou paisagem conforme a imagem, ampliada/reduzida para ocupar a área útil (margem 10 mm), proporção mantida, pixels originais preservados.

## anexos/
- Original sempre salvo em `anexos/originais/`.
- Conversão para PDF:
  - pdf → cópia (`status: original_pdf`)
  - imagens → PyMuPDF
  - doc/docx/xls/xlsx/ppt/pptx/odt/rtf/txt/csv → LibreOffice headless (timeout, processo isolado)
  - html → WeasyPrint
  - `.msg` (Outlook) → `nao_convertido` até a onda 7
  - `.eml`/`message/rfc822` → `tipo: email`, processado recursivamente em `anexos/emails/<id6>/` (mesma estrutura, `metadata.json` próprio com `pai`)
  - zip/rar/7z → `ignorado` por definição
  - desconhecido → `nao_convertido`
- Um PDF por anexo; nunca compilado.

## Regras de classificação inline × anexo
- Imagem sem `Content-Disposition: attachment` → `corpo/` (ordem: referências `cid:` no HTML, depois as sem referência).
- Partes texto sem nome/disposição = alternativas do corpo, ignoradas.
- E-mail anexado sem nome → `<assunto>.eml`; `arquivo_original` aponta para `anexos/emails/<id6>/original/mensagem.eml`.
- Reprocessar apaga e regenera `mensagem.pdf`, `mensagem-texto.md`, `corpo/`, `anexos/`. `original/` nunca é tocado.

## Casos extremos (onda 7)
- `winmail.dat` (TNEF): `tnefparse`; anexos internos viram anexos normais; o `winmail.dat` fica como `ignorado` (original preservado). Corpo do TNEF usado se a mensagem não tiver outro.
- S/MIME assinado: conteúdo extraído normalmente; `smime.p7s` → `ignorado` (original preservado). Cifrado/opaco (`application/pkcs7-mime`): aviso + `nao_convertido`.
- Charset inválido: fallback `cp1252` com aviso.
- PDF protegido por senha: copiado (`original_pdf`), `motivo` + aviso.
- `.msg` (Outlook): `extract-msg` → `.eml` equivalente (cabeçalho `X-Email-Organizer: convertido de .msg`), processado como e-mail anexado (`tipo: email`, `motivo: convertido de .msg`); `arquivo_original` = o `.msg`.
- Corpo só em RTF (readpst `rtf-body.rtf`): HTML/texto encapsulado extraído com RTFDE e usado como corpo (aviso). Se já há corpo, o `rtf-body.rtf` é descartado (cópia). RTF puro: fica como anexo (LibreOffice).
- Multipart sem partes: ignorado (não vira anexo `.bin`).
- `.txt`: PDF via WeasyPrint (monoespaçado), sem LibreOffice.
- Imagem referenciada por `cid:` no HTML vai para `corpo/` mesmo marcada como `attachment`.
- Qualquer exceção numa parte → aviso, segue para próxima.
