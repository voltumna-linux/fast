# Avvio minimale di fast-01

`fast-rtchain` installa e abilita `rtchain.service`. Al boot esegue un solo
script, `boot-fast01`, con valori incorporati e pause fisse:

1. Mount hugetlbfs, hugepage sul nodo 1, registro CRUDA e binding VFIO.
2. `tsc_init`, sei `dpdk_master` e un `dpdk_ebpm` multi-master.
3. Dodici `naff-ccd`, gruppi 1,9,…,89, separati nel tempo.
4. `catds-srv fast01`, `devx-srv dpdkmaster6` e `devx-srv ebpm`.
5. `tcbuf-srv ebpm`, `naff` e `nafftune`, poi SmartRescan sui tre device.

Il database è `192.168.190.66:20001`. Il fascio è reale: TX sintetico e
simulazione ebpm sono disabilitati; catds è avviato ma non collegato a ebpm.
Starter è mascherato; i server Tango sono avviati direttamente.

## Prerequisiti

Installare questa immagine solo su fast-01, con boot pulito, rete e mount
`/runtime` disponibili. Il firmware/kernel deve consentire VFIO sulle sei
funzioni PCI indicate nello script, ciascuna con il proprio gruppo IOMMU,
e riservare hugepage da 1 GiB (256 sul nodo 1). Le CPU fisiche 0–255 e i due
nodi NUMA devono essere disponibili. Gli avvii usano scheduling normale;
non viene applicato il tuning realtime.

Binari, librerie e dati propri degli applicativi devono essere già installati
in `/runtime/bin`, `/runtime/lib` e nei percorsi richiesti dalle proprietà
Tango. Servono `tsc_init`, `cruda_init`, `dpdk_master`, `dpdk_ebpm`, `naff-ccd`,
`crudaknob`, `catds-srv`, `devx-srv`, `tcbuf-srv` e `libdpdk_ebpm.so` compatibili
con il bring-up storico, incluso il supporto ebpm multi-master.
Il database Tango deve già contenere le istanze elencate e i device
`tcbuf/test/{ebpm,naff,nafftune}`; non viene modificata alcuna registrazione.

Cataloghi e schemi CRUDA indispensabili sono installati in
`/usr/share/fast/rtchain/data`. Derivano da `bring-updown/fast-01/etc`;
i sei schemi master usano nomi `fast0`…`fast5` e app_id 40…45.
La mappa dei worker deriva da `bringup_splitsix.sh`, con RX/TX su quattro
core fisici per porta; le variabili naff dallo snapshot
`gold/fast-01-gold-001/PARAMETRI.txt`. Il catalogo mantiene la rimappatura
storica di 14 elettroniche per porta (84 totali).

## Uso

La ricetta è già inclusa nell'immagine. Il servizio parte automaticamente;
su un boot pulito, se non ancora avviato, il comando equivalente è:

```sh
systemctl start rtchain.service
```

I log dei processi sono in `/run/rtchain-log`; l'output dello script va nel
journal della unit. Non ci sono test, probe, controlli di readiness, retry
o riavvii automatici. Le pause non certificano che i processi siano pronti:
il completamento della unit indica soltanto che lo script è terminato.

`systemctl stop rtchain.service` termina i processi del suo cgroup. Per un
nuovo avvio riavviare la macchina: lo script non recupera risorse condivise
residue. Usare una nuova immagine senza overlay o unit locali del precedente
bring-up in `/etc/systemd/system`, che avrebbero precedenza sui file vendor.

La build Yocto e il boot sull'hardware vanno eseguiti nell'ambiente FAST;
questa modifica non include il loro collaudo.
