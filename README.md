# EndNext
Open-source API assurance toolkit for contract validation, robustness testing, and security analysis.

L'MVP offline elenca le operazioni di un contratto OpenAPI 3.1 locale e verifica una risposta JSON già acquisita, anche a servizio spento.

## Installazione e demo

Dal checkout, con Python 3.12 o successivo.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install '.[dev]'

.venv/bin/endnext inspect examples/items.openapi.json
.venv/bin/endnext check examples/items.openapi.json examples/item-pass.json
.venv/bin/endnext check examples/items.openapi.json examples/item-fail.json --format json
.venv/bin/python -m pytest
```

Se ti serve solo la CLI, installa `.` senza l'extra `dev`. Usa `--format json` con `inspect` o `check` per ottenere un documento su stdout.

| Comando e risultato | Codice di uscita |
| --- | --- |
| `inspect` completato o `check` con `PASS` | `0` |
| `check` con `FAIL` | `1` |
| Errore di input o di esecuzione | `2`, con diagnostica su stderr |

La demo verifica `GET /items/{id}`. Il secondo caso indica `response.body.schema` e `/quantity`, con tipo atteso `integer` e tipo osservato `string`.

## Verifica una risposta tua

1. Esegui `endnext inspect contratto.yaml` per leggere metodo, path template, media type e dichiarazioni di sicurezza del contratto. Puoi usare un file JSON, `.yaml` o `.yml`.
2. Copia `examples/item-pass.json`. Imposta `case`, `method` e `path_template` usando il template esatto di `inspect`. Se il template contiene parametri, indica i valori scalari corrispondenti in `path_parameters`. Il campo è facoltativo per un path senza parametri.
3. Riporta lo status osservato in `status`, imposta `media_type` a `application/json` e inserisci il JSON osservato come valore di `body`. Esegui `endnext check contratto.yaml risposta.json`.

Il report conserva operazione, provenienza, status e tipo JSON richiesto per spiegare un'eventuale discrepanza. Il body, i valori dei parametri e i messaggi grezzi del validatore restano fuori dal report. Usa un nome di caso e di file adatto a essere condiviso.

Il caricamento accetta file locali fino a 1 MiB, riferimenti nello stesso documento e YAML compatibile con JSON. In YAML racchiudi tra virgolette le chiavi numeriche, come `'200'`. `check` verifica una risposta JSON documentata per volta. `inspect` mostra separatamente le dichiarazioni di sicurezza del contratto e delle operazioni. [Decisione sul validatore e limiti](docs/VALIDATOR.md)

## Piano offline

```sh
.venv/bin/endnext plan examples/items.openapi.json examples/items-plan.toml --format json
```

In `select` indica le operazioni usando metodo e path template. Ogni voce `[[cases]]` ha un nome univoco, un'operazione e una sorgente. Per `source = "fixture"`, il percorso della fixture è relativo al TOML. Per `source = "http"`, specifica un GET positivo, lo status atteso e gli eventuali parametri scalari di path e query. Il budget HTTP va da 0 a 20 e parte da 0.

`plan` elenca anche le operazioni senza casi. Mostra la sicurezza effettiva, il motivo degli skip, il costo in richieste e le asserzioni candidate con le relative condizioni. Le richieste sono stimate e le asserzioni candidate non costituiscono risultati di verifica. A parità di input il piano è identico, anche in assenza di rete.

## Verifica HTTP su loopback

Per usare `scan`, aggiungi al TOML la destinazione di un servizio di prova locale per cui hai autorizzazione. Il path del contratto deve ricadere nel prefisso autorizzato.

```toml
[target]
authorized = true
origin = "http://127.0.0.1:8000"
path_prefix = "/items"
```

```sh
endnext plan examples/items.openapi.json suite.toml
endnext scan examples/items.openapi.json suite.toml --format json
```

Su POSIX, `scan` esegue solo i GET positivi e anonimi selezionati. L'origine richiede un indirizzo IP letterale di loopback e una porta esplicita. Il trasporto limita l'esecuzione a una richiesta al secondo, 20 tentativi, 5 secondi per richiesta, 60 secondi complessivi di acquisizione e 1 MiB di body conservato per risposta. Redirect, retry, proxy d'ambiente e propagazione dei cookie sono disattivati. HTTPS verifica il certificato. Il report distingue i tentativi dagli invii completati degli header HTTP. Lo staging è fuori dal perimetro del trasporto corrente. [Trasporto HTTPX](https://www.python-httpx.org/advanced/transports/) · [Tracciamento degli invii HTTP](https://www.python-httpx.org/advanced/extensions/#trace)

## Asserzioni ed evidenze

`check` e `scan` valutano separatamente status documentato, media type, schema ed eventuale status atteso. Una risposta incompleta mantiene le asserzioni supportate dai dati disponibili. Nelle fixture, `body_state` può indicare `absent`, `truncated` o `invalid`. Ometti `body` in questi casi. Uno status HTTP 204 può essere registrato senza body o media type.

Per una fixture con `identity: "anonymous"`, `expected_access` permette di confrontare l'osservazione importata con l'esito dichiarato. Con `sensitive: true`, `expected_cache_directive` valuta la presenza del nome di direttiva in `headers.Cache-Control`. La demo produce asserzioni di schema superate, una discrepanza di accesso, un avviso relativo alla cache e uno skip per aspettativa HTTP assente.

```sh
.venv/bin/endnext check examples/items.openapi.json examples/item-policy-fail.json --format json > evidence.json
.venv/bin/endnext report evidence.json
.venv/bin/endnext report evidence.json --format markdown
.venv/bin/python -m pytest -q tests/test_reports.py tests/test_scan.py
```

Il primo comando esce con codice 1, con evidenza valida in `evidence.json`. Il report JSON v2 conserva tutte le asserzioni. `report` rilegge anche i documenti v1 e mantiene verdetti, provenienza e conteggi nei formati terminale e Markdown. I casi di body parziale e timeout nella suite producono rispettivamente `INCONCLUSIVE` e `ERROR`, con codice 2.

## Confrontare due esecuzioni

Salva i report JSON di due esecuzioni della stessa suite e confrontali offline. L'esempio registra una regressione nello schema di risposta. Il secondo `check` e il comando `diff` escono con codice 1.

```sh
.venv/bin/endnext check examples/items.openapi.json examples/item-pass.json --format json > baseline.json
.venv/bin/endnext check examples/items.openapi.json examples/item-fail.json --format json > current.json
.venv/bin/endnext diff baseline.json current.json --format json
```

`diff` legge report di `check` e `scan` v1 e v2. Abbina metodo, path template, nome del caso, tipo di provenienza e regola. Il fingerprint include anche la posizione del campo, così un FAIL che si sposta su un altro campo emerge come nuovo. Un PASS della stessa regola conferma la risoluzione. Se un controllo già eseguito risulta assente, saltato o inconcludente, appare come non verificato. Mantieni stabili i nomi dei casi tra le esecuzioni.

| Esito del confronto | Codice di uscita |
| --- | --- |
| Almeno un controllo confrontabile, senza nuovi FAIL o verifiche mancanti rispetto alla baseline | `0` |
| Almeno un nuovo FAIL, anche in presenza di verifiche mancanti | `1` |
| Input non valido, confronto senza controlli confrontabili o verifica incompleta | `2` |

Gli avvisi e i FAIL già presenti sono visibili nel riepilogo e non bloccano da soli il confronto.

## Test e integrazione in main

```sh
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest --cov --cov-report=term-missing --cov-report=xml
```

La soglia del 96% combina righe e rami e include le invocazioni della CLI in processi separati. Per integrare un branch, apri una pull request verso `main` e attendi il check obbligatorio **EndNext merge gate**. La CI esegue Ruff, mypy, la suite con coverage e una prova da wheel e sdist su Linux e macOS con Python 3.12. I report di copertura XML sono disponibili tra gli artefatti del workflow.
