# Avvio di Starter dopo la readiness realtime

`fast-rtchain` installa `starter.service.d/70-realtime-ready.conf`:
`Requires=rtchain-ready.service` e `After=rtchain-ready.service`.
All'avvio automatico o a `systemctl start starter.service`, systemd richiede
la catena e attende il completamento riuscito del suo oneshot finale.
`After` da solo consentirebbe l'esecuzione anche dopo un controllo fallito.
La preparazione resta un requisito diretto di Starter e della catena.
Il percorso è preparazione → produttori → tuning finale → readiness →
wrapper dei cgroup → Starter nativo → server Tango per livelli esistenti.
Database e tangorc devono essere disponibili attraverso le dipendenze proprie
di Starter; la readiness non richiede alcun server che Starter deve lanciare.

Il gate non cambia registrazioni, livelli, proprietà di avvio automatico o
comandi di Starter. Se `StartServersAtStartup` è disattivato, Starter parte
dopo la readiness ma attende i comandi dell'operatore. Il profilo per server
resta applicato prima dell'exec dal launcher nativo; vedi [TANGO.md](TANGO.md).
Questo consenso certifica la disponibilità realtime, non la configurazione
completa del banco. Le operazioni successive a Tango restano nel ticket 08.

## Dismissione del servizio monolitico

La ricetta installa `/dev/null` come `rtchain.service` nella directory vendor
di systemd. Le immagini nuove non installano né abilitano il servizio storico
con `After=starter.service`. Starter e la nuova readiness dichiarano inoltre
`Conflicts=rtchain.service`: i due proprietari non possono essere attivi insieme.
Non aggiungere `After=starter.service` alle unit della nuova catena.

Su un sistema aggiornato, una unit in `/etc/systemd/system` prevale sulla
maschera vendor. **Prima del boot di migrazione**, archiviare e rimuovere
la vecchia unit `rtchain.service`, i suoi drop-in e i collegamenti di enable
da `/etc/systemd/system`; eliminare anche eventuali copie in `/run`.
Non installare nuovamente l'overlay storico `bring-updown/fast-0*/etc` sopra
la nuova immagine. I file storici nel workspace sono riferimenti e non sono
il percorso di installazione della nuova catena.

Eseguire la migrazione su macchina ferma con boot pulito: `Conflicts` non
implementa lo stop ordinato e non rende sicuro un passaggio con produttori o
server già vivi. Prima dell'avvio verificare:

```sh
systemctl daemon-reload
systemctl is-enabled rtchain.service   # deve riportare masked
systemctl show rtchain.service -p LoadState -p FragmentPath
systemctl cat starter.service rtchain-ready.service
systemctl show starter.service -p Requires -p After -p Conflicts
systemctl start starter.service
```

Rimuovere eventuali drop-in locali di Starter che azzerano `Requires`/`After`
o ripristinano il vecchio ExecStart. Non attivare in parallelo script di
bring-up, launcher Tango o il gate storico che dipende da devx/tcbuf/catds.

## Esiti operativi

```sh
systemctl show rtchain-prepare.service rtchain-ready.service starter.service \
    -p ActiveState -p SubState -p Result -p ExecMainStatus -p MainPID
journalctl -b -u rtchain-prepare.service -u rtchain-ready.service -u starter.service
```

Un errore della preparazione lascia quella unit `failed` con la propria causa;
la readiness e Starter non eseguono il comando. Un errore del controllo finale
lascia `rtchain-ready.service` fallita e Starter non eseguito. Il job di Starter
fallisce per dipendenza in entrambi i casi: non attendersi necessariamente
`ActiveState=failed` o `Result=dependency` su un servizio mai eseguito.
Usare l'esito del comando `systemctl start`, il journal dei job e gli esiti
delle unit a monte per distinguere le cause. Un errore del wrapper cgroup,
successivo alla readiness, è invece un errore proprio di Starter.

`RemainAfterExit=yes` conserva la readiness riuscita nel boot. Il gate è una
barriera iniziale: non ripete la prova per ogni comando Tango e non è un
monitor dei produttori. Stop, sopravvissuti e riavvii durante il servizio sono
esclusi; correggere i fallimenti con un nuovo boot pulito.

## Test systemd → Starter → server Tango

La suite locale include un controllo del grafo statico e generato dell'host:
nessun ciclo e nessuna dipendenza della readiness da Starter/devx/tcbuf/catds.

```sh
python3 -m unittest discover -s tools/rtchain/tests -v
```

Il test operativo `tests/systemd_integration.py` richiede **una VM usa e getta
con systemd PID 1**, cgroup v2 e cpuset delegabile, syscall NUMA consentite,
utente `controls`, Starter con profili nativi, NativeProfileProbe e PyTango.
Usare esclusivamente un database Tango di prova. Il runner crea unit runtime
e registrazioni con nomi unici e le elimina al termine; mantiene i risultati
in `/var/tmp/fastgate-*`. Non eseguirlo sugli host FAST in servizio.

Nell'immagine di test abilitare:

```bitbake
PACKAGECONFIG:append:pn-starter = " profile-tests"
IMAGE_INSTALL:append = " fast-rtchain-tests"
```

Fornire una fixture JSON `profiles` con due profili CPU distinti realmente
disponibili nella VM; per esempio, su una VM con almeno quattro CPU e nodo 0:

```json
{"profiles": [
  {"cpus": "1", "numa_node": 0, "scheduler": "other", "priority": 0},
  {"cpus": "2", "numa_node": 0, "scheduler": "rr", "priority": 1}
]}
```

Il test crea i cgroup dei server dentro la delega della propria unit Starter;
non usa né modifica i cgroup dei server FAST. L'eventuale campo `cgroup` della
fixture viene sostituito con il percorso isolato. I nodi consentiti rimangono
quelli della VM; ogni profilo richiede il nodo privato specificato.

```sh
sudo python3 /usr/libexec/rtchain-tests/systemd_integration.py \
  --tango-host DB_DI_TEST:PORTA --user controls \
  --starter /usr/bin/Starter \
  --probe /usr/libexec/starter-tests/NativeProfileProbe \
  --fixtures /etc/fast/gate-fixtures.json \
  --gate /usr/lib/systemd/system/starter.service.d/70-realtime-ready.conf
```

Adattare la directory vendor di systemd al layout dell'immagine. Eseguibile e
librerie di Starter/probe devono essere accessibili al servizio senza dipendere
dall'ambiente della shell chiamante.

Il runner usa il drop-in distribuito, traducendo soltanto i nomi delle unit
per isolare il test. Preparazione e readiness sono oneshot controllabili:
copre preparazione con exit 31, readiness con exit 32, e readiness trattenuta
per due secondi prima del successo. Durante l'attesa e dopo i fallimenti
richiede MainPID e timestamp di esecuzione di Starter a zero, nessun ingresso
nel suo comando e nessun report dei server. Dopo il successo esegue Starter
reale con due istanze di NativeProfileProbe registrate ai livelli 1 e 2,
avvio automatico e profili diversi. Verifica ordine dei report, dispositivi
Tango raggiungibili, CPU/cgroup/NUMA/scheduling all'ingresso e confinamento
dei thread e figli. Archivia job, journal e stati delle unit per scenario.

La predisposizione dei cgroup nel runner è una fixture di test; il wrapper
produttivo e la memoria condivisa si qualificano separatamente con la suite
di [TANGO.md](TANGO.md). La readiness hardware si qualifica con
[READINESS.md](READINESS.md). Nessuna fixture sostituisce quelle prove.

In questo ambiente PID 1 non è systemd: il test operativo non è stato eseguito.
Restano da eseguire la build BitBake, la transazione reale in VM e il collaudo
hardware, senza attribuire ai controlli locali una certificazione del boot.
