# EndNext · Piano di implementazione V0.1

Documento operativo del 25 novembre 2026. La [visione di prodotto](./PRODUCT_PLAN.md) descrive la roadmap di lungo periodo. Questo piano sceglie il primo percorso da realizzare, i criteri per accettarlo e la sequenza per arrivare alla release. La disponibilità prevista è di 6–10 ore settimanali per un maintainer.

## Avvio della prossima sessione

Aprire il checkout che contiene `README.md`, `LICENSE` e `docs/PRODUCT_PLAN.md`. Selezionare **6.2 Sol** con **effort High**. Incollare il prompt seguente.

> Leggi integralmente docs/PRODUCT_PLAN.md e docs/IMPLEMENTATION_PLAN_V0.1.md. Implementa soltanto M1, dal checkout attuale fino alla dimostrazione dalla CLI. Controlla prima lo stato di Git e preserva il lavoro già presente. Usa i due casi a esito noto e la prova con rete bloccata per verificare la scelta del validatore. Completa la milestone, esegui i test e riportami file modificati, comandi da ripetere dal checkout, risultati, limiti verificati e criteri di accettazione eventualmente aperti. Fermati al riesame di M1. M2 partirà con la mia conferma.

Al momento della stesura il checkout locale contiene un README essenziale e una licenza MIT. La directory `docs/` risulta non tracciata. I due piani vanno inclusi tra i file di progetto da versionare, preservando il testo della visione. Il punto di partenza effettivo va ricontrollato all'avvio.

## Percorso utente e perimetro

Il primo utilizzatore è il maintainer di un’API REST. Può partire da una risposta sintetica già prodotta dalla propria suite, anche se il servizio è spento. L’MVP offline è utilizzabile alla fine di M2. La release V0.1 aggiunge pianificazione, acquisizione HTTP controllata, due controlli di sicurezza circoscritti e report condivisibili.

| Comando previsto | Input e risultato |
|---|---|
| `inspect` | Da un contratto OpenAPI **3.1.x** locale, JSON o YAML con riferimenti interni, elenca le operazioni di `paths`, i media type e la sicurezza dichiarata. Metodo e path template identificano un’operazione anche in assenza di `operationId`. |
| `check` | Da contratto e fixture locale produce una verifica offline. La fixture identifica caso e operazione, parametri sintetici, status, media type, body osservato e aspettativa del proprietario quando disponibile. |
| `plan` | Incrocia casi, inventario e policy senza rete. Per ogni caso espone regole applicabili, provenienza prevista, motivi di esclusione e costo in richieste. |
| `scan` | Acquisisce osservazioni dai soli casi positivi GET autorizzati. Parametri path/query scalari espliciti. Primo target supportato: loopback. Lo staging HTTPS dipende dalle prove di M4. |
| `report` | Rilegge il JSON versionato e rende terminale o Markdown offline. Conserva verdetti, provenienza e conteggi del documento originale. |

Il perimetro successivo comprende OpenAPI 3.0, contratti multifile o da URL, autenticazioni attive, altri metodi HTTP, baseline tra release e integrazioni CI per gli utenti. Generazione automatica di casi, scenari multiutente, fault injection e plugin pubblici restano nel backlog del prodotto. Il primo incremento utile per altri stack applicativi è già la fixture indipendente dal linguaggio del servizio.

## Architettura e contratti interni

Un pacchetto Python, un processo, dati locali. Creare ogni modulo quando entra nel percorso utente. Per M1 bastano lettura, validazione, evidenza e un ingresso CLI minimo.

| Responsabilità | Decisione |
|---|---|
| Lettura e inventario | Caricare una sola volta il contratto in memoria. Accettare solo il dialetto supportato e riferimenti interni. Distinguere documento invalido, caratteristica non supportata e fixture assente. Imporre limiti al caricamento di input non fidati. |
| Validazione | Usare le API pubbliche di `openapi-core`. Provare selezione della risposta e validazione del body nel contesto dell’operazione. Usare direttamente `openapi-spec-validator` soltanto se occorre un ingresso distinto per verificare il documento. In M1 registrare versioni testate, licenze e motivazione. |
| Pianificazione | Funzione deterministica su inventario, casi e policy. Una richiesta può alimentare più asserzioni. Il planner espone il costo prima dell’esecuzione. |
| Acquisizione | Fixture e HTTP producono lo stesso tipo di osservazione, con provenienza distinta. Il trasporto HTTPX sincrono è l’unico punto di invio. Verifica scope e budget anche al momento dell’invio. |
| Verifica | Le regole ricevono osservazione, contratto e aspettativa. Status documentato, conformità del body ed esito atteso dal proprietario sono asserzioni indipendenti. Un 500 documentato può superare il controllo di schema e violare l’aspettativa del caso. |
| Evidenze e rendering | Trasformare immediatamente gli esiti in dati esportabili. Il renderer riceve solo il modello sanitizzato. CLI con `argparse`, config TOML, modello con tipi standard Python. Nessun database richiesto. |
| Qualità e distribuzione | Python 3.12 come minimo iniziale, `pyproject.toml`, pytest, Ruff e un solo type checker. Separare strumenti di sviluppo e dipendenze runtime. Provare wheel e sdist in ambienti puliti. |

Riutilizzare la validazione di schema e il trasporto esistenti. Schemathesis è un candidato da riesaminare se la generazione di casi diventa una necessità dimostrata. Per le fixture esplicite il primo esperimento riguarda il validatore più circoscritto. [openapi-core](https://openapi-core.readthedocs.io/en/stable/validation/) · [OpenAPI Spec Validator](https://openapi-spec-validator.readthedocs.io/en/latest/) · [Specifica OpenAPI 3.1](https://spec.openapis.org/oas/v3.1.1.html) · [HTTPX](https://www.python-httpx.org/api/) · [Schemathesis](https://schemathesis.readthedocs.io/en/stable/)

### Modello minimo del risultato

Il JSON registra versione dello schema, versione del motore, identificativo di esecuzione, digest del contratto, caso, metodo, path template, provenienza, regola, aspettativa, osservazione sanitizzata, verdetto e motivazione. Il replay usa il riferimento alla fixture locale versionata. Per confrontare risultati deterministici nei test, isolare identificativi di esecuzione e tempi.

| Stato | Esempio di interpretazione |
|---|---|
| PASS | La risposta sintetica contiene il campo richiesto con il tipo previsto. |
| FAIL | Il campo ha un tipo incompatibile. L’evidenza indica campo, aspettativa e discrepanza. |
| WARN | Una risposta marcata sensibile dal proprietario è priva della direttiva di cache attesa. |
| SKIPPED | Un controllo applicabile richiede una fixture mancante o una richiesta esclusa dalla policy. |
| INCONCLUSIVE | Il limite di lettura interrompe il body e impedisce di concludere sulla conformità dello schema. Lo status ricevuto resta valutabile. |
| ERROR | Un timeout o un errore del runner impedisce di acquisire l’osservazione prevista. |

Separare gli esiti del servizio dagli errori degli input e dello strumento. Il riepilogo espone operazioni inventariate, casi pianificati, richieste inviate e asserzioni per stato. Dichiarare sempre se un’osservazione proviene dalla suite del proprietario o dall’esecuzione corrente.

## Milestone e verifiche di uscita

### M1 · Primo risultato offline · 12–16 ore

**Dipendenza.** Checkout accessibile. La milestone richiede soltanto un contratto JSON locale e il formato minimo di fixture.

**Prime otto ore previste**

1. **1 h.** Controllare stato Git e documentazione, predisporre il pacchetto installabile dal checkout e annotare le decisioni iniziali.
2. **2 h.** Preparare un contratto sintetico con una sola operazione e uno schema referenziato internamente. Esempio: GET su `/items/{id}`, risposta 200 JSON con campo `quantity` intero. Preparare due fixture identiche eccetto il tipo di quel campo. Nella seconda usare una stringa sentinella riconoscibile. Scrivere gli esiti attesi prima di integrare la libreria.
3. **3 h.** Verificare che il validatore accetti il documento, selezioni l’operazione corretta, distingua le due risposte e restituisca una posizione del campo utilizzabile. Eseguire la prova con rete bloccata. Verificare anche che riferimenti a URL e ad altri file ricevano una diagnostica senza accessi esterni.
4. **2 h.** Collegare il percorso CLI minimo `check` al modello di evidenza. Produrre JSON sanitizzato e rieseguire entrambe le fixture attraverso il comando.

**Completamento.** Usare le 4–8 ore residue per i test di regressione, l’installazione dal checkout, le istruzioni minime per ripetere la demo e una breve nota sul validatore effettivamente scelto.

**Accettazione**

- [ ] La stessa operazione dà PASS sulla prima fixture e FAIL sulla seconda con regola, percorso del campo e motivazione leggibili.
- [ ] Lo stato di fallimento del controllo è distinguibile da un input illeggibile o da un errore interno.
- [ ] L’output identifica l’osservazione come importata. La stringa sentinella è assente da JSON, stdout e stderr anche nel percorso d’errore.
- [ ] I test automatici dimostrano risoluzione interna, rifiuto dei riferimenti esterni e funzionamento con rete bloccata.
- [ ] Un’altra sessione può installare il pacchetto, ripetere i due comandi e ottenere gli esiti documentati dal checkout.

**Rischio e decisione.** Se le API pubbliche espongono solo messaggi contenenti dati grezzi o attivano risoluzioni implicite, valutare un adapter alternativo usando gli stessi due casi. Un limite irrisolto su offline o sanitizzazione blocca il passaggio a M2.

### M2 · MVP offline utilizzabile · 6–8 ore

**Dipende da M1.** Completare `inspect` e `check` per un contratto e una fixture, aggiungere YAML con loader sicuro, messaggi CLI e quickstart. Documentare gli esiti del comando per verifica superata, asserzione fallita ed errore di esecuzione.

**Deliverable e accettazione.** Dal pacchetto installato localmente un maintainer elenca le operazioni e verifica una risposta propria seguendo il quickstart. JSON e YAML equivalenti danno lo stesso esito. Un’operazione senza `operationId` rimane utilizzabile.

**Test e rischio.** Eseguibile installato, contratto malformato, operazione inesistente, versione non supportata, YAML con tag non ammessi e input oltre limite. Il rischio è una fixture costosa da preparare. Misurare il tempo al primo report su un esempio diverso dalla demo e selezionare tre progetti per M6.

### M3 · Inventario e piano offline · 12–16 ore

**Dipende da M2.** Introdurre più casi, precondizioni, selezione esplicita delle operazioni, budget e motivi di skip. Interpretare i requisiti di sicurezza di documento e operazione, inclusa la possibilità di accesso anonimo. Rendere `plan` disponibile senza trasporto.

**Deliverable e accettazione.** Tutte le operazioni di `paths` sono visibili, comprese quelle senza fixture. Due piani con gli stessi input coincidono per contenuto e ordine. Per ciascun caso si vede se richiede una chiamata e quanti controlli può alimentare.

**Test e rischio.** Override di sicurezza, fixture mancante, riferimenti condivisi, più controlli sulla stessa osservazione e blocco della rete. Verificare i conteggi su un inventario a esito noto. Il rischio è sovrastimare la copertura quando molte regole leggono la stessa risposta.

### M4 · Esecuzione HTTP controllata · 12–16 ore

**Dipende da M3.** Collegare `scan` a HTTPX e a un server locale che conti le richieste. Richiedere autorizzazione dichiarata dal proprietario, origine, porta, prefisso e operazioni selezionate. La sezione `servers` del contratto è descrittiva. Ricontrollare la destinazione finale al momento dell’invio.

**Profilo iniziale.** GET con casi positivi espliciti, concorrenza 1, massimo 1 richiesta/s, 20 invii per esecuzione, 5 secondi per richiesta, 60 secondi complessivi e 1 MiB letto per risposta. Redirect, retry e proxy d’ambiente disabilitati. TLS verificato per HTTPS. HTTP in chiaro solo su loopback. Nessun cookie propagato tra casi.

**Deliverable e accettazione.** Su un server di prova autorizzato gli invii effettivi corrispondono al report. Configurazione incompleta, operazione esclusa e budget esaurito impediscono ulteriori invii. Il supporto staging HTTPS richiede evidenza che la connessione effettiva rimanga nella destinazione autorizzata anche al variare della risoluzione DNS. Se questa prova resta aperta, dichiarare supporto live limitato a loopback e riportare il limite nel riesame.

**Test e rischio.** Usare listener locali per provare redirect, porta e prefisso errati, proxy d’ambiente, ritardi, limite del body e cookie. Testare TLS e vincolo sulla destinazione prima di abilitare staging. Il rischio principale è la divergenza tra destinazione pianificata e contattata.

### M5 · Regole ed evidenze condivisibili · 12–16 ore

**Dipende da M3 e M4.** Completare controlli distinti per status, media type, schema ed esito atteso. Gestire 204, risposte per intervallo e `default`. Per l’error handling di input negativi usare osservazioni già prodotte dalla suite del proprietario. Per l’accesso anonimo confrontare soltanto un caso importato con identità e aspettativa dichiarate. Per la cache verificare la direttiva attesa su una risposta marcata sensibile. Completare `report` e il rendering Markdown.

**Deliverable e accettazione.** La demo mostra tutti e sei gli stati. Ogni FAIL dimostrativo è riproducibile. Terminale, JSON e Markdown concordano sui conteggi. Un 500 con body conforme e status inatteso dal caso produce due esiti distinti. Il report di accesso anonimo descrive la discrepanza osservata senza inferire privilegi o regole di business.

**Test e rischio.** Provare body troncato, JSON malformato, risposta senza body, timeout, aspettativa assente, escaping del Markdown e sentinelle in query, cookie, header, body ed eccezioni. Il rischio è esporre dati grezzi o trasformare un’evidenza parziale in un verdetto definitivo.

### M6 · Prove esterne e release candidate · 12–16 ore

**Dipende da M5.** Usare tre API di progetti indipendenti attraverso fixture o istanze locali/autorizzate. Registrare versione del contratto, operazioni coperte, skip, limiti e tempo al primo report. Riservare tempo per correggere i difetti bloccanti. Preparare wheel, sdist, README, quickstart, limiti, SECURITY.md e istruzioni per i contributori.

**Deliverable e accettazione.** Tre prove documentate, installazione da artefatto verificata su macOS e Linux, esempi ripetibili e almeno un maintainer esterno capace di spiegare e riprodurre un risultato dal quickstart. Verificare nome PyPI, dipendenze e licenze effettive. Presentare la release candidate al proprietario per la decisione di pubblicazione.

**Test e rischio.** Installazione pulita da wheel e sdist, contenuto dei pacchetti e lettura dei report campione. Scegliere progetti con schemi, parametri e requisiti d’accesso diversi. Registrare separatamente eventuali prove in attesa di feedback esterno.

## Calendario indicativo

Ipotesi di 8 ore settimanali e pausa dal 23 al 29 dicembre. Se l’avvio slitta, spostare le finestre mantenendo ordine e criteri di uscita.

| Incremento | Finestra prevista | Risultato da mostrare |
|---|---|---|
| M1 | 25 novembre–8 dicembre 2026 | Due report riproducibili dalla CLI |
| M2 | 9–15 dicembre | Primo MVP installabile e usabile offline |
| M3 | 16–22 dicembre e 30 dicembre–5 gennaio 2027 | Piano deterministico con skip e costo in richieste |
| M4 | 6–19 gennaio | Scansione controllata su loopback |
| M5 | 20 gennaio–2 febbraio | Tre formati coerenti e sei stati dimostrati |
| M6 | 3–16 febbraio | Release candidate provata su progetti indipendenti |

Totale **66–88 ore**, più **8–12 ore di riserva**. Con la riserva, a 6 ore settimanali occorrono circa 13–17 settimane effettive. Rivedere le stime dopo la prova del validatore in M1 e del trasporto in M4.

## Riesame e compatibilità successiva

Al termine di ogni milestone consegnare i comandi realmente eseguiti, gli esiti dei test, un esempio osservabile dalla CLI e i criteri ancora aperti. Il maintainer decide il passaggio alla milestone seguente.

Prima di scegliere la prima estensione, usare le tre prove esterne per capire quale limite abbia impedito più verifiche utili. Se prevale OpenAPI 3.0, aggiungere un corpus dedicato alle differenze di dialetto e riusare planner, osservazioni e report. Se prevale un altro formato di fixture, aggiungere un lettore che produca la stessa osservazione interna. Per ogni modifica al formato dei report, includere una prova di lettura di un artefatto della precedente versione pubblica. Questo rende misurabile il costo di estendere il prodotto su più fronti.
