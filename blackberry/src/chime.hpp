#ifndef CHIME_HPP
#define CHIME_HPP

#include <QMap>
#include <QObject>
#include <QString>

namespace bb { namespace device { class Led; } }
namespace bb { namespace multimedia { class MediaPlayer; } }

/*!
 * Audible + LED feedback for job progress.
 *
 * Four cues in the voice of a PC-speaker beeper, pitches taken from
 * flipper-claude-buddy/flipper-app/notifications.c:
 *   - status:    single POST beep on every new phase/tool
 *   - done:      ascending do-mi-so (C5-E5-G5) when a turn completes
 *   - error:     low double beep (G4-G4) on failure
 *   - attention: rising C5-E5 when the agent needs permission / a question
 *                answer / plan approval (Flipper's SoundPerm)
 * Each is paired with an LED flash (status / green / red / magenta).
 * Shared by Claude, Grok and unified builds.
 *
 * The tones are square waves synthesised into small WAV files inside the
 * app sandbox on first use (no binary assets to ship) and played through
 * bb::multimedia::MediaPlayer on an AUDIO_TYPE_MULTIMEDIA handle, so they
 * follow the MEDIA volume slider rather than the notification volume.
 */
class Chime : public QObject
{
    Q_OBJECT

public:
    enum Cue {
        CueStatus,    // new phase / tool: short blip + brand status LED
        CueDone,      // turn finished:    do-mi-so (C5-E5-G5) + green
        CueError,     // job failed:       G4-G4 double beep + red
        CueAttention  // needs user:       C5-E5 rising pair + magenta
    };

    explicit Chime(QObject *parent = 0);

    // Both cue channels are independently mutable from the Session sheet.
    void setSoundEnabled(bool on) { m_sound = on; }
    void setLedEnabled(bool on) { m_led_on = on; }

    void play(Cue cue);

private:
    // Synthesises the cue's WAV on first request; "" if it can't be written.
    QString wavPath(Cue cue);

    bb::multimedia::MediaPlayer *m_player;
    bb::device::Led *m_led;
    QMap<int, QString> m_wavs;
    // Phases can change twice in the same second (thinking -> tool call);
    // that should read as one beep, not a stutter.
    qint64 m_lastStatusMs;
    bool m_sound;
    bool m_led_on;
};

#endif // CHIME_HPP
