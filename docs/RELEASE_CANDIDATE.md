# Verifiche M6 di EndNext

Le verifiche iniziali sono state eseguite sulla candidata `0.1.0rc1`. L’integrazione in `main` mantiene la versione di sviluppo `0.1.0.dev0`.

## Prove esterne

Le tre prove usano adattamenti minimi degli esempi ufficiali di progetti indipendenti. I test client in-process hanno prodotto l'osservazione HTTP. EndNext ha poi letto il contratto generato e la fixture salvata su disco. I tempi misurano l'export del contratto, `inspect`, la cattura della risposta nella fixture e il primo report.

| Progetto | OpenAPI | Operazione verificata | Osservazione | Risultato | Tempo |
| --- | --- | --- | --- | --- | --- |
| FastAPI 0.165.0 | 3.1.0 | `GET /items/{item_id}` | 422 per un parametro intero invalido, con schema di errore documentato | 4 PASS | 0,84 s |
| Litestar 2.25.0 | 3.1.0 | `GET /user/{user_id}` | 200 con risposta tipizzata e schema riusato nei componenti | 4 PASS | 0,77 s |
| Django Ninja 1.8.0 | 3.1.0 | `GET /api/add` | 200 con parametri query e schema di risposta dichiarato | 4 PASS | 0,72 s |

Ogni contratto conteneva un'operazione. Il piano ha riportato un caso eseguibile e zero skip per prova. Le prove coprono tre esempi pubblici circoscritti e usano contratti OpenAPI 3.1 generati dai framework. [FastAPI, parametri di path](https://fastapi.tiangolo.com/tutorial/path-params/) · [Litestar, parametri](https://docs.litestar.dev/2/usage/routing/parameters.html) · [Django Ninja, quick example e schemi](https://django-ninja.dev/guide/schemas/)

## Distribuzione

La candidata iniziale ha superato la suite e l’installazione da wheel e sdist su macOS e Linux. [Esecuzione iniziale su GitHub Actions](https://github.com/Minells/EndNext/actions/runs/45673906879)

Il workflow **Main CI** ripete queste verifiche per le pull request verso `main`. Per riprodurle, segui il [quickstart](../README.md#quick-start), esegui `python -m build` con il pacchetto `build` installato e installa ogni artefatto in un ambiente pulito usando `python -m pip install <artefatto>`.

Il 25 novembre 2026 l'API PyPI ha restituito HTTP 404 per il progetto `endnext`. Il risultato registra lo stato osservato al momento della prova. [Progetto PyPI](https://pypi.org/project/endnext/)

### Licenze runtime osservate

La tabella riporta versioni e licenze nell'ambiente Python 3.12 usato per la prova. Wheel e sdist di EndNext dichiarano queste dipendenze e includono la licenza MIT del progetto.

| Licenza | Distribuzioni verificate |
| --- | --- |
| MIT | annotated-types 0.8.0, anyio 4.16.0, attrs 26.1.0, h11 0.16.0, jsonschema 4.26.0, jsonschema-specifications 2025.9.1, more-itertools 11.2.1, pydantic 2.14.4, pydantic-core 2.50.5, pydantic-settings 2.16.3, PyYAML 6.0.4, referencing 0.37.0, rfc3339-validator 0.1.4, rpds-py 2026.9.1, six 1.17.0, typing-inspection 0.4.5 |
| BSD-3-Clause | httpcore 1.0.9, HTTPX 0.28.1, idna 3.20, isodate 0.7.2, MarkupSafe 3.0.4, openapi-core 0.23.1, openapi-schema-validator 0.8.1, python-dotenv 1.2.4, Werkzeug 3.1.9 |
| BSD-2-Clause | lazy-object-proxy 1.13.0 |
| Apache-2.0 | jsonschema-path 0.4.6, openapi-spec-validator 0.8.5, pathable 0.5.0 |
| MPL-2.0 | certifi 2026.11.20 |
| PSF-2.0 | typing-extensions 4.16.0 |

## Riesame della milestone

Il 26 novembre 2026 il proprietario ha approvato l’integrazione di M6 sulla base delle prove documentate. La prova del quickstart da parte di un maintainer indipendente è stata omessa su sua indicazione. [Piano di implementazione M6](IMPLEMENTATION_PLAN_V0.1.md#milestone-e-verifiche-di-uscita)
