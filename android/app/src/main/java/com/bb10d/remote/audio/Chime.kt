package com.bb10d.remote.audio

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import kotlin.math.PI
import kotlin.math.min
import kotlin.math.sin

/**
 * Progress cues shared with the BB10 and web clients.
 *
 * Pitches come from flipper-claude-buddy/flipper-app/notifications.c so every
 * device in the family says the same thing:
 *   - Status:    short blip on a new phase/tool
 *   - Done:      rising C5-E5-G5 when a turn finishes (seq_success)
 *   - Error:     low same-pitch double-tap on failure (seq_error)
 *   - Attention: rising C5-E5 when the agent needs permission, a question
 *                answer, or plan approval (seq_perm / SoundPerm)
 *
 * Tones are synthesised at runtime rather than shipped as assets — a few
 * hundred bytes of arithmetic beats an .ogg.
 *
 * Two departures from the BlackBerry original:
 *
 *  - **Square waves are softened.** BB10 emitted raw squares; through a phone
 *    speaker those click on every edge, so each tone gets a short fade and a
 *    little sine blended in. It still reads as a chiptune blip.
 *  - **The status LED becomes a haptic.** Phones no longer have notification
 *    LEDs (and the API is a no-op on nearly all of them), so the second
 *    channel is a vibration of matching length. It stays independently
 *    switchable, exactly like `ledCues` was.
 *
 * Playback uses the *media* stream on purpose — the same call the BB10 build
 * made with `audio_manager_get_handle(AUDIO_TYPE_MULTIMEDIA…)`. Notification
 * volume is silenced far too often for a cue whose whole job is telling you a
 * twenty-minute turn just ended.
 */
class Chime(context: Context) {

    private val app = context.applicationContext

    enum class Cue {
        /** New phase or tool — the quietest of the four. */
        Status,

        /** Turn finished cleanly. */
        Done,

        /** Turn failed or was stopped with an error. */
        Error,

        /** The agent is blocked on a permission or a question. */
        Attention,
    }

    private val pcm = HashMap<Cue, ShortArray>()

    fun play(cue: Cue, sound: Boolean, haptic: Boolean) {
        if (sound) runCatching { emit(cue) }
        if (haptic) runCatching { buzz(cue) }
    }

    // -- tones -------------------------------------------------------------

    private fun emit(cue: Cue) {
        val samples = pcm.getOrPut(cue) { render(cue) }
        if (samples.isEmpty()) return
        val track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build(),
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(RATE)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build(),
            )
            .setTransferMode(AudioTrack.MODE_STATIC)
            .setBufferSizeInBytes(samples.size * 2)
            .build()
        track.write(samples, 0, samples.size)
        // Free the track the moment the last sample has been rendered; a
        // leaked AudioTrack holds a hardware slot open.
        track.notificationMarkerPosition = samples.size
        track.setPlaybackPositionUpdateListener(
            object : AudioTrack.OnPlaybackPositionUpdateListener {
                override fun onMarkerReached(t: AudioTrack?) {
                    runCatching { t?.stop() }
                    runCatching { t?.release() }
                }

                override fun onPeriodicNotification(t: AudioTrack?) = Unit
            },
        )
        track.play()
    }

    private fun render(cue: Cue): ShortArray = when (cue) {
        // A single E5 blip, quiet: this one can fire many times in one turn.
        Cue.Status -> tone(E5, 70, 0.22f)
        // Flipper seq_success: C5-E5-G5.
        Cue.Done -> tone(C5, 100, 0.34f) + tone(E5, 100, 0.34f) + tone(G5, 120, 0.34f)
        // Flipper seq_error: two low G4 taps.
        Cue.Error -> tone(G4, 100, 0.36f) + silence(50) + tone(G4, 100, 0.36f)
        // Flipper seq_perm: rising C5-E5 — not the same-pitch error bi-bi.
        Cue.Attention -> tone(C5, 100, 0.32f) + silence(50) + tone(E5, 100, 0.32f)
    }

    /**
     * One note. Mostly square (that is the BB10 timbre) with a sine blended in
     * to take the hardest edge off, plus a 4 ms fade at each end so the
     * speaker does not pop.
     */
    private fun tone(freq: Double, ms: Int, amplitude: Float): ShortArray {
        val count = RATE * ms / 1000
        val out = ShortArray(count)
        val period = RATE / freq
        val fade = min(RATE * 4 / 1000, count / 2)
        for (i in 0 until count) {
            val phase = (i % period) / period
            val square = if (phase < 0.5) 1.0 else -1.0
            val smooth = sin(2 * PI * phase)
            var value = (square * 0.7 + smooth * 0.3) * amplitude
            if (i < fade) value *= i.toDouble() / fade
            val tail = count - i
            if (tail < fade) value *= tail.toDouble() / fade
            out[i] = (value * Short.MAX_VALUE).toInt().toShort()
        }
        return out
    }

    private fun silence(ms: Int) = ShortArray(RATE * ms / 1000)

    // -- haptics (the old status LED) --------------------------------------

    private fun buzz(cue: Cue) {
        val vibrator = vibrator() ?: return
        if (!vibrator.hasVibrator()) return
        val effect = when (cue) {
            Cue.Status -> VibrationEffect.createOneShot(18, 60)
            Cue.Done -> VibrationEffect.createOneShot(45, VibrationEffect.DEFAULT_AMPLITUDE)
            Cue.Error -> VibrationEffect.createWaveform(longArrayOf(0, 55, 70, 55), -1)
            Cue.Attention -> VibrationEffect.createWaveform(longArrayOf(0, 35, 90, 35), -1)
        }
        vibrator.vibrate(effect)
    }

    private fun vibrator(): Vibrator? = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        app.getSystemService(VibratorManager::class.java)?.defaultVibrator
    } else {
        @Suppress("DEPRECATION")
        app.getSystemService(Vibrator::class.java)
    }

    private companion object {
        const val RATE = 22050

        // Flipper note table (notifications.c message_note_*).
        const val G4 = 392.00
        const val C5 = 523.25
        const val E5 = 659.25
        const val G5 = 783.99
    }
}
