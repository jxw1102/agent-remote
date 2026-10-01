# One codebase, three artifacts:
#   qmake VARIANT=unified app.pro  ->  AgentRemote  (Agent Remote)
# Branding constants live in src/brand.hpp behind the VARIANT_* define;
# per-artifact bar-descriptor.xml + icon live under variant/<name>/.
# The unified variant is the Android app's sibling: profiles for many
# daemons at once, one merged session list, per-provider accents.

isEmpty(VARIANT): VARIANT = claude

contains(VARIANT, grok) {
    TARGET = GrokRemote
    DEFINES += VARIANT_GROK
} else:contains(VARIANT, unified) {
    TARGET = AgentRemote
    DEFINES += VARIANT_UNIFIED
} else {
    TARGET = ClaudeRemote
    DEFINES += VARIANT_CLAUDE
}

CONFIG += qt warn_on debug_and_release cascades
# -lbacktrace: CrashGuard (QNX libbacktrace); -lm: stb_truetype (RichPaint);
# -lbbcascadespickers: FilePicker for the attachment "+" button;
# -lbbmultimedia -laudio_manager -lbbdevice: Chime (beeps on media volume,
# LED flashes)
LIBS += -lbbdata -lbbsystem -lbb -lbbcascades -lbbcascadespickers \
        -lbbmultimedia -laudio_manager -lbbdevice -lbacktrace -lm

SOURCES += $${PWD}/src/main.cpp \
           $${PWD}/src/applicationui.cpp \
           $${PWD}/src/apiclient.cpp \
           $${PWD}/src/statussocket.cpp \
           $${PWD}/src/statussse.cpp \
           $${PWD}/src/crashguard.cpp \
           $${PWD}/src/chime.cpp \
           $${PWD}/src/richpaint.cpp \
           $${PWD}/src/socksproxy.cpp \
           $${PWD}/src/trace.cpp

HEADERS += $${PWD}/src/applicationui.hpp \
           $${PWD}/src/apiclient.hpp \
           $${PWD}/src/statussocket.hpp \
           $${PWD}/src/statussse.hpp \
           $${PWD}/src/crashguard.hpp \
           $${PWD}/src/chime.hpp \
           $${PWD}/src/richpaint.hpp \
           $${PWD}/src/brand.hpp \
           $${PWD}/src/socksproxy.hpp \
           $${PWD}/src/trace.hpp

# A/B build switch: `touch blackberry/NO_SOCKS` builds with the V2Ray SOCKS
# proxy compiled OUT (everything else identical), to prove whether the
# Qt-level proxy is what leaves a grey, un-relaunchable icon after close.
exists($${PWD}/NO_SOCKS): DEFINES += AR_NO_SOCKS
SOCKS10808_ROOT = $${PWD}/../../third_party/socks10808
INCLUDEPATH += $${PWD}/src $${SOCKS10808_ROOT}
SOURCES += $${SOCKS10808_ROOT}/socks10808.c

lupdate_inclusion {
    SOURCES += $${PWD}/assets/*.qml
}

device {
    CONFIG(debug, debug|release) {
        DESTDIR = $${PWD}/arm/o.le-v7-g
    }
    CONFIG(release, debug|release) {
        DESTDIR = $${PWD}/arm/o.le-v7
    }
}

# QML documents compiled into the binary by rcc. See qml.qrc.
# Qt 4.8 embeds the text; assets/ remains the fallback.
RESOURCES += $${PWD}/qml.qrc
