# EndNext · Dal test API alla pull request

Il percorso parte da una risposta acquisita da un test esistente. Una suite
offline produce la baseline e l’evidenza corrente. La CI mostra quali
asserzioni sono peggiorate e quali operazioni hanno verifiche incomplete.

| Milestone | Dipendenze | Criteri di uscita |
| --- | --- | --- |
| [M1 · Verdetti affidabili](https://github.com/Minells/EndNext/milestone/1) | Validatore e report esistenti | Ogni aspettativa esplicita produce un’asserzione o un errore di configurazione. Fixture e acquisizione concordano su HEAD, 204, 205, 304 e media type JSON strutturati. |
| [M2 · Suite offline](https://github.com/Minells/EndNext/milestone/2) | M1, piano TOML e API offline | Un solo caricamento del contratto produce un report con nomi stabili. Due fixture dimostrano una regressione e il caso misto con una fixture mancante. Il benchmark misura il riuso sullo stesso dataset. |
| [M3 · Evidenza nella pull request](https://github.com/Minells/EndNext/milestone/3) | M2, diff ed eccezioni esistenti | Il riepilogo CI rende visibili regressioni e verifiche incomplete, con copertura per operazione e artefatto JSON. Report precedenti, fingerprint ed exit code restano compatibili. |
| [M4 · Adozione da progetti reali](https://github.com/Minells/EndNext/milestone/4) | M2 e M3 | Un test HTTPX alimenta la suite. Il corpus offline copre tre framework e due prove tecniche complete. Wheel e sdist superano il quickstart in un ambiente pulito. I maintainer indipendenti completano le prove di adozione. |

Il lavoro operativo è tracciato nelle issue e nelle pull request delle milestone.
Le prove tecniche e la [validazione con maintainer indipendenti](https://github.com/Minells/EndNext/issues/29)
hanno evidenze distinte. La candidata è verificata localmente prima di ogni
decisione di pubblicazione.

Il flusso offline conserva i limiti sulle risorse e la redazione delle
credenziali. Il traffico live rimane entro lo scope autorizzato. La CI continua
a bloccare difformità nell’audit delle dipendenze. Il
[backlog per più errori di schema](https://github.com/Minells/EndNext/issues/24)
richiede una decisione sull’identità dei finding prima dell’implementazione.

La [visione di prodotto](PRODUCT_PLAN.md) conserva le motivazioni di lungo
periodo. Il [piano V0.1](IMPLEMENTATION_PLAN_V0.1.md) documenta il primo
incremento e le sue verifiche.
