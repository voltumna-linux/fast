# TSC, sei master ed ebpm con unit statiche

Il ticket 03 estende `fast-rtchain`: il comando
`systemctl start rtchain-producers.target` richiede ora l'intera fase:

```text
rtchain-prepare → rtchain-producer-preflight → rtchain-tsc
  → rtchain-master@0 → @1 → @2 → @3 → @4 → @5
  → rtchain-ebpm → rtchain-producer-live → rtchain-producers.target
```

Ogni freccia corrisponde a `Requires` e `After`; il requisito sul master
precedente è scritto dal generatore. `ExecStartPost` fa parte dell'avvio del
servizio: il successivo non parte prima del successo del controllo. Un errore
lascia fallita la fase. Tutti i servizi dei produttori usano `rtchain.slice` e
unit ordinarie installate nell'immagine, senza `systemd-run`, launcher storici,
bring-up completo o teardown. Nessun produttore ha restart automatico.

I dodici naff e la readiness funzionale finale appartengono al ticket 04;
questo target non autorizza ancora Starter e non certifica il banco.
Stop, pulizia delle risorse e riavvio durante il servizio restano esclusi.
Dopo un fallimento correggere la causa e ripartire da un boot pulito: non
rimuovere manualmente risorse mentre altri processi possono usarle.

## Contratto locale

Le sezioni di [INSTALL.md](INSTALL.md) rimangono obbligatorie. I modelli
`examples/fast-01.json` e `fast-02.json` includono ora `producers`, ma sono
inattivi e contengono `REPLACE`. Nessun inventario storico è commissionato.

Il generatore aggiunge alle unit i mount di eseguibili, helper, `files`,
schemi cruda, cataloghi e librerie dichiarati, oltre a quelli espliciti in
`requires_mounts_for` richiesti dalla preparazione. Dichiarare in quest'ultima
lista anche risorse lette indirettamente dagli helper. Non aggiungere come
mount esterno la dispensa tmpfs creata da prepare: un requisito sul medesimo
mount creato dalla preparazione potrebbe bloccare la transazione.

`producers.tsc`, ciascuno dei sei `producers.masters` e `producers.ebpm`
richiedono:

- `executable`: binario reale assoluto, con `args` lista di argomenti già
  separati. Non usare script che creano unit o lanciano processi in background.
- `node`: numero di nodo con dispensa preparata; `cpus`: elenco CPU nella slice;
  `policy`: `other`, `fifo` o `rr`; `priority`: zero per other, 1–99 per FIFO/RR.
  Scheduling e affinità iniziale sono applicati da systemd, prima del loader;
  numactl impone allocazione privata e CPU al lancio del programma reale.
- `environment`: variabili esplicite, stringhe. Le variabili della shell
  chiamante non vengono ereditate. `common_environment` contiene le impostazioni
  condivise; non vengono copiati i default dei vecchi launcher. Qualificare
  tutte le manopole necessarie con la versione effettiva dei binari.
- `files`: input esterni obbligatori, esistenti al preflight; comprendere
  librerie dlopen, configurazioni e mappe non già descritte negli altri campi.
- `shm`: file pubblicato, obbligatoriamente nuovo e non vuoto;
  `ready_keys`: chiavi cruda da leggere con successo; `ready_log`: frasi esatte
  del log corrente che attestano l'inizializzazione; `timeout`: secondi, (0,120].
  Scegliere per i master prove successive alla configurazione EAL/code con i
  binari qualificati. La presenza di un processo non è una prova sufficiente.

`libhot_run NODE -- COMMAND ARGS...` deve essere un helper eseguibile che
entra nella dispensa corretta e termina con exec del comando. Anche numactl
deve preservare il PID. `MainPID` deve essere quello del produttore reale:
nessuna shell residente. Gli helper non sono forniti nel workspace e vanno
qualificati sulla share. Il generatore non li esegue.

I log sono `log_directory/tsc.log`, `master0.log`…`master5.log`, `ebpm.log`,
troncati dal lanciatore prima dell'exec. Configurare output non bufferizzato o
flush dei messaggi usati come prova; non usare messaggi che restano nei buffer.
Gli errori precedenti all'apertura dei log e gli errori dei controlli vanno nel
journal; gli errori di exec successivi sono nel log del produttore.

## Identità e prerequisiti

I master devono essere sei, in ordine fisico `port` 0…5, con `prefix`, `name`,
`app_id`, `bdf`, `ip`, `shm`, `eal` distinti. `eal` è la directory del prefisso
EAL effettivo; usare percorsi coerenti con `DPDK_SHM_SUFFIX` e con il runtime
DPDK qualificato. Gli app_id devono essere maggiori di 2, senza collisioni
con altri produttori del deployment. `cruda_template` deve contenere una sola
occorrenza attiva di `-name=`, `-app_id=` e `-manifest=`; il preflight genera
`masterN.conf` e manifest distinti nel log tmpfs. Il template deve puntare al
registro preparato, senza wipe globale.

`DPDK_CORE_LIST`, `DPDK_RXPORT_CONFIG`, `DPDK_TXPORT_CONFIG` sono obbligatori
nell'ambiente, con gli ID logici globali utilizzati dalle dodici lightbuf.
Nessuna rinumerazione automatica. Le geometrie e le maschere hardware devono
essere verificate per host. `devargs` completa la singola BDF. L'ambiente
imposto e verificato sul processo contiene prefisso, suffisso, maschere locali
0x1, IP, BDF/devargs, schema cruda e core di controllo. Le impostazioni
`DPDK_EBPM_*` del decoder ebpm sono inoltrate anche ai sei master, nei quali
esegue la libreria callback; override discordanti sono errori.

`control_cpus` deve essere contenuto nelle CPU del master; `control_threads`
è un elenco di nomi/pattern comm dei thread di servizio attesi (per esempio
`dpdk-intr`, `dpdk-mp-msg`). Dopo le registrazioni vengono cercati, spostati e
riletti su ciascun MainPID. Un thread obbligatorio assente o non spostabile
fa fallire la fase; nessun pgrep sul primo master.

Il preflight controlla l'assenza di vecchie SHM/prefissi EAL, l'eseguibilità dei
binari, gli input e il budget `free_hugepages` (elenco `path`, `count` positivo).
Dimensionare il budget per tutti i consumatori del boot, inclusi EAL/SHM dei
sei master ed ebpm, dopo l'allocazione del registro. `pci_root` in produzione
è `/sys/bus/pci/devices`: ogni BDF deve essere sola nel proprio gruppo IOMMU,
prima di qualsiasi binding. `dpdk_devbind --bind=vfio-pci BDF` viene eseguito
solo se necessario, con read-back del driver. VFIO deve essere caricato da
prepare. Non sono supportati gruppi condivisi tra master o bypass no-IOMMU.

L'ebpm deve pubblicizzare nel binario `DPDK_EBPM_MASTERS`; questa guardia
preliminare non sostituisce la verifica delle registrazioni. Riceve l'elenco
SHM dei sei master nell'ordine delle porte, split globale 0…5, `fill_id` e
`drain_id` distinti e `DPDK_SLAVE_SHM_BASE` dal proprio `shm`.

`calibration_base` identifica `.csv` e `_chain.csv`; `cruda_conf` è lo schema
ebpm. `catalog_units` specifica gli indici FPGA 1…N attesi: servono tutti gli
indici senza duplicati e una coppia A/B per ciascuno. `units_per_port=0`
disabilita il remap per fascio reale; se positivo il catalogo deve avere
esattamente sei volte quel numero di FPGA. Non si confondono inventario reale
e stride del generatore. `DPDK_EBPM_TX_SYNTH` deve essere esplicitamente `0` o
`1`; con `1`, dichiarare `DPDK_EBPM_TX_SYNTH_PORTS` globali e verificare sei
messaggi di maschera/port_base corretti nel log ebpm. Il generatore resta una
scelta esplicita dell'host, non viene acceso da questi modelli.

Il successo ebpm richiede risorse, prove configurate e, nel log corrente di
OGNI master, `slave FILL_ID: registrato` e `slave DRAIN_ID: registrato`.
Le sei righe DRAIN nel solo log ebpm non bastano. Se la versione distribuita
ha messaggi differenti occorre adattare e verificare il contratto prima del
deployment; non sostituire la prova con il conteggio dei processi.

## Operazioni dopo EAL e registrazioni

`producers.live` seleziona, in ordine, `e810`, `master-knobs`, `tx-rate`.
Per ogni operazione esclusa serve una motivazione non vuota in
`producers.not_applicable`. Tutti gli esiti selezionati sono obbligatori.

- `e810`: riusa l'operazione estratta nel ticket 01, su tutte le sei BDF dopo
  la disponibilità dei master/registrazioni; verifica TPH e propaga il
  fallimento di dpdk_master_tune, che deve effettuare read-back hardware.
- `master-knobs`: riusa scrittura e read-back cruda, con almeno una chiave
  sotto ciascuna delle sei identità. Configurare tutte le chiavi obbligatorie
  dell'host (TX Flush, termostato). Un errore non diventa una sola diagnosi.
- `tx-rate`: `producers.tx-rate.percent` tra 0 e 100. Scrive e rilegge
  `ApplyAtS` comune (tre secondi futuri) e `RatePct` su tutti i master;
  una scadenza mancata fallisce. Dopo l'istante comune richiede
  `RateAppliedPct` uguale su tutti, entro il timeout ebpm. Un valore scritto
  senza riscontro non conclude la fase.

Compaction, naff-knobs, fair server e rivalutazione tick restano nella fase
successiva del ticket 04, dopo i dodici naff. Non invocare `phase live` del
vecchio estrattore al posto della fase produttori: comprende operazioni i
cui prerequisiti non sono ancora presenti.

## Verifiche

```sh
python3 -m unittest discover -s tools/rtchain/tests -v
systemctl status rtchain-producer-preflight rtchain-tsc 'rtchain-master@*' rtchain-ebpm rtchain-producer-live
journalctl -b -u rtchain-producer-live -u rtchain-ebpm
```

I test eseguono CLI/generatore con processi foreground reali controllabili,
risorse in directory temporanee e strumenti esterni controllati. Coprono
successo, TSC vivo senza SHM, ciascun master incompleto, registrazioni mancanti
su ciascuno dei sei anche in presenza di duplicati altrove, risorse vecchie,
cataloghi, budget hugepage, IOMMU, dipendenze generate, thread di servizio,
fallimento tuning e applicazione sincronizzata del rate su sei master.

Non eseguono un boot systemd. Per collaudare la transazione reale in una VM
systemd isolata, installare il pacchetto e una configurazione di test locale
con produttori controllabili e mount/clock/helper controllati. Avviare il target
in un boot pulito, acquisendo il journal monotonic e i tempi di pubblicazione:
nessun master deve precedere il controllo TSC, nessun master N deve precedere
la conclusione di N-1, ebpm deve seguire tutti i master e il tuning deve seguire
le registrazioni. Ripetere da boot pulito con SHM TSC assente, EAL/SHM assente
su ciascuna porta, FILL e DRAIN mancanti separatamente su ciascun master,
fallimento del mount e del tuning: il target deve fallire senza eseguire i
passi dipendenti. Tango deve restare assente durante tutte le prove.

Su fast-01 e fast-02 qualificare separatamente binari e helper, copie NUMA,
clock TSC, pool, gruppi VFIO, mapping porte/CPU/ID logici, cataloghi, chiavi e
messaggi di inizializzazione, FILL/DRAIN lato master e impostazioni E810 lette
indietro. Verificare MainPID dopo gli exec e le variabili nei sei processi.
La presenza di file in una simulazione non dimostra memoria fisica NUMA,
inizializzazione reale delle code o prestazioni realtime. La prova funzionale
del flusso e l'autorizzazione di Starter restano nel ticket 04 e successivi.
