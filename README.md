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
