# Validatore offline

## Esiti attesi prima dell'integrazione

Entrambe le fixture selezionano `GET /items/{id}` (`getItem`), status `200` e `application/json`. Lo schema effettivo si trova in `#/components/schemas/Item`. I documenti delle fixture differiscono solo nel valore di `body.quantity`, un intero nel primo caso e una stringa sentinella nel secondo.

| Fixture | Verdetto | Codice di uscita | Evidenza attesa |
| --- | --- | --- | --- |
| `examples/item-pass.json` | `PASS` | `0` | `response.body.schema` verificata sulla risposta importata |
| `examples/item-fail.json` | `FAIL` | `1` | `response.body.schema`, campo `/quantity`, atteso `integer`, osservato `string` |

Un errore degli input o del motore esce con codice `2`. Nessun canale esporta il valore del campo sentinella, compresi i percorsi di errore.

## Prova e decisione

- `OpenAPI.from_dict` ha accettato il contratto. `OpenAPI.iter_response_errors` con `V31ResponseDataValidator` ha risolto lo schema interno e restituito zero errori per il primo caso. Il secondo ha restituito `InvalidData` con causa `InvalidSchemaValue`. Il dettaglio strutturato ha esposto percorso `quantity`, keyword `type` e tipo atteso `integer`. Il messaggio grezzo della causa conteneva la sentinella. L'adapter usa il percorso e i nomi dei tipi, senza serializzare eccezioni o valori. [API pubbliche di validazione](https://openapi-core.readthedocs.io/en/stable/validation/)
- Il resolver predefinito supporta URI esterni. Nella prova con `SchemaPath.from_dict(handlers={})` un riferimento HTTPS ha raggiunto il tentativo di apertura intercettato dal test. EndNext controlla l'intero albero prima di consegnarlo alla libreria, ammette riferimenti a frammenti dello stesso documento e rifiuta `$id` e dialetti personalizzati. Il validatore legge il metaschema OpenAPI 3.1 incluso nella sua distribuzione. `servers` non determina destinazioni: il template dichiarato nella fixture identifica esattamente l'operazione.
- L'alternativa con `openapi-schema-validator.OAS31Validator` e un `referencing.Registry` in memoria ha ottenuto gli stessi due esiti e la stessa posizione con rete bloccata. Avrebbe richiesto di gestire nell'adapter la selezione OpenAPI di operazione, status e media type. EndNext mantiene `openapi-core` per tale selezione e per la validazione degli schemi oggetto. Per gli schemi booleani alla radice, sui quali `openapi-core` 0.23.1 ha sollevato un `TypeError`, l'adapter applica direttamente la semantica di `true` e `false`.
- Un controllo senza schema del body applicabile genera un errore di input. Il primo controllo fallito tra status, media type e schema determina il risultato. La fixture contiene il body già decodificato come JSON. Per `application/json` l'adapter seleziona la dichiarazione più specifica tra media type esatto, `application/*` e `*/*` e configura il decoder JSON anche per i due intervalli.

### Versioni e licenze osservate

| Distribuzione | Versione verificata | Licenza |
| --- | --- | --- |
| `openapi-core` | 0.23.1 | BSD-3-Clause |
| `openapi-spec-validator` | 0.8.5 | Apache-2.0 |
| `openapi-schema-validator` | 0.8.1 | BSD-3-Clause |
| `jsonschema` | 4.26.0 | MIT |
| `jsonschema-path` | 0.4.6 | Apache-2.0 |
| `referencing` | 0.37.0 | MIT |
| `PyYAML` | 6.0.4 | MIT |

## Prova del percorso offline

Il test su un contratto YAML meteo con `GET /forecast` e senza `operationId` ha prodotto un PASS. Sul Mac usato per la verifica, la sequenza automatizzata da `inspect` al primo report, compresa la scrittura della fixture, ha richiesto 0,33 secondi.

Progetti selezionati per le prove di M6, da eseguire con fixture o istanze locali.

- Full Stack FastAPI Template, per risposte e schemi condivisi generati da FastAPI. [Sorgente del backend](https://github.com/fastapi/full-stack-fastapi-template/blob/master/backend/app/main.py)
- Litestar Fullstack, per contratti generati da controller Litestar. [Applicazione di riferimento](https://github.com/litestar-org/litestar-fullstack)
- Tutorial Django Ninja, per operazioni con parametri, schemi di risposta e requisiti di accesso. [Guida del progetto](https://github.com/vitalik/django-ninja/blob/master/docs/docs/tutorial/index.md)
