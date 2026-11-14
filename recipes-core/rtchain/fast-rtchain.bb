SUMMARY = "Minimal direct boot of the fast-01 acquisition chain"
LICENSE = "CC-BY-4.0"
LIC_FILES_CHKSUM = "file://COPYING.CC-BY-4;md5=29f6d6758af09d75babd5c4960f7e64d"

FILESEXTRAPATHS:prepend := "${THISDIR}/../../tools:${THISDIR}/../..:"
SRC_URI = "file://rtchain file://COPYING.CC-BY-4"
S = "${UNPACKDIR}"

inherit features_check systemd
REQUIRED_DISTRO_FEATURES = "systemd"
SYSTEMD_SERVICE:${PN} = "rtchain.service"
SYSTEMD_AUTO_ENABLE = "enable"

RDEPENDS:${PN} = "bash systemd util-linux-mount util-linux-taskset numactl \
    kmod pciutils dpdk-tools python3-json python3-io python3-pytango"

do_install() {
    install -d ${D}${libexecdir}/rtchain ${D}${systemd_system_unitdir} \
        ${D}${sysconfdir}/systemd/system ${D}${datadir}/fast/rtchain/data
    install -m 0755 ${S}/rtchain/boot-fast01 ${D}${libexecdir}/rtchain/
    sed 's|/usr/libexec|${libexecdir}|g' ${S}/rtchain/rtchain.service \
        > ${D}${systemd_system_unitdir}/rtchain.service
    sed -i 's|/usr/share/fast/rtchain|${datadir}/fast/rtchain|g' \
        ${D}${libexecdir}/rtchain/boot-fast01
    install -m 0644 ${S}/rtchain/data/* ${D}${datadir}/fast/rtchain/data/
    install -m 0644 ${S}/rtchain/README.md ${D}${datadir}/fast/rtchain/
    # /etc masks Starter without owning its vendor unit.
    ln -s /dev/null ${D}${sysconfdir}/systemd/system/starter.service
}

FILES:${PN} += "${libexecdir}/rtchain ${datadir}/fast/rtchain"
