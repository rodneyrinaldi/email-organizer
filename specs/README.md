# Specs

| Arquivo | Conteúdo |
|---|---|
| [arquitetura.md](arquitetura.md) | Camadas, pipeline, estrutura de diretórios |
| [metadata.md](metadata.md) | Schema do `metadata.json` (fonte de verdade) |
| [config.md](config.md) | `config.json` na raiz do output |
| [fontes.md](fontes.md) | IMAP, Graph, PST |
| [artefatos.md](artefatos.md) | PDF principal, Markdown, corpo, anexos, e-mails aninhados |
| [ia.md](ia.md) | Classificação e enriquecimento via Ollama |
| [interface.md](interface.md) | UI web, jobs, relatório |
| [limpeza.md](limpeza.md) | Rotina de limpeza e lixeira (`.lixeira/`) |
| [ondas.md](ondas.md) | Roadmap e status |
| [decisoes.md](decisoes.md) | ADRs |

## Princípios
1. Simples, direto, fácil de manter.
2. Arquivos acessíveis fora do container (volumes).
3. Processamento sob demanda; sem agendamentos.
4. Sem dependência do Outlook Desktop nem da origem após a captura.
5. Arquitetura orientada a arquivos e JSON.
6. Reprocessável: tudo deriva de `original/mensagem.eml`.
7. Evidência: cópia bruta + SHA-256 de todo artefato + rastreio de origem.
8. Expansão por rotinas: ler `metadata.json` → enriquecer → gerar artefatos.
