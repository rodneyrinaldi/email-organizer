# IA

Ollama remoto (outro computador na rede). URL e modelo em `config.json`. Sem Ollama embutido na imagem.

## Regras
- IA nunca controla fluxo, caminho ou exclusão. Só grava no `metadata.json`.
- Indisponível/timeout → `pipeline.classificacao.status: pendente`; reprocessável depois.
- Resposta sempre JSON (`format: json` do Ollama) validada por Pydantic; inválida → 1 retry, depois `erro`.
- Registrar `modelo` e data.

## Classificação
Entrada: assunto, remetente, primeiros N chars do texto, nomes dos anexos.
Saída: `{ "categoria", "subcategoria", "confianca" }`. Categoria fora da lista → `Outros`.

Categorias iniciais: Banco, Seguros, Imóveis, Contratos, Procurações, Trabalhista, Tributário, Processo Judicial, Cartão de Crédito, Outros.

## Thread (fallback)
Só quando não há `In-Reply-To`/`References`: IA infere `nova | resposta | encaminhamento` por assunto (`RE:`, `ENC:`, `FW:`) e corpo citado. `metodo: ia`. Heurística de prefixo antes da IA.

## Execução
- `processar`: depois da captura e dos artefatos, classifica as mensagens do job sem `pipeline.classificacao.status: ok`. `--sem-classificacao` pula.
- `python -m email_organizer classificar [--todas]`: classifica pendentes/erros de todo o repositório (sem acessar caixa postal); `--todas` reclassifica tudo.
- Desligada se `ollama.habilitado` ou `opcoes.classificacao` for `false`.
- Requisição: `POST /api/chat`, `stream: false`, `temperature: 0`, `format` = JSON Schema da resposta. Texto do corpo limitado a 3000 caracteres.
- Primeira falha de conexão no lote → demais ficam `pendente` sem chamar o Ollama. Relatório mostra `ok/pendente/erro`.
- Thread: a mesma chamada devolve `tipo` quando não há pai por cabeçalho nem prefixo no assunto; grava `thread.metodo: ia`.

## Futuro
Entidades, resumo, OCR etc. seguem o mesmo padrão: nova chave no JSON, nova etapa em `pipeline`.
