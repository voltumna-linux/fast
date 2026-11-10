SUMMARY = "Static FAST preparation and complete realtime readiness"
LICENSE = "CC-BY-4.0"
LIC_FILES_CHKSUM = "file://COPYING.CC-BY-4;md5=29f6d6758af09d75babd5c4960f7e64d"

FILESEXTRAPATHS:prepend := "${THISDIR}/../../tools:${THISDIR}/../..:"
SRC_URI = "file://rtchain file://COPYING.CC-BY-4"
S = "${UNPACKDIR}"

inherit features_check
REQUIRED_DISTRO_FEATURES = "systemd"

RDEPENDS:${PN} = "python3-core python3-json python3-io \
    systemd util-linux-mount util-linux-findmnt numactl ethtool kmod pciutils \
    nfs-utils-client"

do_install() {
    install -d ${D}${libexecdir}/rtchain ${D}${systemd_unitdir}/system \
        ${D}${systemd_unitdir}/system-generators ${D}${sysconfdir}/fast/hosts \
        ${D}${sysconfdir}/systemd/journald.conf.d ${D}${datadir}/fast/rtchain/examples
    for tool in rtchain-ops rtchain-prepare rtchain-producer rtchain-ready rtchain-systemd-generator; do
        install -m 0755 ${S}/rtchain/$tool ${D}${libexecdir}/rtchain/
    done
    install -m 0644 ${S}/rtchain/rtchain_*.py ${D}${libexecdir}/rtchain/
    install -m 0644 ${S}/rtchain/systemd/*.service ${S}/rtchain/systemd/*.slice \
        ${S}/rtchain/systemd/*.target ${D}${systemd_unitdir}/system/
    sed -i -e 's|/usr/libexec|${libexecdir}|g' \
        ${D}${systemd_unitdir}/system/rtchain-*.service
    printf '#!/bin/sh\nexec ${libexecdir}/rtchain/rtchain-systemd-generator "$@"\n' \
        > ${D}${systemd_unitdir}/system-generators/fast-rtchain
    chmod 0755 ${D}${systemd_unitdir}/system-generators/fast-rtchain
    install -m 0644 ${S}/rtchain/systemd/60-fast-rtchain.conf \
        ${D}${sysconfdir}/systemd/journald.conf.d/
    install -m 0644 ${S}/rtchain/examples/*.json ${D}${datadir}/fast/rtchain/examples/
    install -m 0644 ${S}/rtchain/INSTALL.md ${D}${datadir}/fast/rtchain/
    install -m 0644 ${S}/rtchain/READINESS.md ${D}${datadir}/fast/rtchain/
    install -m 0644 ${S}/rtchain/PRODUCERS.md ${D}${datadir}/fast/rtchain/
    install -m 0644 ${S}/rtchain/README.md ${D}${datadir}/fast/rtchain/
}

FILES:${PN} += "${libexecdir}/rtchain ${systemd_unitdir} ${datadir}/fast/rtchain"
