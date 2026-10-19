require recipes-kernel/linux/linux-production.inc

# Use kernel.bbclass installation: AMD's override hardcodes /lib/modules and
# omits the kernel image. The class respects nonarch_base_libdir for usrmerge.
do_install() {
    kernel_do_install
}

# Restore the class package layout and enforce usrmerge QA for modules too.
FILES:${KERNEL_PACKAGE_NAME}-modules:remove = "/boot /lib/modules/${KERNEL_VERSION}/* /lib/modules/${KERNEL_VERSION}/modules*"
INSANE_SKIP:${KERNEL_PACKAGE_NAME}-modules:remove = "usrmerge"

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI:append = " \
	file://vtd-amd.cfg \
        file://amd-extra.cfg \
	file://static_intel_drivers.cfg \
    "

# static_intel_drivers.cfg is NOT a copy-paste: AMD boards (e.g. H14DSH)
# have Intel NICs and must use the statically linked drivers.

SRC_URI:append = " \
    file://acpi-ras.cfg \
    file://amd-cpu.cfg \
    file://debug.cfg \
    file://amd-kvm.cfg \
    file://media.cfg \
    file://memory.cfg \
    file://netfilter.cfg \
    file://amd-platform-drivers.cfg \
    file://0001-sched-isolation-Add-strict-isolcpus-flags.patch \
    file://0002-percpu-Allocate-isolated-CPUs-percpu-pages-on-the-al.patch \
    file://0003-fork-Back-the-per-cpu-stack-caches-with-a-shared-poo.patch \
    file://0004-fork-Don-t-cache-kernel-stacks-on-isolated-CPUs.patch \
    file://0005-x86-mm-Flush-all-TLBs-when-freeing-kernel-page-table.patch \
    file://0006-x86-mm-pat-Flush-all-TLBs-for-scattered-CPA-ranges.patch \
    file://0007-x86-mm-Limit-INVLPGB-to-VA-in-invlpgb-flush-addr-nos.patch \
    file://0008-memcg-Leave-isolated-CPUs-stocks-alone-in-drain-all.patch \
    file://0009-sched-fair-Skip-unchanged-unlimited-bandwidth-update.patch \
    file://0010-rcu-Don-t-dirty-rcu-data-of-CPUs-that-stayed-quiesce.patch \
    file://0011-sched-cputime-Don-t-lock-isolated-runqueues-for-task.patch \
    file://0012-mm-swap-Don-t-batch-LRU-folios-on-isolated-CPUs-v2.patch \
    file://0013-mm-page-alloc-Don-t-keep-pcp-lists-on-isolated-CPUs-v2.patch \
    file://0014-sched-psi-Leave-isolated-CPUs-out-of-pressure-averag.patch \
    file://0015-mm-vmstat-Don-t-dirty-idle-per-cpu-NUMA-counters-whe.patch \
    file://0016-fork-Hand-out-pooled-kernel-stacks-of-the-requested-node.patch \
    file://0017-sched-isolation-Log-each-strict-isolcpus-flag-at-boot.patch \
    file://0018-x86-cpu-amd-Work-around-AMD-erratum-1718.patch \
    file://0019-sched-deadline-re-evaluate-the-tick-dependency-for-the-fair-server.patch \
    file://0020-mm-vmstat-Leave-isolated-CPUs-alone-in-vmstat_refresh.patch \
    file://0021-mm-Leave-isolated-CPUs-alone-in-the-page-table-sync-IPI.patch \
    file://0022-x86-mm-Keep-housekeeping-processes-off-broadcast-TLB-flushes.patch \
    file://0023-mm-swap-Skip-isolated-CPUs-in-lru_add_drain_all-with-isolcpus-lru.patch \
    file://0024-sched-topology-Allocate-root-domains-on-the-node-of-their-CPUs.patch \
    file://0025-fork-Pool-kernel-stacks-per-node.patch \
    file://0026-mm-mempolicy-Keep-the-boot-interleave-off-isolated-nodes.patch \
    file://0027-mm-slub-Leave-isolated-nodes-slabs-to-their-CPUs.patch \
    file://0028-printk-Keep-the-log-buffer-off-isolated-nodes.patch \
    file://0029-x86-mm-Give-no-process-a-global-ASID-with-isolcpus-noasid.patch \
    file://0030-x86-fpu-Show-the-global-ASID-of-a-process-in-proc-PID-arch_status.patch \
    file://0031-x86-mm-Flush-housekeeping-batched-unmaps-by-IPI-with-isolcpus-tlbbatch.patch \
    "
