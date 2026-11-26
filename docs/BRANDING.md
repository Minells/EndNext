# Identità visiva di EndNext

## Giunzione

EndNext verifica risposte API osservate contro un contratto OpenAPI e conserva evidenze confrontabili tra esecuzioni. Nel simbolo, il profilo superiore rappresenta il contratto e quello inferiore la risposta osservata. Il vuoto a gradino rende visibile la relazione tra i due. L’endpoint è il punto di partenza e l’evidenza può servire alla verifica successiva.

La grafia ufficiale è **EndNext**. Il wordmark è un disegno vettoriale unico, con spaziatura dedicata e un gradino ripreso nella **E** e nella **t**. Usa un solo colore per l’intera parola.

## Marchio e file

| File | Utilizzo |
| --- | --- |
| [logo-light.svg](../assets/branding/logo-light.svg) | Combinazione orizzontale Graphite su fondi chiari |
| [logo-dark.svg](../assets/branding/logo-dark.svg) | Combinazione orizzontale con wordmark Chalk e profilo inferiore Signal, su Graphite o altro fondo scuro uniforme |
| [logo.svg](../assets/branding/logo.svg) | Combinazione su fondo Graphite incorporato, per superfici dal colore incerto |
| [logo-monochrome.svg](../assets/branding/logo-monochrome.svg) e [logo-monochrome-inverse.svg](../assets/branding/logo-monochrome-inverse.svg) | Nero puro su chiaro o bianco puro su scuro, per stampa, timbri e riproduzione a un colore |
| [symbol.svg](../assets/branding/symbol.svg) | Simbolo autonomo Graphite su Signal, per avatar e icone di prodotto |
| [wordmark.svg](../assets/branding/wordmark.svg) | Nome disegnato, per spazi in cui il simbolo è già presente |
| [favicon.svg](../assets/branding/favicon.svg) | Icona per schede e segnalibri, su fondo Graphite incorporato |
| [banner.svg](../assets/branding/banner.svg) | Tavola 2:1 riutilizzabile per documentazione, annunci e release |
| [social-preview.png](../assets/branding/social-preview.png) | Esportazione opaca 1280 × 640 px del banner, per le anteprime dei link |

Il simbolo usa un reticolo di 64 unità. Tra i profili ci sono 8 unità in verticale e 4 nei punti più stretti. A 16 px corrispondono a 2 px e 1 px. Mantieni almeno 8 unità di spazio libero dai bordi visibili del logo. La combinazione orizzontale parte da 160 px di larghezza sullo schermo o 35 mm in stampa. Per spazi tra 16 e 159 px di larghezza usa il simbolo; per la favicon scegli il file dedicato.

Mantieni le proporzioni e usa l’asset previsto per il fondo. Evita rotazioni, contorni, ombre, ritagli della giunzione, riempimenti aggiuntivi e lettering ricomposto. Lascia libera l’area tra i due profili. In HTML, dai al marchio il testo alternativo `EndNext` quando identifica il prodotto. Se il nome è già presente accanto a un simbolo decorativo, usa un testo alternativo vuoto.

## Colori

| Colore | HEX | RGB | Ruolo |
| --- | --- | --- | --- |
| Graphite | `#141618` | 20, 22, 24 | Fondo scuro e testo primario su chiaro |
| Carbon | `#292D32` | 41, 45, 50 | Superficie secondaria su scuro, struttura e separatori |
| Signal | `#E6FF52` | 230, 255, 82 | Profilo osservato nel marchio scuro, accento del marchio e azioni primarie |
| Chalk | `#F3F2ED` | 243, 242, 237 | Fondo chiaro e testo primario su scuro |
| Steel | `#8C959F` | 140, 149, 159 | Testo secondario su Graphite e Carbon |

Usa fondi neutri come base e Signal in punti in cui serve attirare l’attenzione. Su un pulsante Signal usa testo Graphite. Quando il controllo è su Chalk, aggiungi un bordo Graphite per rendere riconoscibile il perimetro. Su scuro, un link Signal è adatto al testo. Su chiaro, usa il colore `link` del tema. Sottolinea i link all’interno del testo.

| Coppia per il testo | Contrasto |
| --- | --- |
| Chalk e Graphite | 16,18:1 |
| Graphite e Signal | 16,23:1 |
| Steel su Graphite | 5,97:1 |
| Steel su Carbon | 4,56:1 |
| Testo secondario chiaro `#515B65` su Chalk | 6,17:1 |

Per il testo normale usa almeno 4,5:1. Per il testo grande e gli elementi funzionali come bordi dei controlli e indicatori di focus, usa almeno 3:1 nel rispettivo contesto. Usa `border` per separatori decorativi e `border-control` per contorni funzionali. I colori di stato hanno token indipendenti. Scrivi il nome dello stato anche quando è accompagnato da un colore. [WCAG 2.2 · Contrasto del testo](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html) · [WCAG 2.2 · Contrasto non testuale](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html)

## Tipografia e geometria

| Ruolo | Carattere | Indicazioni |
| --- | --- | --- |
| Wordmark | Contorni dedicati, a partire da Instrument Sans | Usa l’SVG ufficiale |
| Titoli | Instrument Sans 600 | 40 px per titoli di apertura, 28 px per titoli di pagina, interlinea 1,15 |
| Testo e documentazione | Instrument Sans 400 | Base 16 px, interlinea 1,5 |
| Codice e comandi | IBM Plex Mono 400 | Base 14 px, preserva spazi e allineamenti |
| Metadati e label | IBM Plex Mono 400 | Base 13 px, con una dimensione maggiore quando serve |

Le scale dei token tipografici usano `rem` e seguono la dimensione di testo scelta dall’utente. La prima alternativa è la sans o monospace di sistema. Instrument Sans e IBM Plex Mono sono distribuiti con SIL Open Font License 1.1. Gli SVG contengono i contorni e si visualizzano senza installare font. Quando si distribuiscono i file dei font, includere gli avvisi di copyright e la licenza del pacchetto usato e rispettare eventuali nomi riservati nelle versioni modificate. [Licenza Instrument Sans](https://github.com/google/fonts/blob/main/ofl/instrumentsans/OFL.txt) · [Licenza IBM Plex Mono](https://github.com/google/fonts/blob/main/ofl/ibmplexmono/OFL.txt)

Usa una griglia di 4 px con misure da 4, 8, 12, 16, 24, 32, 48 e 64 px alla base di 16 px. Allinea testi e diagrammi sugli stessi margini. Mantieni le superfici piatte, con bordi da 1 px e angoli retti. Usa un raggio da 4 px per controlli compatti. Le icone funzionali partono da una tavola di 24 px con tratti da 2 px e terminali squadrati. Provale a 16 px prima di riusarle nelle viste dense.

## Token e temi

[tokens.json](../assets/branding/tokens.json) è il riferimento per i valori e le relazioni. Un valore come `{color.brand.graphite}` rimanda al percorso omonimo nel JSON. [tokens.css](../assets/branding/tokens.css) espone gli stessi ruoli come custom properties con prefisso `--en-`. Quando cambi un valore, aggiorna entrambi i formati. Il CSS segue la preferenza di sistema e offre colori di sistema quando è attiva la modalità a colori forzati.

```css
.report {
  background: var(--en-color-canvas);
  color: var(--en-color-text);
  font-family: var(--en-font-family-sans);
}

.report :focus-visible {
  outline: var(--en-border-focus-width) solid var(--en-color-focus);
  outline-offset: var(--en-border-focus-offset);
}
```

## GitHub, CLI e documentazione

- **README.** Usa il marchio orizzontale su `<picture>` con le varianti per tema. I due badge locali mostrano requisiti Python e licenza. Per nuovi badge statici usa la stessa altezza di 24 px, le superfici Carbon e Graphite e il testo Chalk in IBM Plex Mono. Collega il badge alla fonte del dato. Per lo stato dinamico di CI usa il badge nativo di GitHub. [Immagini per tema](https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/quickstart-for-writing-on-github#adding-an-image-to-suit-your-visitors) · [Badge dei workflow](https://docs.github.com/en/actions/how-tos/monitor-workflows/add-a-status-badge)
- **CLI.** L’help interattivo usa una versione del simbolo costruita con caratteri da terminale, accanto a EndNext. Graphite su Signal si attiva sui terminali a 24 bit. `NO_COLOR` abilita la versione monocromatica. Pipe, terminali essenziali e codifiche non UTF-8 ricevono il nome in testo semplice. I colori del marchio appartengono alla testata dell’help. Risultati, log e report comunicano attraverso nomi dei campi e verdetti.
- **Documentazione e diagrammi.** Usa blocchi rettangolari e connettori ortogonali. Mostra i flussi effettivi e indica la provenienza delle osservazioni. Scrivi comandi, metodi HTTP e percorsi in monospace. Usa Signal per mettere in evidenza un elemento alla volta e affiancagli una label. Per spiegare il prodotto, preferisci un diagramma o un esempio tratto dai casi sintetici del repository.
- **Release e condivisione.** Parti dal banner vettoriale e mantieni allineamenti e margini. Per l’anteprima del repository, carica il PNG in **Settings → Social preview → Edit**. GitHub gestisce questa immagine nelle impostazioni. [Configurazione dell’anteprima su GitHub](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/customizing-your-repositorys-social-media-preview)
