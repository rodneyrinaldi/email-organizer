# Interface

`http://localhost:8080`. FastAPI + Jinja2 + HTMX + Tailwind (CDN, versão fixa). Sem login. Sem SPA.

## Feedback
- Botões mostram spinner e ficam desabilitados durante a requisição (`hx-disabled-elt`).
- Job: spinner, etapas (captura → artefatos → classificação), contadores, barras de progresso de artefatos e IA (feito/total), tempo decorrido.
- Fim do job: banner (concluído / com erros / cancelado / falhou) + toast uma única vez + "Últimos jobs" atualizado (OOB).
- Avisos e validações (sem fonte, job em andamento, índice) viram toast sem trocar o painel (`HX-Reswap: none`).
- Falha HTTP/rede: toast de erro via JS (`htmx:responseError`/`htmx:sendError`).

## Menu
Processar · Rotinas: **Limpeza**, **Lixeira** (ver [limpeza.md](limpeza.md)).

## Tela principal
- Fontes: checkboxes por conta IMAP/Graph e por PST (todas do `config.json`; ativas vêm marcadas).
- Pastas: carregadas sob demanda por fonte (HTMX). Marcadas na tela substituem as do config só naquele job (filtros da pasta no config preservados).
- Destino: raiz do output (fixo; `OUTPUT_DIR`).
- Opções: PDF principal, Markdown, Classificação IA (sobrescrevem `opcoes` só no job).
- Botão **Processar**.
- Ação **Reprocessar** (a partir do `.eml` salvo; etapas `artefatos`/`classificacao`; padrão só as ≠ ok, "também as já ok" refaz tudo).
- Ação **Reconstruir índice**.
- Últimos 10 jobs (de `_jobs/*/relatorio.json`).

## Jobs
- Um job por vez (lock em memória; um processo uvicorn). Thread background.
- Progresso via polling HTMX (`/jobs/{id}/status` a cada 2s): fase (captura/artefatos/classificacao), fonte/pasta atual, lidas, novas, duplicadas, filtradas, erros, contagem da IA. Sem n/total (IMAP/Graph não informam barato).
- Cancelar: flag cooperativa entre mensagens.
- Estado e log em `/output/_jobs/<job-id>/`.

## Relatório final
`relatorio.json` + `relatorio.md`: totais por fonte/pasta, novas, duplicadas, ignoradas (zip), falhas de conversão, IA pendente, lista de avisos com caminho da mensagem. Exibido na UI ao terminar.

## Graph auth
Botão "Conectar" por conta Graph → exibe código device flow e URL; token salvo em `<ESTADO_DIR>/tokens/`. Polling a cada 3s até concluir.
