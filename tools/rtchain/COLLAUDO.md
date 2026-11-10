# Collaudo integrato di fast-01 e fast-02 (#9)

Ripetere separatamente sui due host, in una finestra di prova concordata,
da boot pulito. La migrazione dal monolite è descritta in [STARTUP.md](STARTUP.md).
Non eseguire pulizia SHM, stop o riavvio della catena in servizio. Le prove
con guasti e server di prova si eseguono nella VM usa e getta e con il DB
isolato descritti in quel documento.

## Identità della prova

Creare un archivio per host e boot, per esempio
`/var/tmp/collaudo/fast-01/BOOT_ID/`. Registrare operatore, data UTC, hostname,
boot ID e risultato di ogni passo: PASS, FAIL oppure NON ESEGUITO, con
valore atteso, valore osservato e percorso delle evidenze grezze. Conservare
comandi, stderr ed exit status. Un comando assente lascia il criterio non
verificato. Limitare l'accesso ai dati amministrativi nell'archivio.

Prima del boot archiviare:

- Revisioni Git e stato delle modifiche di FAST, Starter e bring-updown.
  Quest'ultimo resta un riferimento storico, non un componente da attivare.
- Identificativo e manifest dell'immagine installata, manifest pacchetti,
  configurazione kernel, cmdline, `bitbake -e starter`, `bitbake -e fast-rtchain`
  e log do_patch/do_compile dalla build corrispondente.
- JSON amministrativo effettivo dell'host, SHA256, eventuali symlink, drop-in
  locali, catalogo, calibrazione e ambiente loader. Versioni e checksum dei
  binari effettivamente eseguiti dalla share, helper e adattatore `rtchain_probe`.
- Inventario letto dal DB esplicito dell'host: eseguibile/istanza, host
  registrato, livelli, avvio automatico e proprietà `NativeLaunchProfile`
  di ciascun `dserver/...`, confrontati con `tango_servers.profiles`.

Non usare il checkout come prova della revisione installata: registrare
SRCREV e patch effettive, anche quando Starter locale e ricetta differiscono.
Completare e qualificare tutti i segnaposto prima di commissionare l'host
come in [INSTALL.md](INSTALL.md). Non copiare automaticamente CPU, BDF,
catalogo, DB o parametri dall'altro host. Distinguere catds fast01/fast02,
destinatari tcbuf e parametri del generatore.

## Preparazione fisica

Acquisire i dati prima e dopo l'avvio e confrontarli con la configurazione
approvata dell'host. Per ogni esclusione registrare la motivazione specifica
coerente con `not_applicable`.

| Criterio | Evidenze richieste |
| --- | --- |
| Mount | `findmnt`, sorgente/tipo/opzioni, `RequiresMountsFor`, accesso a binari/input nel contesto delle unit |
| Log in RAM | Percorso risolto di `log_directory` dei produttori su tmpfs dedicato; configurazione effettiva di journald e journal del boot in storage volatile |
| Hugepage | `nr_hugepages`, `free_hugepages`, `surplus_hugepages` per nodo/dimensione, budget e consumo, mount hugetlbfs e permessi |
| Dispense e registro | `libhot_setup --verifica` qualificato per nodo, collocazione fisica delle pagine, UID/GID e accesso da controls |
| CPU e NUMA | `lscpu -e`, `numactl --hardware`, CPU/nodi online e cpuset effettivi sistema/realtime/Tango rispetto alla mappa prevista |
| Sei E810 | BDF, vendor/device, driver, gruppo IOMMU, nodo NUMA e master/porta assegnati; EAL/code inizializzate e read-back TPH/tuning dopo EAL |
| Kernel e periferiche | Cmdline e valori SMT, EDAC, THP, reclaim, moduli, detach espliciti, IRQ, workqueue, X710 IRQ/XPS/RSS e tx-nocache-copy selezionati |
| Fase live | Esiti e valori di compaction, fair server positivo, tick e manopole su tutti i master e dodici gruppi naff |

Una mappa virtuale presente non dimostra il nodo fisico delle pagine.
Registrare versioni, comandi e output degli strumenti NUMA/PCI qualificati
usati per verificare registro e dispense.

## Ordine di avvio e dati prima di Starter

Prima dell'avvio verificare maschera del monolite, assenza di launcher
concorrenti e dipendenze secondo [STARTUP.md](STARTUP.md). Eseguire l'avvio
previsto dall'immagine una sola volta nel boot pulito. Acquisire:

```sh
hostname
date -u
cat /proc/sys/kernel/random/boot_id
uname -a
cat /proc/cmdline
systemctl cat rtchain-prepare.service rtchain-ready.service starter.service rtchain-bench.service
systemctl show starter.service -p Requires -p After -p Conflicts -p ExecStart
systemctl list-units --all 'rtchain-*'
systemctl show 'rtchain-*.service' starter.service \
  -p ActiveState -p SubState -p Result -p ExecMainStatus -p MainPID \
  -p ExecMainStartTimestampMonotonic -p ExecMainExitTimestampMonotonic \
  -p ActiveEnterTimestampMonotonic -p ControlGroup
journalctl -b -o short-monotonic --no-pager
```

Archiviare anche `systemctl cat` di TSC, master, ebpm, naff e relativi drop-in
generati. Dimostrare completamento di prepare, preflight, TSC, master 0–5,
ebpm, producer-live, dodici naff, final-live e ready prima dell'ingresso
nel comando Starter. Per il oneshot ready conta il completamento riuscito,
non il timestamp del suo inizio.

Verificare [PRODUCERS.md](PRODUCERS.md) e [READINESS.md](READINESS.md):

1. MainPID stabili, SHM/EAL, chiavi e log di questa esecuzione dei sei master;
   registrazioni FILL e DRAIN dell'ebpm su ciascuno.
2. Dodici gruppi 1,9,17,…,89, SHM, inizializzazione e corrispondenza delle
   etichette spettro/tune con i consumatori.
3. Adattatore qualificato per ABI e identità dei buffer: progressione su
   master0…master5, ebpm e tutti i naff. Conservare due campioni grezzi,
   intervallo e journal della barriera. Se l'adattatore non salva campioni,
   predisporre prima della prova un suo log diagnostico su file separato,
   preservando stdout JSON ed esito. Campioni successivi a Starter non
   dimostrano la readiness precedente al suo avvio.
4. Nessuna richiesta Tango nella readiness. Anche se è previsto un banco
   sintetico successivo, il flusso iniziale deve essere disponibile senza catds.

## Profili e banco

Seguire [TANGO.md](TANGO.md) per NativeProfileProbe in ambiente isolato:
due istanze, avvii singoli/per livelli/automatici, profilo all'ingresso,
confinamento di thread/figli, memoria privata e condivisa. Una lettura di
`/proc` a posteriori non prova l'applicazione prima dell'exec.

Sul boot hardware verificare Starter in `supervisor`, radice delegata vuota,
server nei gruppi previsti, cpuset CPU/mems effettivi, scheduling e permessi.
Usare un consumatore qualificato per provare accesso al vero registro
hugetlbfs sul nodo 1 con memoria privata sul nodo 0, senza migrare pagine
condivise. Il probe tmpfs costituisce una prova complementare.

Per il banco confrontare DB, istanze, parametri e destinatari approvati per
questo host. Archiviare separatamente stato/journal di `rtchain-bench.service`,
attesa dei prerequisiti Tango/campi, read-back e SmartRescan secondo
[BENCH.md](BENCH.md). Registrare due verdetti distinti: readiness realtime
e banco configurato. Un banco fallito non cancella la readiness riuscita;
un banco disabilitato lascia non verificato il collaudo completo del banco.

## Fallimenti e ripetibilità

Eseguire nella VM usa e getta i runner di [STARTUP.md](STARTUP.md) e
[TANGO.md](TANGO.md). Archiviare comando, fixture, exit status, directory dei
risultati, job, journal e report dei probe. La suite locale si ripete con:

```sh
python3 -m unittest discover -s tools/rtchain/tests -v
```

| Scenario | Esito richiesto e sede della prova |
| --- | --- |
| Prepare exit 31; ready exit 32 | VM systemd: Starter mai eseguito, nessun report del server, job fallito e causa a monte |
| Readiness trattenuta | VM systemd: nessun ingresso Starter durante l'attesa, poi successo e livelli nell'ordine previsto |
| Risorsa/registrazione mancante, naff incompleto, dati fermi, etichette perse, probe bloccato | Suite readiness controllata; ripetere con produttori controllati in VM verificando Starter non eseguito |
| Profilo invalido, cgroup assente, permessi insufficienti | Runner profili: server reale non eseguito ed errore osservabile in DevReadLog |
| Prerequisito banco mancante, scrittura o SmartRescan falliti | Suite banco controllata; qualificare anche con PyTango/DB di prova, senza banco pronto e con readiness distinta |

La suite Python controlla CLI e grafo, non la transazione systemd. Non
iniettare guasti sugli host in servizio. Per ripetere un avvio hardware
fallito correggere la causa e predisporre un nuovo boot pulito. Non sono
richiesti stop ordinato o monitoraggio della collocazione. Le prestazioni
realtime richiedono misure dedicate e non sono certificate da questa sequenza.

## Verbale per ciascun host

Copiare questa tabella nell'archivio e completarla con confronto
atteso/osservato e riferimento alle evidenze.

| Voce | Esito iniziale |
| --- | --- |
| Host, boot ID, data, operatore, finestra | NON ESEGUITO |
| Immagine, revisioni installate, checksum/configurazione e DB | NON ESEGUITO |
| Preparazione fisica, NUMA, hugepage, dispense, kernel, E810 | NON ESEGUITO |
| Sei master, registrazioni ebpm, dodici naff | NON ESEGUITO |
| Dati prima di Starter e ordine di avvio | NON ESEGUITO |
| Profili iniziali, confinamento, memoria condivisa reale | NON ESEGUITO |
| Readiness realtime | NON ESEGUITO |
| Configurazione completa del banco | NON ESEGUITO |
| Fallimenti in ambiente isolato, log e revisioni della suite | NON ESEGUITO |

Chiudere #9 solo dopo i due verbali completi e le prove isolate eseguite.
