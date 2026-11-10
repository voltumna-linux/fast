# Starter nativo nell'immagine FAST

La ricetta FAST seleziona Starter 9.1, fetch upstream
`11145e1d0717971b4d37c74055b305679f2c853c`, con
`0001-native-launch-profiles.patch`: incorpora il commit locale Starter
`034326ca9431210c672810185d31181c1c214b35` e l'estensione del probe per la
memoria condivisa. La revisione pacchetto aggiunge `.fastnative1`.
La patch è distribuita nel layer: non richiede di pubblicare il commit su
GitLab. La dipendenza di compilazione è cppTango; il percorso nativo usa le
syscall Linux di libc e non richiede libsystemd o libnuma. Il backend systemd
opzionale per i device server non è usato. La vecchia patch stdout, relativa
al launcher sostituito, è rimossa: stdout del figlio va a `/dev/null`, stderr
e gli errori del launcher restano nei log Starter/DevReadLog.

`fast-rtchain` installa il drop-in di `starter.service` e `tango-profile`.
L'utente/gruppo rimane `controls`, come configurato da meta-elettra.
Starter richiede anche il successo della readiness realtime ed è ordinato
dopo di essa. Il contratto di avvio, la dismissione del servizio monolitico
e il test systemd → Starter → server di prova sono in [STARTUP.md](STARTUP.md).

## Gerarchia e permessi

Richiesti kernel con cgroup v2 unificato, CONFIG_CPUSETS e CONFIG_NUMA,
controller cpuset disponibile e systemd con supporto `Delegate=cpuset` e
`AllowedCPUs`/`AllowedMemoryNodes`. Non montare una gerarchia v1 parallela.

```text
/sys/fs/cgroup/tango.slice/                 # systemd: risorse host
  starter.service/                         # systemd delega cpuset a controls
    supervisor/                            # wrapper, poi exec Starter
    catds-srv-fast01/                       # solo figli con questo profilo
    devx-srv-dpdkmaster/
    ...
```

Il wrapper viene eseguito come `controls` nella radice delegata. Sposta solo
sé stesso in `supervisor` prima di abilitare cpuset nei figli, rispettando
la regola cgroup v2 che vieta processi nei nodi interni con controller di
dominio. Prepara i gruppi vuoti e verifica CPU/nodi effettivi prima dell'exec
di Starter. Rifiuta gruppi popolati: richiede un boot pulito, senza migrazioni
di server già vivi. Stop e riavvio con sopravvissuti restano fuori perimetro.

`Delegate` dà a `controls` accesso alla directory delegata, a `cgroup.procs`
e a `cgroup.subtree_control`; i gruppi creati dal wrapper appartengono allo
stesso utente. Sorgente, destinazione e antenato comune della migrazione
restano nello stesso ramo delegato. Non si scrive in cgroup systemd esterni,
non si cambia il cgroup di Starter durante il lancio di un server.
Solo il figlio entra nel proprio gruppo prima dell'exec reale. Non applicare
`ProtectControlGroups=yes`, namespace cgroup privati o altri filtri che
impediscano queste operazioni o `set_mempolicy`.

Starter resta nel cpuset `starter_cpus`; il figlio può lasciarlo e ricevere
il cpuset del server. Entrambi devono essere sotto `resources.tango.cpus`.
Non restringere `AllowedCPUs` dell'intera `starter.service` alle sole CPU di
supervisione: escluderebbe le CPU dei server. Thread e figli possono ampliare
l'affinità soltanto entro il cpuset del proprio gruppo. Tutti questi processi
condividono l'identità fidata `controls`: la delega non costituisce un confine
di sicurezza contro programmi che modificano intenzionalmente i cgroup.

`LimitRTPRIO=99` permette al launcher nativo di applicare FIFO/RR tramite
rlimit, senza assegnare CAP_SYS_NICE ai binari. `LimitMEMLOCK=infinity`
conserva il supporto agli applicativi realtime. Seccomp/LSM e i permessi dei
file condivisi devono consentire le operazioni richieste.

Ogni gruppo server consente l'unione di `shared_memory_nodes` e `numa_node`.
Il primo deve includere il nodo del registro e tutti gli altri nodi delle
regioni condivise. Per consumatori con allocazioni private sul nodo 0 e
regioni condivise sul nodo 1, configurare `shared_memory_nodes: "0-1"` e
`numa_node: 0`. La proprietà applica MPOL_BIND alle nuove allocazioni private;
non modifica i nodi del cgroup e non migra pagine condivise. Il wrapper scrive
`cpuset.mems` soltanto su gruppi server vuoti. L'accesso hugetlbfs dipende
anche da mount, UID/GID e modalità predisposti nella preparazione.

## Inventario e proprietà del database

Completare `tango_servers` nel JSON locale dell'host descritto in INSTALL.md:

```json
{
  "tango_host": "database-qualificato.example:20001",
  "starter_cpus": "4-5",
  "shared_memory_nodes": "0-1",
  "profiles": [
    {"server": "catds-srv/fast01", "group": "catds-srv-fast01",
     "cpus": "8-15", "numa_node": 0, "scheduler": "other", "priority": 0}
  ]
}
```

CPU e indirizzo sopra sono illustrativi. I modelli distinti fast-01/fast-02
sono inattivi; contengono catds-srv/fast01 oppure fast02, devx-srv/dpdkmaster,
devx-srv/ebpm, tcbuf-srv/ebpm e tcbuf-srv/naff come inventario iniziale da
qualificare. Aggiungere ogni istanza effettivamente gestita (compresi eventuali
BPM per settore, naff veloci e monitor), eliminando quelle non previste.
Assegnare un nome di gruppo distinto a ogni istanza; risolvere i CCD nelle
CPU reali. Non copiare automaticamente la mappa storica tra i due host.

`tango_host` è obbligatorio, senza fallback al database dell'altra macchina
o alla selezione per rete di tangorc. Il wrapper lo esporta a Starter e ai
figli. Un `TANGO_CLUSTER` diverso dall'host commissionato viene rifiutato.
Il file locale deve essere amministrato da root. Dopo la qualificazione:

```sh
/usr/libexec/rtchain/tango-profile configure-db
/usr/libexec/rtchain/tango-profile configure-db --apply
systemctl daemon-reload
```

Il primo comando stampa il piano, senza collegarsi al database. Il secondo
usa esplicitamente `tango_host`, verifica per l'intero inventario che server
e device amministrativi esistano e che l'host registrato coincida con quello
locale, poi scrive e rilegge `NativeLaunchProfile` su ciascun device
`dserver/<eseguibile>/<istanza>`. Non cambia host, livelli o registrazioni.
È ripetibile; le scritture Tango non sono transazionali: un errore intermedio
richiede correzione e riesecuzione, senza rollback automatico.

Il JSON è l'inventario per predisporre risorse e configurare le proprietà.
A ogni lancio Starter legge il profilo dalla proprietà del server: non legge
questo JSON per sostituirla. Ogni variazione amministrativa deve restare
compatibile con i gruppi predisposti. Una proprietà assente conserva il
comportamento nativo (il server eredita `supervisor`); verificarne quindi la
presenza su **tutte** le istanze FAST prima di abilitarne i livelli automatici.
Una proprietà presente ma invalida o inapplicabile impedisce l'exec.

## Build e collaudo nell'immagine

```sh
bitbake starter fast-rtchain
```

Archiviare `bitbake -e starter` (SRCREV, SRC_URI, PR, DEPENDS e PACKAGECONFIG),
log do_patch/do_compile e manifest dell'immagine. Controllare che cppTango
10.3 selezionata dal distro compili Starter. Non è stata eseguita una build
BitBake in questo workspace; la sola build host con cppTango 10.0.2 non
certifica questo passaggio.

Per includere gli strumenti di collaudo in un'immagine di test:

```bitbake
PACKAGECONFIG:append:pn-starter = " profile-tests"
IMAGE_INSTALL:append = " starter-profile-tests"
```

Installano `/usr/libexec/starter-tests/NativeProfileProbe` e
`profile_integration.py`; non sono parte dell'immagine operativa predefinita.
Usare un database Tango usa e getta. Sul boot di prova, sostituire
temporaneamente l'ExecStart di Starter con:

```ini
[Service]
ExecStart=
ExecStart=/usr/libexec/rtchain/tango-profile launch -- /usr/bin/python3 /usr/libexec/starter-tests/profile_integration.py --tango-host DB_DI_TEST:PORTA --starter /usr/bin/Starter --probe /usr/libexec/starter-tests/NativeProfileProbe --fixtures /etc/fast/profile-fixtures.json
```

La suite e Starter di prova ereditano `supervisor` dentro la **stessa**
delega dell'immagine. Le registrazioni temporanee della suite sono isolate
tramite TANGO_CLUSTER; i due profili della fixture devono usare due gruppi
già predisposti dal JSON, con CPU diverse. Il loro `numa_node` sia 0, mems
effettivi 0-1. Formato completo della fixture e criteri della suite sono in
`/usr/share/doc/starter/native-launch-profile.md`.

Per il caso permessi usare `permission_failure_profile` con un cgroup v2
dominio root-owned esterno al ramo delegato, cpuset CPU identico al profilo
e nodi 0-1, `cgroup.procs` non scrivibile da controls; scegliere
`permission_failure_error` coerente con l'errore osservato `open cgroup.procs`.
Non usare FIFO 99 come caso negativo: l'unit concede intenzionalmente RTPRIO.
La suite verifica mancato ingresso nel server reale, errore in DevReadLog,
cgroup assente, profilo invalido, singoli avvii, livelli seriali/paralleli,
avvio automatico, riavvio dopo crash e confinamento di thread e figli.

Per verificare anche la memoria condivisa, prima di Starter creare un file
tmpfs di prova come amministratore, mantenendone le pagine residenti:

```sh
numactl --membind=1 python3 - <<'PY'
import mmap
with open('/dev/shm/fast-profile-shared', 'w+b') as stream:
    stream.truncate(4096)
    page = mmap.mmap(stream.fileno(), 4096)
    page[:] = b'F' * 4096
    page.close()
PY
chmod 0444 /dev/shm/fast-profile-shared
```

Aggiungere alla fixture `"shared_memory_file": "/dev/shm/fast-profile-shared"`
e `"shared_memory_node": 1`. Il probe mappa la pagina condivisa all'ingresso,
ne legge il contenuto e interroga il nodo fisico con get_mempolicy. La suite
richiede nodo 1 in tutti gli avvii pur con MPOL_BIND privato sul nodo 0;
una migrazione sul nodo 0 fa fallire il test. Ripetere il controllo sul vero
registro hugetlbfs con un consumatore qualificato: il test tmpfs non certifica
il protocollo delle regioni DPDK né le prestazioni realtime.

Archiviare JSON del probe, log di Starter, journal e:

```sh
systemctl show starter.service -p User -p Group -p Delegate -p ControlGroup -p LimitRTPRIO
cat /sys/fs/cgroup/tango.slice/starter.service/cgroup.procs
cat /sys/fs/cgroup/tango.slice/starter.service/supervisor/cgroup.procs
```

La radice deve essere vuota; Starter resta in supervisor anche dopo i lanci.
Verificare i cpuset effettivi di ogni gruppo e i file condivisi accessibili
da controls. Rimuovere la fixture e il drop-in di test al termine del boot
usa e getta. Il successo delle verifiche locali Python/CMake non sostituisce
questo collaudo systemd → Starter → server di prova.
