# Rotina: Resumo

Menu **Rotinas → Resumo** (`/resumo`) e CLI `resumir <caminho>… --saida x.pdf [--refazer]`.
Gera um PDF com o resumo de cada mensagem selecionada e, ao final, **Considerações** sobre o
conjunto. IA só enriquece: nada é movido ou apagado.

## Seleção
Mesma árvore da limpeza (conta → remetente/`.enviadas` → mensagens; PST → pastas do PST).
Pasta marcada cobre tudo abaixo. Mensagens em ordem cronológica (`data_utc`).

## Por mensagem
- Entrada: cabeçalhos (data, direção, de, para, assunto, nomes dos anexos) + texto do corpo.
- **Sem o histórico citado**: linhas `>` e tudo após "Em … escreveu:", "On … wrote:",
  "-----Mensagem original", bloco "De:/From:". Exceção: encaminhamento (`ENC:`/`FW:`/`Fwd:` ou
  "Forwarded message") com menos de 200 chars próprios mantém o conteúdo encaminhado.
- Corpo > 7000 chars: partes de até 5000 (quebra em parágrafo), notas por parte (máx. 8 partes;
  o resto é omitido e sinalizado) e resumo final sobre as notas (map-reduce).
- Saída (JSON Schema): `resumo` (2–5 frases), `temperatura` (`cordial | neutra | tensa |
  hostil`), `assuntos` (até 3), `pedidos_prazos`.
- Grava `resumo` + `pipeline.resumo` no `metadata.json` (só as próprias chaves). Mesma `versao`
  do prompt e mesmo modelo → reaproveitado; **refazer resumos** ignora o cache.
- Falha numa mensagem → item "resumo indisponível" no PDF, o resto segue. Ollama fora do ar →
  aborta com erro.

## Considerações
- Feitas só sobre os resumos curtos (uma linha por mensagem: nº, data, direção, de, assunto, tom,
  resumo, pedidos).
- Até 12000 chars → uma chamada. Mais que isso → blocos de até 12000, notas por bloco, repete
  até caber (consolidação hierárquica).
- Saída: `situacao` (`resolvida | em andamento | pendente | em conflito | informativa`),
  `panorama`, `conteudo`, `temperatura`, `evolucao_tom`, `pontos_atencao`, `proximos_passos`.

## Ollama
`POST /api/chat`, `temperature: 0`, `format` = JSON Schema, `num_ctx: 8192`. Resposta inválida →
1 retry. Chamadas sequenciais.

## PDF
WeasyPrint, A4: cabeçalho (seleção, período, modelo, data), aviso "gerado por IA, confira no
original", índice (nº, data, de, assunto, tom), um cartão por mensagem (com caminho relativo do
original) e Considerações em página própria. Na web abre num iframe, com "abrir em nova aba" e
"gravar PDF"; o arquivo fica no temporário do container e só o último é mantido.

## Jobs
Um resumo por vez; não roda junto com processar/reprocessar nem com limpeza/lixeira. Progresso
por polling (`/resumo/{id}/status`), cancelável entre mensagens.
