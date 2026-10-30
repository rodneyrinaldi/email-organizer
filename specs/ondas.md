# Ondas

Cada onda: spec revisada → testes → código → validação → atualizar status aqui. Próxima onda só com autorização do usuário.

| # | Onda | Status |
|---|---|---|
| 0 | Infra do projeto (specs, skills, CLAUDE.md, git, tooling) | ✅ concluída |
| 1 | Núcleo: modelos Pydantic do metadata, repositório (paths, escrita atômica), dedup, índice, captura de `.eml` → diretório + original | ✅ concluída |
| 2 | Artefatos: parse MIME, PDF principal, Markdown, corpo, anexos (LibreOffice/PyMuPDF/WeasyPrint), e-mails aninhados, hashes | ✅ concluída |
| 3 | Fontes: PST (readpst) e IMAP (Gmail + advogado@), runner + CLI, artefatos paralelos | ✅ concluída (IMAP real pendente: senha de app) |
| 4 | Fonte Graph (Hotmail/365, device flow) | ✅ concluída (real validado: conectar + pastas) |
| 5 | IA: cliente Ollama, classificação, thread fallback | ✅ concluída (validado no Ollama real) |
| 6 | Interface web, jobs, relatório, reprocessamento | ✅ concluída |
| 7 | Docker final, casos extremos (TNEF, S/MIME, charset), validação ponta a ponta com PSTs reais | ✅ concluída (E2E com PSTs reais) |

## Critério de pronto (todas)
- `pytest` e `ruff` verdes.
- Spec atualizada se o comportamento mudou.
- Nenhuma escrita na origem.

## Fontes do MVP
- IMAP: Gmail (senha de app) e caixa de domínio próprio (Hostinger).
- Graph: conta pessoal Microsoft (Hotmail/Outlook.com).
- PST: vários GB, com pares sobrepostos (`X.pst`/`X-copia.pst`) → dedup obrigatória.

