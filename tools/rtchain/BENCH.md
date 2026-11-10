# Configurazione del banco dopo Tango

`rtchain-bench.service` completa la configurazione del banco dopo Starter.
Il generatore aggiunge `Wants=rtchain-bench.service` a Starter soltanto se
`bench.enabled` è `true` nella configurazione commissionata dell'host.
Il banco richiede Starter ed è ordinato dopo di esso; attende poi device,
attributi e campi effettivamente disponibili. Il solo processo Starter
attivo non prova che i livelli abbiano completato l'avvio.

La readiness realtime e Starter non richiedono il successo del banco.
Un errore lascia `rtchain-bench.service` fallita, con il motivo nel journal;
non cambia il risultato già ottenuto da `rtchain-ready.service` e non
riavvia Starter o i produttori. Nessuna dipendenza di ritorno verso la
readiness è introdotta. `active (exited)` e `bench: ready` certificano il
completamento di queste operazioni in questo avvio, non un monitoraggio
continuo del banco.

## Configurazione per host

I modelli fast-01 e fast-02 sono inattivi (`bench.enabled: false`).
Prima dell'abilitazione compilare nel JSON dell'host:

- `bench.timeout`: attesa dei prerequisiti, positiva e al massimo 180 secondi;
  `poll_interval`: intervallo positivo in secondi, al massimo 180.
- `bench.devices`: lista di oggetti `name` e `attributes`. I nomi sono device
  Tango relativi al database, senza URI, host o alias. Ogni device deve
  rispondere a ping; ogni attributo elencato deve essere leggibile.
- `bench.keys`: chiavi cruda dei campi sorgente necessari ai tcbuf. Lista
  obbligatoria e non vuota; scegliere i campi della flotta realmente
  configurata sull'host. Il tool deve restituire `chiave = valore`:
  un'uscita zero senza chiave risolta è un errore.
- `smart-rescan.tango_host`: deve coincidere con
  `tango_servers.tango_host`, il database esplicito usato per Starter.
  `smart-rescan.devices` elenca i tcbuf destinatari, tutti presenti anche
  nei prerequisiti. Prima delle scritture si verifica la disponibilità
  del comando SmartRescan; ogni comando viene poi eseguito una sola volta,
  senza retry automatico di comandi che potrebbero avere già avuto effetto.
- `bench.operations`: `['smart-rescan']` per il fascio reale, oppure
  `['synthetic-beam', 'smart-rescan']` per il banco sintetico
  (usare doppi apici nel JSON).

Per il sintetico dichiarare anche `synthetic-beam.device` (il catds corretto,
presente in `bench.devices`), `sim_base`, `action_j`, `noise_nm`, `qs`,
`mode` (0 o 1). `sim_base` va ricavata dalla configurazione di pubblicazione
catds, non dal nome device né dal nome dell'host. Si attende anche
`sim_base__Qx`. Dopo tutti i prerequisiti si configurano le manopole del
generatore e si abilita il modo per ultimo, verificando ogni scrittura.
Usare parametri qualificati per l'host, senza default sintetici storici.
Non viene selezionato automaticamente alcun DB o simulatore.

Gli stadi naff, il pavimento sideband e le maschere waterfall rimangono in
`naff-knobs`, nella fase live precedente alla readiness (ticket 04).
Anche l'eventuale flotta aggiuntiva deve essere avviata e qualificata come
produttore: questo servizio non avvia flotte né riscrive le impostazioni
necessarie ai produttori già certificati.

## Avvio, rilanci e diagnosi

Registrare e abilitare nei livelli di Starter catds e tutti i tcbuf/devx
necessari, con istanze e profili dell'host corretto. Queste operazioni
restano amministrazione Tango/Starter. Il banco non esegue launcher,
kill, Init, stop o restart diretti dei server Tango. Su una macchina
migrata con catds ancora collegato a un vecchio registro, richiedere il
rilancio tramite Starter e attendere la nuova pubblicazione prima di
rieseguire il banco. Lo stop/riavvio della catena resta fuori perimetro.

Dopo aver commissionato e abilitato il banco, ricaricare systemd prima
del successivo avvio di Starter. Per un banco già avviato:

```sh
systemctl daemon-reload
systemctl start rtchain-bench.service
systemctl status rtchain-ready.service starter.service rtchain-bench.service
journalctl -b -u rtchain-bench.service
```

Dopo aver corretto un errore usare `systemctl restart rtchain-bench.service`.
Il rilancio ripete scritture e SmartRescan, senza rollback delle operazioni
già riuscite. Un fallimento può quindi lasciare configurazione parziale,
ma non dichiara il banco pronto. I comandi cruda hanno timeout e le chiamate
Tango hanno timeout individuali; systemd impone anche un limite totale di
900 secondi. Il servizio gira come `controls` in `tango.slice`, con accesso
al registro e alle librerie configurate; verificare tali permessi sull'host.

## Verifiche

`tests/test_bench.py` esegue la CLI con endpoint Tango e registro controllati:
attesa prima delle scritture, percorso riuscito, fascio reale senza simulatore,
prerequisiti mancanti, DB incongruente, URI esterno, scrittura fallita,
SmartRescan fallito e separazione del fallimento dal gate realtime.
Il test del generatore verifica il collegamento e il fallimento locale del
solo banco per configurazione invalida. La suite verifica anche il grafo
statico senza cicli. Queste prove non sostituiscono un boot systemd reale,
PyTango contro il DB effettivo o il collaudo hardware fast-01/fast-02.
