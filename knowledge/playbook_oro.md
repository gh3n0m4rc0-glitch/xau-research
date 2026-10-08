# Playbook del desk oro (XAUUSD)

Documento base della "memoria di ricerca": gli analisti AI ne leggono i passaggi pertinenti a ogni analisi.
Puoi aggiungere altri file (.md, .txt, .pdf) in questa cartella: verranno indicizzati automaticamente.

## 1. Driver macro dell'oro

- **Rendimenti reali USA (TIPS 10 anni)**: storicamente il driver principale. Rendimenti reali in calo riducono il costo opportunità di detenere oro (che non paga interessi). Dal 2022 la relazione si è indebolita molto: l'oro è salito nonostante rendimenti reali elevati, sostenuto dagli acquisti record delle banche centrali (oltre 1.000 tonnellate l'anno nel 2022-2024). Il desk quindi pesa i driver in base al regime, non in modo fisso.
- **Dollaro USA**: relazione inversa prevalente (l'oro è prezzato in dollari). Dollaro forte = vento contrario.
- **Politica della Fed**: aspettative di tagli = positivo per l'oro; sorprese restrittive = negativo. Il tasso a 2 anni è il miglior proxy di mercato del percorso atteso dei tassi.
- **Inflazione attesa (breakeven)**: in aumento tende a sostenere l'oro come copertura.
- **Rischio geopolitico e stress finanziario**: domanda di bene rifugio. Attenzione: negli shock di liquidità acuti (es. marzo 2020) l'oro può prima scendere per vendite forzate e solo dopo salire.
- **Banche centrali**: acquisti persistenti (Cina, Polonia, Turchia, India…) creano un pavimento strutturale alla domanda.
- **Flussi ETF** (es. GLD): afflussi = domanda d'investimento occidentale; deflussi prolungati = debolezza.
- **Domanda fisica Asia**: premio di Shanghai rispetto a Londra, festività (Capodanno cinese, Diwali/stagione dei matrimoni in India).
- **Posizionamento COT** (Managed Money sui futures COMEX): estremi di posizionamento lungo rendono l'oro vulnerabile a liquidazioni; estremi corti favoriscono short squeeze. Va usato come segnale contrarian solo agli estremi.

## 2. Eventi macro e reazione dell'oro

| Evento | Orario tipico (New York) | Reazione tipica dell'oro |
|---|---|---|
| NFP (occupazione USA) | 8:30, primo venerdì del mese | dato forte → dollaro e rendimenti su → oro giù; debole → oro su |
| CPI / PCE (inflazione) | 8:30 | inflazione sopra attese → Fed più restrittiva → oro giù (di solito) |
| FOMC (decisione) | 14:00 + conferenza stampa 14:30 | primo movimento sul comunicato, spesso un secondo movimento (anche opposto) durante la conferenza |
| Richieste sussidi, ISM, vendite al dettaglio | 8:30 / 10:00 | impatto minore, rilevante se c'è una grossa sorpresa |

Regole del desk:
- Prima del dato non si aprono nuove posizioni (blackout). Le posizioni in profitto vengono protette a break-even.
- Il primo movimento dopo il rilascio è spesso rumoroso (whipsaw); la continuation è più affidabile se: (a) la sorpresa rispetto al consenso è ampia, (b) va nella stessa direzione del quadro macro, (c) il volume del rilascio è molto alto e il ritracciamento successivo avviene con volume più basso.
- La vista pre-news va formata nelle ore precedenti (consenso, "whisper", indicatori anticipatori come ADP/ISM per l'NFP, posizionamento). Se tutti si aspettano la stessa cosa, il rischio è "buy the rumor, sell the fact".
- Continuation solo se il prezzo conferma la vista: impulso ≥ 1,5 ATR H1 nella direzione attesa, poi ritracciamento 38-62% con conferma.

## 3. Sessioni e orari (UTC; con l'ora legale USA/UK gli orari si spostano di 1 ora)

- **Asia (circa 23:00-07:00 UTC)**: liquidità più sottile, l'oro tende a costruire un range. Gli stop si accumulano sopra il massimo e sotto il minimo asiatico.
- **Apertura di Londra (07:00-08:00 UTC)**: spesso spazza un lato del range asiatico (stop run, "Judas swing") prima del movimento vero. La mattina di Londra stampa spesso uno degli estremi della giornata.
- **Aste LBMA del prezzo dell'oro**: 10:30 e 15:00 ora di Londra. Momenti di liquidità e possibili inversioni.
- **New York**: è il centro di gravità del volume sull'oro (futures COMEX). I dati USA escono alle 8:30 e alle 10:00 ora di New York. Durante la sovrapposizione Londra-New York (circa 12:00-16:00 UTC) ci sono i movimenti più ampi; New York spesso inverte il movimento della mattina londinese.
- **Dopo le 20:00 UTC**: range che si comprimono e spread che si allargano verso il rollover. Il desk evita nuovi ingressi in quelle ore.
- **Range giornaliero medio (ADR)**: se prima di New York si è già consumato il 90-130% dell'ADR, il movimento è in gran parte esaurito; oltre il 130% serve una news per estenderlo.

## 4. Liquidità e stop run

- I pool di liquidità sono dove si accumulano gli stop: massimo e minimo del giorno precedente (PDH/PDL) e della settimana precedente (PWH/PWL), equal highs/lows (massimi o minimi quasi uguali, i più "ovvi"), range asiatico, swing evidenti, numeri tondi (multipli di 10 e 50 dollari).
- **Sweep valido**: il prezzo supera il livello di poco (meno di circa 1 ATR H4), poi richiude dall'altra parte entro poche candele. Se la penetrazione è profonda e il prezzo accetta oltre il livello, è una rottura vera e non uno stop run.
- **Stop loss**: mai appena oltre un livello ovvio. Va messo oltre il punto di invalidazione e oltre la fascia dove avvengono i tipici sweep, e mai su prezzi tondi.
- **Target**: il pool di liquidità opposto, uscendo un po' prima del livello.

## 5. Analisi volumetrica

### Tick volume e volume reale
- Sul CFD spot il volume disponibile è il "tick volume" (numero di variazioni di prezzo). Uno studio su EURUSD, USDJPY, GBPUSD ed EURCHF ha trovato correlazioni oltre 0,95 con il volume reale su base oraria. Su timeframe molto brevi la relazione è meno verificata.
- Il volume reale e il lato dell'aggressore (chi compra o vende "a mercato") esistono sui **futures COMEX GC**. Il servizio li legge da Databento, se configurato, e li passa al cBot convertiti in prezzi spot tramite la base futures-spot.

### Volume profile
- **POC** (Point of Control): il prezzo con più volume, cioè il "fair value" accettato. Funziona da magnete e da target.
- **Value Area** (70% del volume, tra VAL e VAH): zona di accettazione. Fuori dalla value area il prezzo è "caro" (premium) o "a sconto" (discount).
- **Regola dell'80%** (Market Profile, Dalton): se il prezzo apre o esce fuori dalla value area del giorno precedente, vi rientra e ci resta per due periodi, tende ad attraversarla fino all'altro bordo. Funziona meglio in mercati bilanciati, non nei trend forti.
- **HVN** (nodi ad alto volume): supporti e resistenze dove il prezzo rallenta. **LVN** (nodi a basso volume): zone di transito veloce, dove il prezzo non resta a lungo.
- In range si compra vicino alla VAL con target la VAH, e viceversa. In trend, dopo una rottura sostenuta della VAH o della VAL, si cercano pullback su quel livello.

### VWAP
- VWAP giornaliero e settimanale con bande di deviazione standard. Sopra il VWAP comandano i compratori della sessione. Oltre 2σ dal VWAP settimanale il prezzo è esteso e il rischio di ritorno alla media cresce.
- Il VWAP ancorato da uno swing importante mostra il prezzo medio di chi è entrato da quel punto.

### Delta e CVD
- **Delta** = volume aggressivo in acquisto meno volume aggressivo in vendita. **CVD** = delta cumulato.
- **Divergenza**: il prezzo fa un nuovo massimo ma il CVD no, quindi l'acquisto aggressivo si sta esaurendo (segnale ribassista); simmetrico sui minimi.
- **Assorbimento**: tanto volume aggressivo da un lato ma il prezzo non avanza, perché ordini limite passivi lo assorbono. È un possibile segnale di inversione.

### Wyckoff / VSA (Volume Spread Analysis)
- **Climax**: volume enorme, range ampio, chiusura lontana dall'estremo. Indica la fine di un movimento (selling climax sui minimi, buying climax sui massimi).
- **Sforzo contro risultato**: molto volume con poco progresso indica assorbimento; poco volume con grande progresso indica assenza di contropartita.
- **No demand / no supply**: candele di pullback a basso volume. Il trend è sano e il pullback si può comprare.
- **Spring / upthrust**: falsa rottura di un range con rientro rapido. È l'equivalente Wyckoff dello stop run. Sull'oro gli spring migliori avvengono spesso all'apertura di Londra o di New York e attorno ai numeri tondi.
- **Fasi Wyckoff**: accumulazione (selling climax → automatic rally → secondary test → spring → test → sign of strength → last point of support) e distribuzione (buying climax → automatic reaction → upthrust/UTAD → sign of weakness → last point of supply). Il desk entra sullo spring/upthrust confermato o sul test a volume basso.
- **DOM**: lo sbilanciamento persistente tra size in bid e in ask conferma la pressione, ma gli ordini nel book possono essere cancellati (spoofing): va usato solo come conferma, mai da solo.
- **Breakout valido**: volume relativo (RVOL, confrontato con la stessa ora dei giorni precedenti) almeno 1,3-1,5 volte la media. Un breakout a volume basso è un candidato fakeout.

## 6. Strategie del desk

| Codice | Strategia | Quando funziona | Quando fallisce |
|---|---|---|---|
| A | Pullback in trend (D1 in trend, ritracciamento H4 nella zona EMA21-EMA50, pullback a basso volume) | trend D1 chiari, macro allineata | regimi laterali e inversioni di trend |
| B | Stop run e rientro (sweep di un pool di liquidità + riconquista) | aperture di Londra/NY, livelli molto ovvi, volume alto sullo sweep | rotture vere (penetrazione profonda, accettazione oltre il livello) |
| C | Breakout + retest di un range di compressione | volatilità compressa che si espande, RVOL alto | breakout senza volume |
| D | Mean reversion ai bordi del range | regime laterale (ADX basso), assorbimento ai bordi | partenza di un trend |
| E | Continuation post-news coerente con la vista pre-news | grandi sorprese allineate alla macro | whipsaw, dati misti, conferenza FOMC che ribalta il comunicato |
| G | Wyckoff spring/upthrust sul trading range H4 | range lunghi e rispettati, falsa rottura con shakeout o test a volume basso | rotture con accettazione fuori dal range |
| F | Rientro in value area (regola dell'80%) | mercato bilanciato, rifiuto con volume fuori dalla value area | giornate di trend con accettazione fuori valore |

La strategia "core" per il trade di recupero va scelta con le statistiche del backtest: tanti trade, profit factor sopra 1,3 e risultati positivi in ogni anno. Di default è la A (la più semplice e replicabile).

## 7. Rischio: win rate, aspettativa, serie perdenti

- Il win rate da solo non conta: conta l'**aspettativa** = win rate × vincita media − loss rate × perdita media. Un 40% di vincite con R:R 1:2,5 batte un 70% con R:R 1:0,4.
- Le serie perdenti sono **normali** anche con un buon sistema. Probabilità di avere almeno k perdite di fila in 100 trade (simulazione):

| Win rate | 3 di fila | 4 di fila | 5 di fila | 6 di fila | 8 di fila |
|---|---|---|---|---|---|
| 45% | ~100% | 99% | 92% | 73% | 31% |
| 55% | ~100% | 92% | 65% | 36% | 8% |
| 65% | 95% | 63% | 29% | 11% | 1% |

- **Trade di recupero (1,75x)**: è un solo tentativo dopo una perdita, solo con la strategia core e solo con conferme al massimo livello (punteggio ≥ 0,75, 3 conferme su 4, volumi e macro a favore, nessuna news tier 1 entro 12 h). Se fallisce non si raddoppia mai: scatta una pausa. Moltiplicare i lotti a catena (martingala) porta prima o poi alla rovina del conto.
- **Scaglionamento**: su un'idea forte il rischio totale si divide in tranche (mercato + limiti più profondi) con lo **stesso** stop. Il prezzo medio migliora e il rischio massimo resta quello deciso. La quarta posizione si aggiunge solo a idea già protetta a break-even.

## 8. Checklist del comitato prima di un trade

1. Qual è il regime (trend o range, volatilità) e quale playbook gli si addice?
2. La macro e la vista pre-news sono a favore, neutrali o contro?
3. Dove sono valore e liquidità (POC, VAH/VAL, HVN, pool di stop)? C'è spazio fino al target senza muri?
4. Cosa dicono volume e delta: partecipazione, assorbimento, divergenze? E i futures CME?
5. Dov'è l'invalidazione? Lo stop è oltre la zona degli sweep tipici?
6. Ci sono eventi entro 24 h o il weekend vicino?
7. Quale prova farebbe cambiare idea (il caso dell'avvocato del diavolo)?

## Fonti utili

- Tick volume e volume reale: studio "Tick Volume vs Real Volume" (fxvolume, correlazioni orarie > 0,95 su 4 coppie FX).
- Volume profile e regola dell'80%: Dalton, *Mind Over Markets*; guide sul value area trading.
- Sessioni dell'oro: analisi su Tokyo/Londra/New York (MQL5 blog "The XAUUSD Trading Day").
- Dati: FRED (DFII10, DTWEXBGS, DGS2, T10YIE, VIXCLS), CFTC COT (oro 088691), Databento GLBX.MDP3 (GC).
