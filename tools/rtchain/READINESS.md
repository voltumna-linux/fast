# Readiness realtime prima di Starter

Il ticket 04 completa la catena di avvio con dodici unit statiche naff e una
barriera oneshot. Dopo aver commissionato la configurazione dell'host:

```sh
systemctl start rtchain-ready.service
systemctl status rtchain-ready.service
journalctl -b -u rtchain-final-live.service -u rtchain-ready.service
```

La sequenza richiesta con `Requires` e `After` è:

```text
prepare → producer-preflight → tsc → master@0 → … → master@5
 → ebpm → producer-live → naff@1 → @9 → … → @89
 → final-live → ready
```

`rtchain-producers.target` conserva il significato del ticket 03, fino a
producer-live. Per includere naff, tuning finale e prova dati usare
`rtchain-ready.service`. L'integrazione della dipendenza di Starter è nel
ticket 07: il suo servizio dovrà richiedere questa barriera e ordinarsi dopo
di essa. Il successo certifica la disponibilità realtime per i consumatori;
configurazione del simulatore, SmartRescan, campi catds e altri passi del
banco seguono Starter (ticket 08). Nessuna query a devx, tcbuf, catds, nessun
comando Tango, launcher storico, teardown o unit transitoria è usato qui.

## Configurazione dei dodici naff

Le sezioni di INSTALL.md e PRODUCERS.md restano obbligatorie. Aggiungere
`producers.naffs`: dodici oggetti con il contratto comune dei produttori
(`executable`, `args`, `node`, `cpus`, `policy`, `priority`, `environment`,
`files`, `shm`, `ready_keys`, `ready_log`, `timeout`) e `group` in ordine
1,9,17,…,89. Sono i dodici gruppi della flotta richiesta dalla specifica,
non ventiquattro processi di due flotte storiche. Ogni host mantiene la sua
mappa CPU/NUMA, il proprio periodo e la propria configurazione di ingresso.

`NAFF_CCD_GROUP` è imposto e verificato nel processo; `NAFF_CCD_PREFIX` deve
essere esplicito e uguale per tutti. Tutte le altre variabili effettivamente
necessarie al binario (ingresso ebpm/lightbuf, CPU interna, periodo, finestra,
configurazione cruda, ecc.) sono responsabilità della configurazione
commissionata e devono essere qualificate con la versione distribuita. Gli
helper devono preservare MainPID mediante exec. Non utilizzare il launcher
CS che avvia in background. Non introdurre default storici in produzione.

I dodici `shm` devono indicare lo stesso file, distinto da quelli di TSC,
master ed ebpm. Il preflight rifiuta risorse pregresse e controlla anche gli
eseguibili/input dei naff e l'adattatore funzionale. Il primo gruppo rifiuta
una SHM già presente; gli altri richiedono quella del creatore. Ogni lancio
ha un log corrente `naffGROUP.log`, deve pubblicare chiavi cruda leggibili
(non è sufficiente l'exit zero di crudaknob) e attestare almeno
`spectrum ring labelled`. Aggiungere a `ready_log` le altre prove qualificate
di completamento dell'inizializzazione. Il gruppo successivo parte solo dopo
il successo di queste prove e altri otto secondi per il creatore, due per gli
altri: viene preservata la
separazione storica degli avvii per evitare la cancellazione delle etichette.
La presenza e la validità delle etichette di spettro e tune per tutti i
gruppi sono controllate nuovamente dalla prova funzionale finale.

I modelli fast-01/fast-02 sono inattivi (`commissioned: false`), con
`REPLACE` per inventario e parametri non qualificati. La variabile del periodo
nel modello è un segnaposto da verificare, non un'interfaccia del binario
garantita da questo repository. Dichiarare mount hugetlbfs, budget hugepage
2M e input indiretti necessari nella preparazione e nei mount esterni.

## Operazioni dopo i produttori

`readiness.live` seleziona, nell'ordine indicato, `naff-knobs`, `real-beam`,
`compaction`, `fair-server`, `tick-revalidate`. Per ciascuna operazione esclusa
serve una motivazione non vuota in `readiness.not_applicable`.
`naff-knobs` è obbligatoria: `bases` deve essere `[readiness.naff_base]`,
`groups` l'elenco completo dei dodici gruppi e `pipeline_stages` deve valere
31, includendo le bande laterali. `sideband_floor` e `waterfall_mask` restano
scelte esplicite dell'host. Le scritture sono rilette e verificate.

`real-beam` imposta solo il produttore ebpm, senza simulatore né Tango;
se l'host richiede configurazione sintetica successiva, motivare l'esclusione
qui e configurare il banco dopo Starter. Il flusso richiesto dalla readiness
deve comunque essere disponibile prima di Starter. Non spostare un
prerequisito dei dati sotto una dipendenza da catds.

Compaction usa `tools.disable_kcompact`, da fornire e qualificare. Fair server
richiede percorsi runtime espliciti e un runtime positivo con rilettura;
runtime zero e watchdog/stop storici restano esclusi. Tick rivaluta i thread
FIFO dei produttori già vivi e ne ripristina la priorità, fallendo se non ne
trova. Un errore obbligatorio fa fallire `rtchain-final-live.service` e impedisce
la readiness. Non viene eseguita indiscriminatamente la vecchia `phase live`.

## Dipendenza funzionale esplicita

`tools.rtchain_probe` è un eseguibile assoluto esterno, da distribuire e
qualificare insieme ai binari FAST: le ABI dei buffer e il controllo storico
non sono presenti nel workspace. La sua assenza fallisce già il preflight.
Il pacchetto fornisce l'aggregatore, non un decoder che presume un'ABI non
verificata. Il contratto è testabile e non accetta un successo implicito:

- L'adattatore legge da stdin un oggetto JSON `{"version":1,"sources":{…}}`.
  Le sorgenti sono esattamente `master0`…`master5`, `ebpm`, `naff1`…`naff89`.
  Ogni sorgente contiene `pid`, `shm`, `labels` e, per i naff, `group`.
- Deve aprire e validare le risorse di questa esecuzione, leggere sequenze di
  dati effettivamente pubblicati e, per i naff, verificare le etichette
  configurate di spettro e tune, entrambe non vuote e distinte. Deve validare
  tipo/versione del buffer, associazione al produttore/gruppo, e disponibilità
  di dati utilizzabili per i consumatori. File soltanto presenti non bastano.
- Restituisce su stdout solo un oggetto JSON con le stesse sorgenti:
  `{"master0":{"sequence":123,"labels":[]},…,"naff1":{"sequence":456,
  "labels":["etichetta-spettro-g1","etichetta-tune-g1"]},…}`. Le sequenze
  sono interi non negativi. Ogni elenco etichette deve corrispondere a quello
  richiesto, nell'ordine. Diagnostica su stderr e uscita non zero per errori.
- `readiness.labels` associa a ciascun naff almeno le due etichette reali;
  `sample_interval` è in (0,30] secondi e `timeout` in
  (sample_interval,120]. Il controllo legge due campioni separati
  dall'intervallo e richiede che **ogni** sequenza aumenti strettamente.
  Reset/wrap durante questa finestra è un fallimento, non un successo.
- Mai usare orologio, heartbeat del processo, messaggi di log, un contatore
  inventato dall'adattatore o richieste Tango come riscontro del flusso. I
  contatori devono attestare dati prodotti lungo tutta la catena. Un'interfaccia
  di sola lettura con le librerie dei buffer qualificati è appropriata.

Il processo dell'adattatore riceve solo PATH e LD_LIBRARY_PATH dichiarati;
non eredita credenziali o ambiente Tango. Errori, JSON invalido, copertura
incompleta, etichette mancanti, sequenze ferme e timeout fanno fallire la
barriera con diagnostica. Preparazione/preflight/tuning devono essere active
con Result=success. Prima e dopo la prova vengono riverificati tutti i
MainPID, SHM, EAL, chiavi/log correnti e registrazioni FILL e DRAIN dell'ebpm
su ciascuno dei sei master; un cambio di MainPID fa fallire la prova.

## Verifica e collaudo

```sh
python3 -m unittest discover -s tools/rtchain/tests -v
```

La suite usa CLI reali, processi foreground controllati e un adattatore che
legge buffer di prova, senza Tango. Copre l'avvio della flotta, tuning,
progressione su tutte le sorgenti, assenza risorse e registrazioni sui sei
master, naff incompleti, etichette perse, stadi obbligatori falliti e
adattatore mancante/fallito/incompleto/bloccato. Verifica i drop-in generati
e i collegamenti statici. Non certifica un boot systemd o l'hardware.

In VM con systemd avviare la barriera con produttori e adattatore controllati;
verificare il journal e la transazione anche per prepare/live falliti, un
naff incompleto e ciascuna sorgente ferma. Nessun server Tango deve essere
necessario. Ripetere poi su fast-01/fast-02 con binari, ABI, inventario e
parametri reali: confrontare etichette con quelle dei consumatori, verificare
registrazioni e progressione di tutti i gruppi/porte, tuning e NUMA/E810.
L'adattatore deve fallire per dati immobili anche quando tutti i processi
sono vivi. Mancanza dell'adattatore qualificato impedisce il deployment;
non sostituirlo con `/bin/true` o con il gate storico misto.

La barriera è di avvio, non un monitor periodico. Stop ordinato, propagazione
dei guasti durante il servizio e restart/pulizia non sono definiti qui.
Dopo un avvio fallito correggere la causa e ripartire da un boot pulito.
