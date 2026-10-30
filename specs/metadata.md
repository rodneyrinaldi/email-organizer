# metadata.json — schema v1

Fonte de verdade. Todas as rotinas futuras trabalham sobre ele.

## Regras
- Escrita atômica.
- Cada rotina é dona das suas chaves; não remove chaves alheias.
- Datas em ISO 8601 com offset. Caminhos relativos ao diretório da mensagem.
- Todo arquivo referenciado tem `sha256` e `tamanho`.
- Mudança de schema → incrementa `schema_version`.

## Exemplo

```json
{
  "schema_version": 1,
  "id": "8e4c2b9a1f03",
  "chave_dedup": { "tipo": "message_id", "valor": "<123@dominio.com>" },

  "message_id": "<123@dominio.com>",
  "in_reply_to": "<120@dominio.com>",
  "references": ["<118@dominio.com>", "<120@dominio.com>"],

  "origem": {
    "tipo": "imap",
    "conta": "voce@gmail.com",
    "pasta": "INBOX",
    "uid": 48213,
    "uidvalidity": 1,
    "capturado_em": "2026-10-29T15:02:11-03:00"
  },
  "origens_adicionais": [
    { "tipo": "pst", "arquivo": "Advocacia.pst", "pasta": "Caixa de Entrada", "capturado_em": "..." }
  ],

  "direcao": "recebida",
  "data": "2026-10-29T14:33:10-03:00",
  "data_utc": "2026-10-29T17:33:10Z",
  "assunto": "Financiamento Imobiliário",
  "remetente": { "nome": "João Silva", "email": "joao@email.com" },
  "para": [{ "nome": "", "email": "contato@escritorio.adv.br" }],
  "cc": [],
  "bcc": [],

  "original": { "arquivo": "original/mensagem.eml", "sha256": "...", "tamanho": 183223 },
  "pdf_principal": { "arquivo": "mensagem.pdf", "sha256": "...", "tamanho": 90122 },
  "texto_markdown": { "arquivo": "mensagem-texto.md", "sha256": "...", "tamanho": 2211 },

  "corpo": [
    { "ordem": 1, "arquivo": "corpo/imagem-001.pdf", "content_id": "image001.png@01DB", "mime": "image/png", "sha256": "...", "tamanho": 1200 }
  ],
  "imagens_remotas": [
    { "url": "https://...", "status": "embutida", "sha256": "..." }
  ],

  "anexos": [
    {
      "ordem": 1,
      "tipo": "arquivo",
      "nome_original": "contrato.docx",
      "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "arquivo_original": { "arquivo": "anexos/originais/contrato.docx", "sha256": "...", "tamanho": 40211 },
      "arquivo_pdf": { "arquivo": "anexos/contrato.pdf", "sha256": "...", "tamanho": 51002 },
      "status": "convertido"
    },
    {
      "ordem": 2,
      "tipo": "email",
      "nome_original": "RE: proposta.eml",
      "diretorio": "anexos/emails/a91f0c",
      "status": "convertido"
    },
    {
      "ordem": 3,
      "tipo": "arquivo",
      "nome_original": "docs.zip",
      "arquivo_original": { "arquivo": "anexos/originais/docs.zip", "sha256": "...", "tamanho": 900122 },
      "status": "ignorado",
      "motivo": "zip não processado por definição"
    }
  ],

  "thread": { "tipo": "resposta", "pai_message_id": "<120@dominio.com>", "metodo": "cabecalho" },

  "classificacao": {
    "categoria": "Banco",
    "subcategoria": "Financiamento Imobiliario",
    "confianca": 0.94,
    "modelo": "qwen2.5:7b",
    "classificado_em": "..."
  },

  "pipeline": {
    "captura":       { "status": "ok", "em": "...", "versao": "0.1.0" },
    "artefatos":     { "status": "ok", "em": "...", "versao": "0.1.0" },
    "classificacao": { "status": "ok", "em": "...", "versao": "0.1.0" }
  },

  "avisos": [
    { "etapa": "anexos", "mensagem": "falha ao converter planilha.xlsx: timeout LibreOffice" }
  ]
}
```

## Valores
- `origem.tipo`: `imap | graph | pst | eml` (eml = importação de arquivo avulso)
- `chave_dedup.tipo`: `message_id | hash_cabecalhos` (sha256 remetente|data_utc|assunto) `| hash_conteudo` (sha256 do .eml, quando não há cabeçalhos)
- `id`: 12 hex de sha256(`tipo:valor` da chave)
- `direcao`: `recebida | enviada` (enviada = remetente é uma conta própria do config)
- `anexos[].tipo`: `arquivo | email`
- `anexos[].status`: `convertido | original_pdf | nao_convertido | ignorado`
- `thread.tipo`: `nova | resposta | encaminhamento`; `thread.metodo`: `cabecalho | assunto | ia` (assunto = prefixo RE:/ENC:/FW:)
- `pipeline.*.status`: `ok | erro | pendente`
- `imagens_remotas[].status`: `embutida | falhou`
