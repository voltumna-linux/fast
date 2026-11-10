# Keep the upstream fetch revision reachable; carry the identified local change
# as a patch rather than asking BitBake to fetch an unpublished local commit.
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRCREV = "11145e1d0717971b4d37c74055b305679f2c853c"
S = "${UNPACKDIR}/git"
SRC_URI:remove = "file://02-starter-stdout.patch"
SRC_URI:append = " file://0001-native-launch-profiles.patch"
PR:append = ".fastnative1"

# Native launch uses libc Linux syscalls, without libsystemd or libnuma.
PACKAGECONFIG ??= ""
PACKAGECONFIG[profile-tests] = "-DBUILD_TESTING=ON,-DBUILD_TESTING=OFF"
PACKAGES =+ "${PN}-profile-tests"
FILES:${PN}-profile-tests = "${libexecdir}/starter-tests"
RDEPENDS:${PN}-profile-tests = "${PN} python3-core python3-json python3-pytango"

do_install:append() {
    install -d ${D}${datadir}/doc/${BPN}
    install -m 0644 ${S}/docs/native-launch-profile.md ${D}${datadir}/doc/${BPN}/
    if ${@bb.utils.contains('PACKAGECONFIG', 'profile-tests', 'true', 'false', d)}; then
        install -d ${D}${libexecdir}/starter-tests
        install -m 0755 ${B}/NativeProfileProbe ${D}${libexecdir}/starter-tests/
        install -m 0755 ${S}/tests/profile_integration.py ${D}${libexecdir}/starter-tests/
    fi
}
