# Avvio NUMA in due fasi: CPU e RAM inizialmente sul nodo 0

Ricerca del 2026-11-09 sulle interfacce upstream Linux 6.18, con controlli di sorgente sul tag stabile `v6.18.33` (commit `83657f4189612e5cbcabc3058acd36c0bd120729`). Le citazioni web sono fissate a `v6.18`, salvo diversa indicazione. Questo documento descrive un progetto da validare: non costituisce una prova sulla topologia o sul funzionamento dell'hardware di destinazione. Non sono state cambiate configurazioni di avvio né eseguite operazioni di hotplug.

## Esito e significato della richiesta

La parte CPU dispone di un meccanismo upstream per ritardare l'attivazione dei processori. La parte RAM richiede invece un percorso specifico di esclusione e successiva aggiunta, oppure supporto firmware/hypervisor per presentare la memoria in un secondo momento. Non emerge una coppia di parametri generici che significhi «avvia soltanto CPU e RAM di node0 e riattiva gli altri nodi dopo il boot».

Occorre distinguere tre requisiti:

1. **Durante la prima fase:** soltanto le CPU del nodo 0 sono online e soltanto la RAM prevista per quel nodo è disponibile agli allocatori Linux.
2. **Prima delle applicazioni:** le altre risorse vengono aggiunte/attivate, le assegnazioni vengono verificate e soltanto allora viene aperta la barriera di avvio delle applicazioni.
3. **Dopo la seconda fase:** contenere kernel, servizi e interrupt nelle risorse di housekeeping. Questo è un ulteriore obiettivo di isolamento; non segue automaticamente dai primi due.

«Nessuna istruzione kernel sulle CPU applicative», comprendendo il loro stesso avvio, è incompatibile con l'onlining: le callback STARTING e ONLINE vengono eseguite sulla CPU che entra in servizio. Anche le chiamate di sistema dell'applicazione richiedono esecuzione kernel. L'obiettivo verificabile deve quindi specificare quali attività ridurre e quali eccezioni ammettere. [CPU hotplug, sezioni STARTING/ONLINE](https://raw.githubusercontent.com/torvalds/linux/v6.18/Documentation/core-api/cpu_hotplug.rst).

## Riscontri nel progetto FAST

Analisi del layer al commit `fdcb697184919f422f5e75aa69381a20625a6f83`, limitata alla macchina `d-9755-h14dsh`. Non è disponibile una directory di build né una sessione sulla macchina AMD: topologia effettiva, configurazione Kconfig risultante e comportamento hardware non sono stati rilevati. Il checkout locale separato del kernel è successivo alla versione richiesta; per i controlli di baseline è stato letto il tag `v6.18.33`, senza modificarlo.

- **Kernel:** [fast.conf](../conf/distro/fast.conf) richiede `linux-amd` 6.18.33; [la ricetta AMD](../meta-amd/meta-amd-bsp/recipes-kernel/linux/linux-amd-6.18.33.inc) fissa `SRCREV_machine=83657f4189612e5cbcabc3058acd36c0bd120729`, corrispondente alla baseline esaminata. Il [bbappend FAST](../recipes-kernel/linux/linux-amd_%.bbappend) aggiunge 31 patch locali: l'analisi upstream non sostituisce la verifica del sorgente e della configurazione finali prodotti da BitBake.
- **Boot attuale:** [GRUB AMD](../recipes-bsp/grub/files/d-9755-h14dsh/grub.cfg) isola `128-255,384-511`, indirizza gli IRQ a `0-127,256-383`, disabilita il bilanciamento NUMA automatico e richiede `hugepagesz=1G hugepages=512`. Queste maschere non provano la topologia corrente. La prenotazione anticipata di 512 GiB di hugepage va ridisegnata: non può essere mantenuta senza controllarne destinazione e capienza nella prima fase. Spostarla alla seconda fase può rendere più difficile ottenere pagine contigue da 1 GiB.
- **Patch di isolamento:** il flag locale `isolcpus=node` non è abilitato nella riga GRUB. La patch [0002](../recipes-kernel/linux/files/0002-percpu-Allocate-isolated-CPUs-percpu-pages-on-the-al.patch) lascia invariato il primo chunk per-CPU; la [0026](../recipes-kernel/linux/files/0026-mm-mempolicy-Keep-the-boot-interleave-off-isolated-nodes.patch) limita l'interleave di inizializzazione; la [0027](../recipes-kernel/linux/files/0027-mm-slub-Leave-isolated-nodes-slabs-to-their-CPUs.patch) lascia invariati richieste esplicite di nodo e nuove pagine slab; la [0028](../recipes-kernel/linux/files/0028-printk-Keep-the-log-buffer-off-isolated-nodes.patch) ammette fallback dell'allocazione del log. Abilitare questi flag non dimostra l'esclusività assoluta.
- **Hotplug e osservabilità:** [memory.cfg](../recipes-kernel/linux/files/memory.cfg) richiede `CONFIG_MEMORY_HOTPLUG=y` e `CONFIG_NUMA_KEEP_MEMINFO=y`; [amd-cpu.cfg](../recipes-kernel/linux/files/amd-cpu.cfg) richiede la politica offline per la memoria aggiunta. Sono frammenti, non la `.config` risultante. Il [profilo di produzione](../meta-voltumna/recipes-kernel/linux/linux-production.inc) include [disable_ftrace.cfg](../meta-voltumna/recipes-kernel/linux/files/disable_ftrace.cfg): un'immagine diagnostica deve abilitare esplicitamente la strumentazione necessaria e controllarne la configurazione finale.
- **Servizi:** il [bbappend systemd FAST](../recipes-core/systemd/systemd_%.bbappend) installa il servizio di affinità IRQ, ma non una politica NUMA globale. Nel repository adiacente bring-up/down esiste già `common/etc/systemd/system/user.slice.d/10-cpuset-node0.conf`, con `AllowedCPUs=0-127,256-383` e `AllowedMemoryNodes=0`: riguarda `user.slice`, e la sua presenza nel sorgente non prova l'installazione sul server o il vincolo degli altri servizi.

Per la seconda fase, la configurazione futura dovrà impostare `CPUAffinity`, `NUMAPolicy=bind` e `NUMAMask=0` per PID 1 e come default dei servizi, più cpuset di CPU e memoria per `system.slice` e `user.slice`. La slice delle applicazioni dedicate dovrà essere una sorella di queste, con risorse proprie: non potrà allargare limiti imposti da una slice antenata. Per i suoi processi andranno sovrascritti esplicitamente affinità e politica NUMA ereditate. Verificare anche servizi precoci ed eventuali programmi che cambiano autonomamente affinità/politica; questi controlli non coprono il kernel globale. [Configurazione del gestore systemd 259](https://www.freedesktop.org/software/systemd/man/259/systemd-system.conf.html), [controllo risorse systemd](https://raw.githubusercontent.com/systemd/systemd/v259/man/systemd.resource-control.xml).

## CPU: limite iniziale reversibile

`maxcpus=N` limita il numero di CPU portate online durante l'avvio e permette di attivare successivamente quelle escluse. `nr_cpus=N` limita invece il numero totale supportato: una CPU esclusa da questo limite non può essere recuperata via hotplug. Serve `CONFIG_HOTPLUG_CPU`; bisogna preservare l'intero insieme necessario di CPU `possible` e `present`. Le risorse per-CPU possono essere predisposte anche per CPU ancora offline. [Documentazione CPU hotplug](https://raw.githubusercontent.com/torvalds/linux/v6.18/Documentation/core-api/cpu_hotplug.rst).

Il valore di `maxcpus` è un conteggio, non una maschera NUMA. In `bringup_nonboot_cpus()` e `cpuhp_bringup_cpus_parallel()` vengono usate le CPU presenti e, quando previsto, le maschere dei thread SMT primari; non viene selezionato un nodo. Pertanto «numero di CPU del nodo 0» non significa «CPU del nodo 0», soprattutto con identificativi non contigui. [Sorgente `kernel/cpu.c`](https://raw.githubusercontent.com/torvalds/linux/v6.18/kernel/cpu.c).

Un candidato più controllabile è `maxcpus=1`, **a condizione che la CPU di boot appartenga al nodo desiderato**, seguito dall'attivazione delle altre CPU del nodo 0 nell'initramfs o in una fase iniziale ordinata. Non usare `maxcpus=0`: equivale a `nosmp` e su x86 disabilita anche IO-APIC. [Parametri kernel](https://raw.githubusercontent.com/torvalds/linux/v6.18/Documentation/admin-guide/kernel-parameters.txt).

L'attivazione individuale usa il file `cpuX/online`. Il programma che la coordina deve operare su un inventario verificato, controllare ogni risultato e ricontrollare l'insieme online completo. Una scrittura riuscita non dimostra l'isolamento successivo.

## RAM: cosa fanno realmente le opzioni disponibili

| Meccanismo | Effetto e limite per questo progetto |
| --- | --- |
| `movable_node` | Assegna a ZONE_MOVABLE la memoria identificata hotpluggable; non la rende inizialmente offline. |
| `kernelcore=` / `movablecore=` | Suddividono la RAM in zone; non selezionano node0 come unico nodo iniziale. |
| `memhp_default_state=offline` | Controlla l'onlining automatico della memoria aggiunta mediante hotplug; non disattiva la RAM ordinaria già scoperta al boot. |
| `mem=` | Su x86 limita l'indirizzo massimo utilizzabile, non un nodo NUMA. |
| `memmap=...` | Modifica intervalli di indirizzi fisici nella mappa iniziale; non fornisce da solo il successivo recupero della RAM. |

Le ultime due opzioni richiedono una mappa fisica reale; la sintassi di riserva è `memmap=dimensione$inizio`. Il carattere `$` richiede attenzione nel bootloader. Un semplice limite in byte non identifica necessariamente la RAM di un socket. [Definizioni dei parametri](https://raw.githubusercontent.com/torvalds/linux/v6.18/Documentation/admin-guide/kernel-parameters.txt).

`find_zone_movable_pfns_for_nodes()` distribuisce normalmente kernelcore fra i nodi e, con `movable_node`, ignora kernelcore/movablecore usando gli intervalli marcati hotpluggable. Non introduce una fase di RAM offline. [Sorgente `mm/mm_init.c`](https://github.com/torvalds/linux/blob/v6.18/mm/mm_init.c). La marcatura dipende, nel percorso ACPI, dal flag SRAT `ACPI_SRAT_MEM_HOT_PLUGGABLE`: essere un nodo diverso da zero non basta. [Parser SRAT](https://raw.githubusercontent.com/torvalds/linux/v6.18/drivers/acpi/numa/srat.c).

Il riscontro decisivo per `memhp_default_state=offline` è nel codice: `memory_dev_init()` registra i blocchi iniziali con `MEM_ONLINE`; `add_memory_resource()` applica invece la politica di onlining ai blocchi aggiunti successivamente. Spegnere blocchi tramite sysfs dopo un avvio ordinario non soddisfa «mai usati nella prima fase». [Registrazione dei blocchi iniziali](https://raw.githubusercontent.com/torvalds/linux/v6.18/drivers/base/memory.c), [aggiunta a runtime](https://raw.githubusercontent.com/torvalds/linux/v6.18/mm/memory_hotplug.c).

Inoltre `maxcpus` non impedisce allocazioni RAM sugli altri nodi: `numa_register_nodes()` prepara i nodi con memoria anche senza richiedere che le loro CPU siano online; l'allocatore per-CPU dispone unità per CPU possibili. [Inizializzazione NUMA x86](https://raw.githubusercontent.com/torvalds/linux/v6.18/arch/x86/mm/numa.c), [allocatore per-CPU](https://raw.githubusercontent.com/torvalds/linux/v6.18/mm/percpu.c).

Un controesempio concreto nella baseline 6.18.33 è `alloc_node_data(nid)`: richiede memoria preferibilmente sul nodo indicato e azzera la struttura con `memset()`. Lasciare tutta la RAM disponibile con `maxcpus=1` non evita quindi scritture di inizializzazione nei nodi remoti. Questo non dimostra l'impossibilità di un kernel modificato, ma invalida la garanzia basata sul solo limite CPU. [Sorgente esatto `mm/numa.c`](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/mm/numa.c?h=v6.18.33).

## Recuperare la RAM esclusa: possibilità condizionata

L'hotplug memoria distingue **aggiunta**, che crea mapping e metadati, da **onlining**, che espone le pagine all'allocatore. Un blocco escluso dalla mappa iniziale potrebbe non avere affatto un dispositivo `memoryXXX`: scrivere `online` presuppone che l'aggiunta sia già avvenuta. Le notifiche ACPI richiedono supporto della piattaforma. Il probing manuale opera su blocchi interi ed è esplicitamente sconsigliato upstream perché un indirizzo errato può causare un crash. [Memory hotplug 6.18](https://docs.kernel.org/6.18/admin-guide/mm/memory-hotplug.html).

Il percorso sysfs `memory/probe` esiste soltanto con `CONFIG_ARCH_MEMORY_PROBE`. `probe_store()` verifica l'allineamento, ricava il nodo dall'indirizzo e chiama `__add_memory()`: non è un comando generico per convertire qualunque riserva firmware in RAM utilizzabile. [Implementazione del probe](https://raw.githubusercontent.com/torvalds/linux/v6.18/drivers/base/memory.c).

`__add_memory()` deve registrare una risorsa System RAM; `add_memory_resource()` verifica intervallo, nodo possibile e mapping architetturale e può fallire. Queste condizioni non dimostrano che gli intervalli esclusi con `memmap` siano recuperabili sulla macchina concreta: vanno verificati conflitti con risorse riservate, proprietà dell'intervallo e percorso firmware/driver. [Implementazione dell'aggiunta](https://raw.githubusercontent.com/torvalds/linux/v6.18/mm/memory_hotplug.c).

Anche l'attribuzione NUMA richiede una prova: `memory_add_physaddr_to_nid()` consulta le informazioni conservate e, se non trova l'intervallo, restituisce il nodo del primo blocco di `numa_meminfo`. Il probe potrebbe quindi aggiungere RAM fisicamente remota come node0; non usa la diversa funzione `phys_to_target_node()`, che considera anche intervalli riservati. Su x86 l'inizializzazione modifica alcune associazioni dei nodi senza memoria e ricostruisce associazioni CPU/nodo. Non si può dedurre la topologia della seconda fase dalla sola topologia di un boot ordinario. [Informazioni degli intervalli NUMA](https://raw.githubusercontent.com/torvalds/linux/v6.18/mm/numa_memblks.c), [inizializzazione x86](https://raw.githubusercontent.com/torvalds/linux/v6.18/arch/x86/mm/numa.c).

**Stato:** l'esclusione iniziale più riaggiunta è una strada sperimentale, non una procedura pronta e verificata. Un percorso firmware/hypervisor supportato è un'alternativa da accertare. In assenza di entrambi, il requisito RAM rigoroso richiede progettazione nel kernel o nella piattaforma; le sole opzioni movable non lo implementano.

## Limiti dopo l'attivazione

Onlining non mantiene automaticamente i servizi sul nodo 0. Vanno gestiti separatamente affinità dei processi, cpuset, politica memoria, IRQ, workqueue e thread kernel. La documentazione upstream elenca mitigazioni specifiche per varie fonti di jitter, comprese quelle per-CPU; non promette assenza universale di attività kernel. [Riduzione del jitter dei kthread per-CPU](https://docs.kernel.org/6.18/admin-guide/kernel-per-CPU-kthreads.html).

Usare `online_movable` per la RAM applicativa può escludere molte allocazioni kernel ordinarie, ma non equivale a «nessun dato kernel fisicamente presente»: servono comunque metadati. Il percorso `memmap_on_memory`, quando utilizzabile, può ospitare metadati nella RAM aggiunta stessa. [Metadati e aggiunta memoria](https://raw.githubusercontent.com/torvalds/linux/v6.18/mm/memory_hotplug.c). La capacità residua di memoria utilizzabile dal kernel sul nodo 0 diventa quindi un vincolo da dimensionare e misurare.

Il flag globale `memmap_on_memory` non dimostra dove finiranno i metadati del probing manuale: `probe_store()` passa `MHP_NONE`, mentre il ramo di metadati nella memoria aggiunta richiede `MHP_MEMMAP_ON_MEMORY`. [Chiamante probe](https://raw.githubusercontent.com/torvalds/linux/v6.18/drivers/base/memory.c), [controllo del flag](https://raw.githubusercontent.com/torvalds/linux/v6.18/mm/memory_hotplug.c).

La garanzia va delimitata all'esecuzione Linux osservabile: affinità e hotplug Linux non costituiscono una garanzia per firmware, BMC o SMI. Anche collocazione dell'immagine, caricamento del bootloader e decompressione devono rientrare nell'analisi se «solo RAM di node0» decorre dall'inizio assoluto del boot, anziché dall'avvio degli allocatori ordinari.

## Modifiche necessarie per una successiva sperimentazione

Il risultato dello studio non è una configurazione distribuibile. Il lavoro successivo, subordinato all'inventario hardware, sarebbe:

- **GRUB:** aggiungere una voce sperimentale separata, mantenendo `hybridboot` come default; usare il rinvio CPU soltanto dopo aver verificato il nodo del processore di boot. Gli intervalli RAM e il trattamento delle hugepage devono essere ricavati dall'inventario, non dalle maschere CPU.
- **Kernel:** verificare prima se firmware e hotplug esistenti permettono aggiunta e attribuzione NUMA corrette. In caso contrario serve un percorso esplicito per conservare la topologia, escludere gli intervalli dagli allocatori iniziali e aggiungerli con nodo e risorse verificati. Vanno controllati anche immagine kernel, allocazioni memblock, dati dei nodi e primo chunk per-CPU: cambiare soltanto l'interleave tardivo non basta.
- **Coordinamento:** prevedere una fase iniziale che abiliti solo le CPU del nodo 0, e un servizio separato per l'attivazione delle risorse applicative. Le applicazioni devono dipendere dal successo della seconda fase; a un errore restano ferme, con diagnostica dell'eventuale stato parziale. Il recupero può richiedere il riavvio con la voce normale: non presumere di poter rimuovere tutta la memoria appena aggiunta.
- **Servizi e hugepage:** applicare la gerarchia systemd descritta sopra e prenotare le hugepage sul nodo richiesto solo quando la sua RAM è disponibile. Verificarne quantità e collocazione prima di aprire la barriera delle applicazioni; una prenotazione incompleta deve impedire l'avvio della catena.

## Protocollo di validazione proposto

Le seguenti sono condizioni di accettazione e passi per una prova futura; non sono operazioni eseguite.

1. **Fissare l'oggetto della prova.** Archiviare commit upstream/vendor, serie di patch, configurazione realmente risultante, immagine, initramfs e riga di comando effettiva. Verificare `CONFIG_HOTPLUG_CPU`, `CONFIG_MEMORY_HOTPLUG`, l'eventuale `CONFIG_ARCH_MEMORY_PROBE`, conservazione delle informazioni NUMA e presenza delle interfacce richieste. Un frammento Kconfig non prova la configurazione finale.
2. **Inventariare un avvio normale.** Salvare maschere CPU possible/present/online, APIC e appartenenze NUMA, SRAT/SLIT, mappa EFI/E820, `/proc/iomem`, intervalli e dimensione dei blocchi memoria, collegamenti `memoryXXX/nodeY`, zone e quantità per nodo. Separare RAM effettiva, buchi, MMIO, riserve firmware e memoria non volatile. Confermare il nodo della CPU di boot.
3. **Preparare recupero e osservabilità.** Disporre di console remota e voce di boot funzionante selezionabile senza il sistema operativo. Preparare log persistenti/console e, per il requisito «mai», osservazione delle allocazioni iniziali: una fotografia di `/proc/zoneinfo` a fine boot non ne prova la storia. Non calcolare indirizzi RAM dai soli numeri delle CPU.
4. **Provare prima il rinvio CPU.** Con RAM invariata, verificare un avvio con la sola CPU di boot, poi attivare soltanto le altre CPU di housekeeping. Conservare un confronto esatto fra insieme atteso e osservato. Non procedere se cambia la mappa NUMA o manca una CPU necessaria.
5. **Dimostrare separatamente il percorso RAM.** Su ambiente di prova, definire gli intervalli fisici esclusi e il meccanismo autorizzato di aggiunta; verificare allineamento, conflitti di risorse, associazione al nodo, numero di blocchi e metadati. Il documento non fornisce una mappa inventata né una sequenza `probe` da eseguire alla cieca. Se il percorso non è dimostrato, fermare questa variante del progetto.
6. **Validare la prima fase.** Prima delle applicazioni, confrontare CPU online con l'insieme consentito e accertare che nessuna RAM esterna a node0 sia gestita dagli allocatori. Distinguere il nodo presente/online dalla presenza di pagine utilizzabili. Confrontare log iniziali e mappa finale; verificare anche le prenotazioni anticipate di hugepage.
7. **Validare la transizione.** Bloccare le applicazioni dietro una barriera esplicita. Aggiungere memoria, verificarne nodo e stato, scegliere la zona prevista; attivare CPU e controllare topologia e affinità. Registrare ogni operazione e rifiutare una transizione parziale. Un mero ritardo temporale non costituisce una barriera.
8. **Validare la seconda fase sotto carico.** Solo dopo tutti i controlli, avviare le applicazioni e misurare distribuzione delle pagine, allocazioni kernel, interrupt, thread, jitter e pressione sul nodo 0, anche con rete e I/O attivi. Provare avvii ripetuti, errori e ripristino del boot normale. Il successo su una macchina virtuale verifica parti del coordinamento, non la mappa fisica e il firmware del server.

Il risultato da ottenere è un verbale con prove separate per disponibilità iniziale, correttezza della riattivazione e isolamento operativo. Finché manca la prova RAM sul kernel effettivamente distribuito e sulla piattaforma, l'intero avvio rigoroso in due fasi resta **non validato**.
