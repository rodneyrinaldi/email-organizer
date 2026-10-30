# Componentes de terceiros

O código deste repositório está sob a [licença MIT](LICENSE). Os componentes abaixo **não** são
cobertos por ela: cada um mantém a própria licença, e **cabe a quem usa, modifica ou distribui este
projeto (inclusive a imagem Docker) verificar se o uso pretendido é compatível com elas**.

> [!WARNING]
> Esta lista é informativa, foi montada a partir dos metadados dos pacotes na data da publicação e
> pode estar incompleta ou desatualizada. Ela **não** é aconselhamento jurídico. Confira sempre a
> licença vigente na fonte oficial de cada projeto, inclusive das dependências transitivas.

## Pontos de atenção

| Componente | Licença | Por que importa |
|---|---|---|
| **PyMuPDF** | AGPL-3.0 **ou** licença comercial Artifex | Copyleft forte, que alcança inclusive o uso em rede (SaaS). Distribuir ou oferecer como serviço pode exigir liberar o código sob AGPL ou adquirir a licença comercial. |
| **extract-msg** | GPL-3.0 | Copyleft: distribuir uma obra combinada pode exigir GPL. |
| **readpst / libpst** (`pst-utils`) | GPL-2.0+ | Executado como programa externo dentro da imagem; a imagem redistribui o binário. |
| **tnefparse**, **RTFDE** | LGPL-3.0 | Copyleft fraco; atenção ao redistribuir versões modificadas. |
| **LibreOffice** | MPL-2.0 (e outras) | Executado como programa externo dentro da imagem. |
| **Modelos de IA** (ex.: `qwen2.5`) | licença de cada modelo | Baixados à parte no Ollama; alguns têm restrições de uso. |

## Pacotes Python (diretos)

| Pacote | Licença |
|---|---|
| fastapi | MIT |
| uvicorn | BSD-3-Clause |
| jinja2 | BSD-3-Clause |
| python-multipart | Apache-2.0 |
| pydantic | MIT |
| httpx | BSD-3-Clause |
| weasyprint | BSD-3-Clause |
| pymupdf | AGPL-3.0 / comercial |
| markdownify | MIT |
| msal | MIT |
| tnefparse | LGPL-3.0 |
| extract-msg | GPL-3.0 |
| pytest, ruff (desenvolvimento) | MIT |

Dependências transitivas (ex.: RTFDE, beautifulsoup4, cryptography) têm licenças próprias; liste-as
com `pip-licenses` ou `pip show <pacote>` no ambiente instalado.

## Imagem Docker

| Componente | Licença |
|---|---|
| Imagem base `python:3.12-slim` (Debian) | PSF-2.0 + licenças dos pacotes Debian |
| LibreOffice (writer, calc, impress) | MPL-2.0 |
| pst-utils (readpst/libpst) | GPL-2.0+ |
| Pango, HarfBuzz | LGPL-2.0+ / MIT |
| Fontes DejaVu, Liberation | licença livre própria / SIL OFL-1.1 |

## Interface web (carregados via CDN no navegador)

| Componente | Licença |
|---|---|
| HTMX | 0BSD |
| Tailwind CSS (CDN) | MIT |

## Serviços externos

Gmail/Google, Hostinger, Microsoft Graph/Entra e Ollama são acessados pelas suas APIs e regidos
pelos respectivos termos de uso. Este projeto não é afiliado nem endossado por nenhum deles.
