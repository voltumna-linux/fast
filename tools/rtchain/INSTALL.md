# Preparazione statica della macchina FAST

Il pacchetto `fast-rtchain` è incluso nelle immagini runtime e sviluppo FAST.
Installa gli strumenti Python, tre unit statiche e una barriera target, il
generatore delle dipendenze dell'host e journald volatile con limite 128 MiB.
Non abilita il bring-up storico. I produttori, la readiness e Starter saranno
integrati dai ticket 03–07; questa preparazione da sola non avvia la catena.

## Configurazione dell'host

Copiare il modello corrispondente da `/usr/share/fast/rtchain/examples/` in
`/etc/fast/hosts/fast-01.json` oppure `fast-02.json`. Il nome viene risolto
dall'hostname corto: non esiste fallback a un altro host. I modelli sono
intenzionalmente incompleti (`REPLACE`, pool zero, `commissioned: false`):
non contengono una variante storica attivabile. Completare ogni valore con
l'inventario della macchina e impostare `commissioned: true` soltanto dopo
la verifica amministrativa. Il JSON deve risiedere nel filesystem locale,
essere amministrato da root e non essere scrivibile dagli utenti applicativi.

`requires_mounts_for` deve comprendere tutti i percorsi sorgente esterni
usati dagli helper: binari, configurazioni, cataloghi e librerie, anche quelli
letti indirettamente da `libhot_setup`. Il generatore aggiunge automaticamente
anche i percorsi assoluti in `tools` e `library_path`. Definire i filesystem
esterni in `/etc/fstab` o in unit `.mount`, con `_netdev` dove necessario per
NFS. `RequiresMountsFor` aggiunge requisiti e ordinamento sui mount che
forniscono quei percorsi; `After=remote-fs.target` da solo non basta.
Non usare mount ignorati o automount come sostituto di questi requisiti.
Nessun helper proveniente dalla share viene eseguito dal generatore.

Le liste di risorse usano numeri e intervalli separati da virgole. Specificare
in `resources.rtchain` e `resources.tango` CPU e nodi memoria disponibili.
Le due slice sono figlie dirette della radice, sorelle di `system.slice` e
`user.slice`; il confinamento del sistema non le restringe. Il nodo del
registro deve essere consentito in entrambe. Includere anche gli altri nodi
delle regioni condivise consumate da Tango: `AllowedMemoryNodes` non è la
politica di allocazione privata del server. Non sono impostati vincoli di
memoria su `system.slice`, né eseguite migrazioni globali di pagine.
La creazione dei cgroup per istanza, i permessi di Starter e la politica NUMA
privata appartengono al ticket 06; `tango.slice` ne predispone il confine.

`time_sync_units` elenca servizi la cui conclusione riuscita certifica la
sincronizzazione effettiva prima del TSC, per esempio il servizio di attesa
del demone in uso. Non basta elencare un demone che diventa attivo prima della
sincronizzazione. Il generatore li richiede e ordina la preparazione dopo di
essi; non ferma servizi NFS, clock, D-Bus, timer o journald. La preparazione
è ordinata dopo `force-irq-affinity.service`, perché l'ultima impostazione
IRQ deve essere quella specifica dell'host.

## Contratto delle operazioni

Il formato dei singoli passi resta quello di [README.md](README.md). La nuova
CLI `rtchain-prepare` esegue soltanto `phases.prepare`, dopo la validazione
dell'intero contratto. Mount, pool hugepage, dispense, registro e confinamento
dei servizi sono obbligatori e non possono essere liste vuote. Per ogni altra
operazione prepare occorre selezionarla oppure scrivere una motivazione in
`not_applicable`. Non è ammessa una fase live/bench dentro prepare.

- `mounts` crea soltanto tmpfs e hugetlbfs locali e ne verifica il tipo. Deve
  precedere dispense e registro; il pool deve precedere il registro. Impostare
  esplicitamente pagesize, dimensioni, proprietari e gruppi. Il mount del
  registro deve corrispondere alla sezione `registry`. `log_directory` deve
  avere un mount tmpfs dedicato; usare `/run/rtchain-log` con gli helper storici.
- `libraries` richiede gli helper esterni `libhot_setup` e `numactl`, lanciando
  la creazione con CPU e memoria sul nodo selezionato e poi `--verifica`.
  L'helper deve creare copie distinte di codice e librerie per nodo, senza
  condividere la stessa page cache tra dispense. Deve fallire se copie,
  fingerprint o verifica non riescono. Non è fornito nel workspace: distribuirlo
  sulla share insieme alle sue dipendenze. Verificare il suo contratto prima
  di commissionare l'host. La sua assenza impedisce la preparazione.
- `registry` richiede `cruda_init` esterno e pubblicazione del file nel mount
  hugetlbfs esatto, sotto `numactl` sul nodo indicato. Configurare permessi e
  gruppi per tutti i futuri consumatori. Le librerie degli helper sono cercate
  nell'elenco esplicito `library_path`, dopo i mount. I launcher dei produttori
  dovranno usare le dispense appropriate; questa CLI non modifica il loader
  globale con `ldconfig`.
- `kernel.values` tratta valori scalari. `kernel.selectors` tratta interfacce
  come THP: dopo la scrittura richiede il valore attivo tra parentesi quadre.
  Configurare SMT, EDAC, reclaim, compaction proattiva e workqueue secondo il
  kernel installato. Non scaricare `fuse` se ospita filesystem in uso; un
  eventuale smontaggio deve essere una dipendenza esplicita dedicata. Non
  viene eseguito detach indiscriminato di NVMe, SATA o USB. Se il deployment
  richiede detach, serve prima una unit specifica basata sull'inventario di
  root, mount e swap, richiesta e ordinata prima di questa preparazione.
- `kernel-jail` usa un helper esplicito `kernel_jail applica CPU WQ_CPU`,
  proveniente dalla share. Il sorgente di riferimento è
  `bring-updown/common/jail_kernel.py`: conserva il read-back e la diagnosi
  dei thread/IRQ non spostabili; qualificare le eccezioni del kernel reale.
  X710 segue questo passo, con tutte le code comprese quelle da azzerare e
  assegnazioni ricavate dai futuri profili CPU, senza interrogare PID Tango.

E810 TPH/tuning dopo inizializzazione EAL, compaction, fair server e
tick-revalidate restano in `live`, dopo i produttori (ticket 03–04). Il runtime
fair server deve restare positivo: zero, stop dei servizi e watchdog storici
non sono varianti supportate da questa preparazione. Non si attivano watchdog
RAM o processi residenti senza un contratto di supervisione. Il binding VFIO
delle porte E810 appartiene al lancio statico del ticket 03.

## Avvio e diagnosi

Dopo aver predisposto configurazione, helper e mount:

```sh
systemctl daemon-reload
systemctl start rtchain-producers.target
systemctl status rtchain-prepare.service
journalctl -b -u rtchain-prepare.service
systemctl cat rtchain-prepare.service rtchain.slice tango.slice
```

Le unit sono statiche: non eseguire `enable`. Le future unit dei produttori
devono avere `Requires=rtchain-prepare.service` e
`After=rtchain-prepare.service` e usare `Slice=rtchain.slice`. Il target
`rtchain-producers.target` espone la stessa barriera per l'avvio manuale;
raggruppare unit sotto un target non sostituisce il requisito nel produttore.
Un mount richiesto fallito impedisce l'esecuzione del comando; un errore
obbligatorio restituisce stato fallito e identifica fase e causa nei log.
Una configurazione invalida fa generare anche un `ExecStartPre=/bin/false`:
un errore del solo generatore non bloccherebbe una transazione systemd.
La diagnosi del generatore compare nel journal del boot/daemon-reload.

`RemainAfterExit=yes` conserva l'esito nel boot. Non riavviare la preparazione
con produttori o consumatori vivi: non effettua teardown, pulizia shm o
azzeramento dei pool. Dopo una preparazione parziale correggere la causa e
ripartire da un boot pulito. Stop e riavvio durante il servizio sono esclusi.

## Verifica controllata e collaudo reale

```sh
python3 -m unittest discover -s tools/rtchain/tests -v
```

La suite verifica CLI e output del generatore con risorse controllate: mount
prima dell'uso, dipendenze di clock, errore mount, errore dispense, host errato,
configurazione incompleta e accesso al nodo condiviso. Non esegue un boot.
Nell'ambiente di sviluppo PID 1 non è systemd e non è presente una build
BitBake configurata. Il corpo `do_install` è stato eseguito in staging;
`systemd-analyze verify` è stato tentato ma bloccato dalle restrizioni sui
socket dell'ambiente. Le verifiche dell'immagine e del boot restano da eseguire:

1. Costruire l'immagine FAST e controllare manifest/RDEPENDS e installazione
   di unit, generatore, Python, numactl, mount/findmnt, ethtool, modprobe,
   setpci e client NFS. Confermare configurazione kernel/cgroup v2 e cpuset.
2. In una VM systemd, fornire mount e clock controllati, quindi richiedere la
   barriera da un servizio produttore fittizio. Ritardare il mount e verificare
   che nessun helper vi acceda prima; far fallire il mount e poi la verifica
   delle dispense: il produttore deve restare fermo in entrambi i casi.
3. Su ciascun host reale archiviare topologia CPU/NUMA, cmdline e inventario
   PCI; controllare `nr_hugepages` e `free_hugepages` per nodo e dimensione,
   pagesize dei mount, spazio disponibile, gruppi e permessi. Confrontare
   pagine realmente allocate con il pool richiesto, comprese quelle da 1 GiB.
4. Verificare collocazione NUMA fisica delle copie di codice/librerie e del
   registro (mapping di prova e `numa_maps`/strumento di verifica dell'helper),
   impronte delle dispense, accesso al registro da entrambi i nodi e assenza
   di migrazioni della memoria condivisa. Il solo file presente non basta.
5. Verificare EffectiveCPUs/EffectiveMemoryNodes delle slice, collocazione
   dei servizi, maschere IRQ effettive, workqueue, X710 XPS/RSS e impostazioni
   kernel. Dopo EAL verificare separatamente VFIO/E810, tuning live, fair server
   e tick. Annotare eccezioni gestite dal kernel e misurare jitter sull'hardware.

La readiness realtime e la prova systemd → Starter → server Tango rimangono
nei ticket successivi. Il successo di questi test non certifica prestazioni
realtime, allocazioni fisiche NUMA o disponibilità completa del banco.
