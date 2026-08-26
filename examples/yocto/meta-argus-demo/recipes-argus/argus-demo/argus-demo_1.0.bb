SUMMARY = "Argus Demo pygame application"
DESCRIPTION = "Minimal pygame app used as the target under test for the \
Argus framework's Yocto example. Draws a Home/Settings screen, exposes an \
HTTP instrumentation endpoint on :8085 (status/state/health/screen/input), \
and logs its transitions to stdout for journalctl."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

# The editable source lives in examples/yocto/app/ (not a files/ copy here):
# THISDIR is .../meta-argus-demo/recipes-argus/argus-demo, so three levels up
# is examples/yocto/, then into app/. See the "Editable source vs. recipe
# files" section of examples/yocto/README.md.
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../app:"

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
