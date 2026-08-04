#include "chime.hpp"

#include "brand.hpp"

#include <bb/device/Led>
#include <bb/device/LedColor>
#include <bb/multimedia/MediaPlayer>

#include <audio/audio_manager_routing.h>

#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QUrl>

#include <errno.h>
#include <math.h>

using namespace bb::device;
using namespace bb::multimedia;

namespace {

// High enough to keep the square wave's harmonics from folding back and
// detuning the pitch.
const int SAMPLE_RATE = 44100;
// Square waves are loud for their peak level; leave plenty of headroom.
const double AMPLITUDE = 0.40;
// 0.2ms attack/release: just enough to stop the DC step from popping,
// far too short to soften the hard on/off snap of a PC speaker.
const int FADE_SAMPLES = 8;

// Pitches from flipper-claude-buddy/flipper-app/notifications.c
// (seq_success / seq_error / seq_perm) and tools/chime-lab.html.
const double STATUS_HZ = 600.00; // deep POST beep
const double ERROR_HZ  = 350.00; // deeper still, like a failure code
// Flipper seq_success: ascending major triad C5-E5-G5 (do-mi-so).
const double C5 = 523.25;
const double E5 = 659.25;
const double G5 = 783.99;

struct Note {
    double hz; // 0 = rest
    int ms;
};

const Note SEQ_STATUS[] = { { STATUS_HZ, 180 } };
// Same 100ms steps as Flipper's message_delay_100 between notes.
const Note SEQ_DONE[]   = { { C5, 100 }, { E5, 100 }, { G5, 120 } };
// Two short beeps, the way a POST failure code sounds.
const Note SEQ_ERROR[]  = { { ERROR_HZ, 110 }, { 0, 80 }, { ERROR_HZ, 110 } };
// Flipper seq_perm (SoundPerm): rising C5-E5 — "come look at this".
// 50ms rest between notes so it reads as a double beep, not a slur, and
// cannot be confused with the same-pitch error bi-bi.
const Note SEQ_ATTENTION[] = { { C5, 100 }, { 0, 50 }, { E5, 100 } };

void appendLe16(QByteArray &out, unsigned int v)
{
    out.append(char(v & 0xff));
    out.append(char((v >> 8) & 0xff));
}

void appendLe32(QByteArray &out, unsigned int v)
{
    appendLe16(out, v & 0xffff);
    appendLe16(out, (v >> 16) & 0xffff);
}

// 16-bit mono PCM WAV of the note sequence, square-wave "MIDI beeper" tone.
//
// Monophonic like the Flipper's piezo: one oscillator that gets retuned per
// note, so the phase carries across note boundaries and the fade only touches
// the ends of the whole cue. Retriggering per note instead would click between
// notes and make a fast triad read as a chord.
QByteArray renderWav(const Note *notes, int count)
{
    int total = 0;
    for (int n = 0; n < count; ++n)
        total += SAMPLE_RATE * notes[n].ms / 1000;

    QByteArray pcm;
    double phase = 0.0;
    int pos = 0;
    for (int n = 0; n < count; ++n) {
        const int samples = SAMPLE_RATE * notes[n].ms / 1000;
        for (int i = 0; i < samples; ++i, ++pos) {
            double value = 0.0;
            if (notes[n].hz > 0.0) {
                value = (phase < 0.5) ? AMPLITUDE : -AMPLITUDE;
                phase = fmod(phase + notes[n].hz / SAMPLE_RATE, 1.0);
            }
            if (pos < FADE_SAMPLES)
                value *= double(pos) / FADE_SAMPLES;
            else if (pos > total - FADE_SAMPLES)
                value *= double(total - pos) / FADE_SAMPLES;
            appendLe16(pcm, (unsigned int)(short)(value * 32767.0));
        }
    }

    QByteArray wav("RIFF");
    appendLe32(wav, 36 + pcm.size());
    wav += "WAVEfmt ";
    appendLe32(wav, 16);             // fmt chunk size
    appendLe16(wav, 1);              // PCM
    appendLe16(wav, 1);              // mono
    appendLe32(wav, SAMPLE_RATE);
    appendLe32(wav, SAMPLE_RATE * 2); // byte rate
    appendLe16(wav, 2);              // block align
    appendLe16(wav, 16);             // bits per sample
    wav += "data";
    appendLe32(wav, pcm.size());
    wav += pcm;
    return wav;
}

} // namespace

Chime::Chime(QObject *parent)
    : QObject(parent)
    , m_player(new MediaPlayer(this))
    , m_led(new Led(this))
    , m_lastStatusMs(0)
    , m_sound(true)
    , m_led_on(true)
{
    // Ride the media volume slider, not the notification one.
    unsigned int handle = 0;
    if (audio_manager_get_handle(AUDIO_TYPE_MULTIMEDIA, 0, false, &handle) == EOK)
        m_player->setAudioManagerHandle(handle);
}

QString Chime::wavPath(Cue cue)
{
    if (m_wavs.contains(cue))
        return m_wavs.value(cue);

    const Note *notes = SEQ_STATUS;
    int count = sizeof(SEQ_STATUS) / sizeof(Note);
    // Versioned filenames so a MediaPlayer / sandbox cache cannot keep
    // serving an older waveform after a pitch fix.
    QString name = "status-v2.wav";
    if (cue == CueDone) {
        notes = SEQ_DONE;
        count = sizeof(SEQ_DONE) / sizeof(Note);
        name = "done-c5e5g5.wav";
    } else if (cue == CueError) {
        notes = SEQ_ERROR;
        count = sizeof(SEQ_ERROR) / sizeof(Note);
        name = "error-v2.wav";
    } else if (cue == CueAttention) {
        notes = SEQ_ATTENTION;
        count = sizeof(SEQ_ATTENTION) / sizeof(Note);
        name = "attention-c5e5.wav";
    }

    QDir dir(QDir::homePath() + "/chimes");
    if (!dir.exists() && !dir.mkpath("."))
        return QString();
    const QString path = dir.filePath(name);
    // Always rewritten once per cue per launch so the baked waveform matches
    // this build even if the path is reused.
    QFile file(path);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate))
        return QString();
    file.write(renderWav(notes, count));
    file.close();
    m_wavs.insert(cue, path);
    return path;
}

void Chime::play(Cue cue)
{
    if (cue == CueStatus) {
        const qint64 now = QDateTime::currentMSecsSinceEpoch();
        if (now - m_lastStatusMs < 1200)
            return;
        m_lastStatusMs = now;
    }

    const QString path = m_sound ? wavPath(cue) : QString();
    if (!path.isEmpty()) {
        // stop() first: replaying the same source (back-to-back status
        // blips) has to rewind, and a new source can't be set while playing.
        m_player->stop();
        m_player->setSourceUrl(QUrl::fromLocalFile(path));
        m_player->play();
    }

    if (m_led_on) {
        LedColor::Type color = BRAND_LED_STATUS;
        if (cue == CueDone)
            color = LedColor::Green;
        else if (cue == CueError)
            color = LedColor::Red;
        else if (cue == CueAttention)
            // Flipper seq_perm: magenta blink for "needs user action".
            color = LedColor::Magenta;
        m_led->setColor(color);
        m_led->flash(1);
    }
}
