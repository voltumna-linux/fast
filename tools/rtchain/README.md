# Operazioni di avvio FAST

`rtchain-ops` separa le operazioni da `bring-updown` senza modificarlo o
richiamare il suo bring-up. È il prefactoring del ticket locale 01;
installazione, unit statiche, lancio dei produttori e readiness appartengono
ai ticket 02–04. Il ticket 02 aggiunge ora il pacchetto e la preparazione
statica descritti in [INSTALL.md](INSTALL.md). Il ticket 03 integra TSC, sei
master ed ebpm: unit e contratti sono descritti in [PRODUCERS.md](PRODUCERS.md).
Non avvia server Tango, non ferma processi e non rimuove memoria condivisa.

Fonte di riferimento: repository `bring-updown`, commit
`4a404f782d812036a16e3a8af42670ddd7f2cd2b`, in particolare
`common/bringup_full.sh`, `common/bringup_splitsix.sh` e
`common/catds/setup_banco_tbt.sh`. Le operazioni sono adattate per avere
esiti espliciti; non si eseguono intervalli di righe del sorgente storico.

## Invocazione e configurazione

Richiede Python 3 (sola libreria standard). Esempi di invocazione, dopo aver
preparato una configurazione locale verificata:

```sh
python3 tools/rtchain/rtchain-ops --config /etc/fast/rtchain.json operation hugepages
python3 tools/rtchain/rtchain-ops --config /etc/fast/rtchain.json phase prepare
python3 tools/rtchain/rtchain-ops --config /etc/fast/rtchain.json phase live
python3 tools/rtchain/rtchain-ops --config /etc/fast/rtchain.json phase bench
```

Il JSON richiede `host` uguale all'hostname corto corrente. `phases` contiene
liste ordinate di nomi di operazioni. L'ordine è esplicito: prima mount,
poi pool, dispense e registro; dopo il lancio dei produttori, tuning e
configurazione naff, compaction, fair server e tick. Una fase vuota, un nome
ignoto o un'operazione appartenente a un'altra fase falliscono prima di
eseguire comandi. Ogni operazione è richiamabile anche singolarmente.
Il codice ritorna 1 al primo errore, 0 al completamento delle sole operazioni
richieste. Nessuno dei due esiti certifica la readiness della catena o del banco.

`tools` associa i nomi degli strumenti ai loro eseguibili. Quelli di sistema
sono cercati nel PATH; quelli esterni vanno dichiarati esplicitamente.
Non si interpretano comandi shell. Il file è configurazione amministrativa
fidata: può indicare file sysfs/procfs e strumenti privilegiati.
Non viene letto `machine.sh`, né importata la variante storica, né scelto un
DB dal nome macchina. Il pacchetto del ticket 02 installa modelli inattivi;
nessuna configurazione storica viene attivata. Differenze di NUMA, BDF, CPU,
pool, filesystem, gruppi e DB vanno riportate esplicitamente nella
configurazione dell'host.

Schema delle sezioni (i percorsi elencati sono campi JSON, non valori attivi):

| Operazione | Fase | Configurazione |
| --- | --- | --- |
| `mounts` | prepare | `entries`: oggetti `target`, `source`, `type`, `options`; hugetlbfs per i pool, tmpfs per log e dispense; verifica il tipo sul mount esatto |
| `hugepages` | prepare | `pools`: oggetti `path` del contatore `nr_hugepages`, `count`; riserva e rilegge ogni pool |
| `libraries` | prepare | `nodes`: nodi NUMA; strumenti `libhot_setup`, `numactl`; crea sul nodo richiesto e richiede `--verifica` riuscito |
| `registry` | prepare | `mount`, `path`, `node`; strumenti `numactl`, `cruda_init`; richiede hugetlbfs già montato e la pubblicazione del file |
| `kernel` | prepare | `unload_modules`, `load_modules`, `values`: mappa file→valore; per attributi leggibili a valore singolo (SMT, EDAC, sysctl, maschere workqueue); ogni scrittura viene riletta |
| `system-cpuset` | prepare | `units`: solo system.slice/user.slice/init.scope; `cpus`; verifica EffectiveCPUs; usare solo prima del lancio e con la catena fuori da system.slice |
| `kernel-jail` | prepare | `cpus`, `workqueue_cpus`; strumento esterno `kernel_jail` con interfaccia `applica CPU WQ_CPU` |
| `network` | prepare | `interfaces`, `route_flush`: abilita e verifica tx-nocache-copy, poi invalida la route cache |
| `x710` | prepare | `interface`, `queues`: oggetti `xps_path`, `mask`, `irq_path`, `irq_cpus`; includere anche le code da azzerare; richiede le assegnazioni CPU già definite, dopo kernel-jail; verifica XPS, IRQ e RSS sulla coda 0 |
| `e810` | live | `bdfs`, `tph` booleano, `parameters` lista argomenti; strumenti `setpci`, `dpdk_master_tune`; dopo inizializzazione EAL/code e prima della readiness |
| `master-knobs` | live | `values`: mappa chiave cruda→valore; include termostato e TX Flush di ciascun master, senza passare da devx |
| `naff-knobs` | live | `bases`: prefissi delle flotte effettivamente avviate; `groups`, `pipeline_stages`, `sideband_floor`, `waterfall_mask`; richiede chiavi naff pubblicate |
| `real-beam` | live | nessuna sezione: disattiva SynthMode e scollega SynthSimBase; richiede ebpm pubblicante, nessun simulatore |
| `compaction` | live | strumento `disable_kcompact`; dopo avvio della catena, prima del fair server |
| `fair-server` | live | `runtime_paths`, `runtime_ns` positivo; nessun valore storico implicito; runtime zero escluso finché non si integra il watchdog |
| `tick-revalidate` | live | nessuna sezione: rivaluta soltanto thread FIFO dei produttori dpdk_master/dpdk_ebpm/naff-ccd e ripristina la priorità; nessun thread trovato è un errore |
| `synthetic-beam` | bench | `sim_base`, `action_j`, `noise_nm`, `qs`, `mode`; richiede prima la chiave `sim_base__Qx`, poi configura il feed e abilita per ultimo il modo |
| `smart-rescan` | bench | `tango_host`, `devices`: nomi tcbuf completi; richiede PyTango nello strumento `python3`; propaga errori dei comandi |

Le scritture cruda richiedono lettura di ritorno nel formato storico
`chiave = valore`. L'uscita zero senza chiave leggibile è un errore.
Per strumenti esterni non disponibili il comando fallisce esplicitamente.
Le chiamate a strumenti devono avere i mount e l'ambiente librerie disponibili
prima dell'esecuzione; le unit del ticket 02 dovranno dichiararli.
La verifica NUMA fisica del registro e delle dispense resta obbligatoria
sull'hardware: la presenza del file non dimostra la collocazione delle pagine.

## Classificazione completa del percorso storico

M = obbligatorio quando l'operazione è selezionata per l'host; D = diagnostico,
da eseguire separatamente senza usarlo come consenso per Starter. L'esito
storico spesso era soltanto un messaggio: i nuovi passi M devono fallire.
Le righe marcate «adattare» descrivono responsabilità dei ticket successivi,
non funzionalità già installate da questo prefactoring.

| Riferimento storico | Prerequisiti | Destinazione / esito |
| --- | --- | --- |
| machine.sh, DEFCON, mappa CHAIN_MAP | configurazione dell'host | prepare, M: configurazione esplicita; niente default DEFCON o snapshot attivati |
| [0-cgroup], ldconfig, [0] bringdown | cgroup, share montata | adattare al contesto delle unit e all'ambiente loader (02); teardown escluso dall'avvio, nessuna fuga automatica nel cgroup radice |
| [1b], [2] V4/SMT/EDAC | macchina senza produttori | prepare, M: `kernel`; rimozione fuse richiede prima smontaggio dedicato (02) |
| [1c] pool 2M/1G e mount | memoria libera, nodi corretti | prepare, M: `hugepages`, `mounts`; mai azzerare pool in uso |
| [1c]/[1c-bis] preload lib-rt/lib-hk, [2b] dispense | share e cpuset/mems corretti | prepare, M: `libraries`; consolidare in dispense per nodo, non precaricare la medesima page cache da nodi diversi (02) |
| [2] pulizia shm obsolete | catena ferma | fuori dal nuovo avvio; non cancellare shm o lock come preparazione implicita |
| [2a], [S1] registro cruda | mount hugetlbfs e pool pronti | prepare, M: `registry`; verifica allocazione NUMA reale in 02 |
| tuning-os THP, compaction proattiva, reclaim | kernel compatibile | prepare, M: valori scalari `kernel`; selettori come THP richiedono verifica specifica in 02 |
| tuning-os detach NVMe/SATA/USB | inventario reale root/mount/swap | adattare 02, M se richiesto: non rimuovere tutte le periferiche per classe copiando assunzioni diskless |
| vfio, bind E810 | PCI/driver, gruppi IOMMU validi | prepare, M: caricamento vfio con `kernel`; bind esplicito delle porte nel lancio statico 03 |
| [2d] timesync in RAM, [2e] servizi NFS, [2f] clock | mount, sincronizzazione prima del TSC | adattare 02, M: mount tmpfs con `mounts`, drop-in runtime e dipendenze time-sync nelle unit; nessuno stop indiscriminato dei servizi |
| [2g] tx-nocache-copy | schede di gestione nel kernel | prepare, M: `network` |
| [2h] check_cmdline | riferimento cmdline aggiornato | D: non importare boot/cmdline.conf storico come configurazione corrente |
| [3]/[4], [S0]–[S4] TSC/master/ebpm | mount, pool, librerie, clock e catalogo | M, lancio e risorse tramite unit statiche in 03; non richiamare bringup_splitsix né i launcher systemd-run |
| [S0] guardie, check_catalog | binari multi-master, catalogo allineato | M, contratto da preservare in 03; numero processi non sostituisce risorse/registrazioni |
| [S5], [S7], thread di controllo dopo [6d] | tutti e sei i master vivi, ebpm registrato | live, M: verificare parametri effettivi e thread su ogni istanza in 03; escludere il vecchio pgrep/head sul primo master |
| [S6]/[S6b] TPH e tuning E810 | EAL e code già inizializzate | live, M: `e810`; evitare reset dopo il tuning |
| [S6c] TX Flush, termostato dopo [7] | chiavi master disponibili | live, M: `master-knobs` su sei prefissi, non solo primo master |
| [S6d] RatePct/ApplyAtS/RateAppliedPct | tutti i master pubblicanti | live, M se selezionato: transazione sincronizzata e read-back da integrare in 03; una semplice scrittura singola non equivale al protocollo storico |
| [5] imcmon, tcbufmon | risorse dei produttori | opzionali, D: monitoraggio; non prerequisiti della readiness |
| [5b] flotta naff, setup_banco [3] flotta veloce | shm ebpm, un creatore per flotta, etichette pubblicate | M: lancio statico in 04; preservare sequenza creatore/attach, non copiare start_flotta_veloce sotto bench |
| [6], [6b], [6c], [10] devx/tcbuf/guardie e rilanci | chiavi/regioni già disponibili | post-Starter: avvio e rilanci solo tramite Starter (05–08); nessun launcher Tango estratto |
| [6d] HA e suo devx | configurazione HA esplicita, master/ebpm | opzionale: separare produttore da devx; non attivare HA implicitamente, integrazione richiede contratto separato |
| [7] elenco processi, Ready nei log, naff_10s, parametri log | include devx e tool assente | D: non costituisce readiness; non riusare invariato |
| [8] compaction | produttori avviati, prima fair server | live, M se selezionato: `compaction` |
| [9] catds, jail catds; setup_banco [1] | DB/istanza corretti, Starter disponibile | post-Starter, M per banco selezionato: avviare da Starter, attendere pubblicazione chiavi, nessun kill/exec diretto (08) |
| setup_banco [2], SynthMode finale [9] | ebpm; anche catds se segue simulatore | live `real-beam` scollega il simulatore; bench `synthetic-beam` richiede Qx. M: una sola configurazione esplicita, niente BANCO_SYNTH poi SYNTH divergenti |
| setup_banco [3b–d], override finale [9] | chiavi delle flotte e gruppi già pubblicati | live, M: `naff-knobs`; stadi, pavimento sideband e maschere non richiedono Tango né catds |
| setup_banco [4] tcbuf restart | flotte e campi disponibili | post-Starter: rimosso il restart diretto; responsabilità di Starter (08) |
| setup_banco [5] SmartRescan | tcbuf pronto e campi pubblicati | bench, M: `smart-rescan`; fallisce se i prerequisiti non sono ancora pronti; attesa funzionale in 08 |
| VERDETTO e lettura termostato | include server Tango | D: separare report dal consenso realtime |
| [10b] sysctl | kernel pronto | prepare, M: `kernel` (prima dei produttori) |
| [11] journald | log disponibili in RAM | adattare 02: log tmpfs tramite `mounts`; preferire configurazione volatile al vecchio stop riattivabile |
| [12] check_numa, [13] check_imc | processi vivi e strumenti hardware | D: non promuovere i risultati storici a gate; verifiche hardware separatamente |
| fair server, QUIET_NODE0 | compaction finita, console/watchdog per runtime zero | live `fair-server` con runtime positivo; runtime zero, RTWD, stop timesync/timer/dbus esclusi finché 02 non integra protezioni e dipendenze |
| [12] tick revalidation, [12b] tick watchdog/RAM root | thread FIFO vivi, fair server completato | live, M: `tick-revalidate`; watchdog eventuale come unit dedicata in 02, non avviato implicitamente |
| [14a] migratepages | mapping condivisi noti | non riusare: migra anche memoria condivisa catds; politica privata/accesso condiviso distinti (06) |
| [14] check_invariante_rt | include catds, contesto hardware | D: non riusare come readiness né introdurre controllo collocazione in produzione |
| [15] jail applicativi Tango | processi già avviati nello storico | sostituito da profilo pre-exec di Starter (05–06); nessuna rincorsa dei PID |
| [15] sistema/workqueue/IRQ, [16] kernel jail | CPU di sistema configurate | prepare, M: `system-cpuset`, `kernel`, `kernel-jail`; catena e futuri cgroup Tango fuori dal system.slice confinato |
| [16b] X710 | IRQ kernel già configurati, CPU assegnate ai futuri cgroup | prepare, M: `x710`, da valori espliciti anziché cgroup di server già vivi; 02/06 definiscono la gerarchia |
| rtchain.service ExecStartPost rtchain-gate.sh | controllo misto dopo Tango | non riusare; nuovo riscontro funzionale senza Tango in 04, aggregando tutti i produttori |

## Dipendenze esterne e verifiche mancanti

Nel workspace non sono forniti `cruda_init`, `tsc_init`, `crudaknob`,
`dpdk_master`, `dpdk_ebpm`, `naff-ccd`, `dpdk_master_tune`, gli strumenti
`libhot_setup.sh`/`libhot_run.sh` e i launcher del repository CS. Le copie
storiche in `bring-updown` non dimostrano quale binario sia installato.
`disable_kcompact.sh` e `jail_kernel.py` sono sorgenti di riferimento presenti;
prima di distribuirli vanno forniti come dipendenze esplicite con il loro
ambiente e verificati sul kernel FAST. Nessuno script completo viene sourced.

`rtchain-gate.sh` è esterno al repository e include dipendenze Tango nel
controllo descritto dai PDF. `tcbuf_seq_sync` è assente e il suo vecchio elenco
CPU occupa risorse realtime (inclusa CPU 128): non va rilanciato invariato.
Il programma della prova dati del passo [7] del PDF è anch'esso esterno.
Il ticket 04 deve fornire un controllo funzionale autonomo verificabile;
nessuna di queste assenze autorizza un successo implicito.
PyTango serve solo a `smart-rescan`; la sola libreria standard basta per
prepare/live. Le verifiche di collocazione, traffico IMC, jitter e cgroup
reali non sono dimostrate dal collaudo controllato e non diventano nuove
funzioni di monitoraggio del prodotto.

```sh
python3 -m unittest discover -s tools/rtchain/tests -v
```

Il collaudo esegue la CLI contro risorse temporanee e strumenti controllati,
senza privilegi e senza Tango. Copre isolamento delle fasi, configurazione
host, errori e chiavi non risolte; non sostituisce il collaudo hardware né
quello systemd → Starter dei ticket successivi.
