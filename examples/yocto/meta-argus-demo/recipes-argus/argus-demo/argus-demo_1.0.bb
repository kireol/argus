SUMMARY = "Argus Demo pygame application"
DESCRIPTION = "Minimal pygame app used as the target under test for the \
Argus framework's Yocto example. Draws a Home/Settings screen, exposes an \
HTTP instrumentation endpoint on :8085 (status/state/health/screen/input), \
and logs its transitions to stdout for journalctl."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

# Files here are a checked-in mirror of ../../../app/ -- see the "Editable
# source vs. recipe files" section of examples/yocto/README.md for why, and
# `make sync` (or the one-line cp it documents) to refresh them after editing
# the app.
SRC_URI = " \
    file://argus_demo.py \
    file://argus-demo.service \
"

S = "${WORKDIR}"

inherit systemd

SYSTEMD_SERVICE:${PN} = "argus-demo.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

RDEPENDS:${PN} = "python3-pygame python3-core"

do_install() {
    install -d ${D}${bindir}
    install -m 0755 ${WORKDIR}/argus_demo.py ${D}${bindir}/argus-demo

    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/argus-demo.service ${D}${systemd_system_unitdir}/argus-demo.service
}

FILES:${PN} += "${systemd_system_unitdir}/argus-demo.service"
