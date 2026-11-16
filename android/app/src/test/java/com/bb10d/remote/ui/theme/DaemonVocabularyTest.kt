package com.bb10d.remote.ui.theme

import androidx.compose.ui.graphics.Color
import com.bb10d.remote.data.PaletteDto
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class DaemonVocabularyTest {

    @Test
    fun hexParsing() {
        assertEquals(Color(0xFFD97757), DaemonVocabulary.parseHex("#d97757"))
        assertEquals(Color(0xFF00D4FF), DaemonVocabulary.parseHex("00D4FF"))
        assertNull(DaemonVocabulary.parseHex(""))
        assertNull(DaemonVocabulary.parseHex("#fff"))
        assertNull(DaemonVocabulary.parseHex("#zzzzzz"))
    }

    @Test
    fun daemonPaletteReplacesTheBuiltIn() {
        // Codex is untouched by the other tests in this class.
        val before = Accent.Codex.tint
        DaemonVocabulary.usePalettes(
            mapOf(
                "codex" to PaletteDto(
                    label = "Codex CLI", accent = "#112233", surface = "#010203",
                    heading = "#445566",
                ),
                // Unknown harness and a palette with no accent are ignored.
                "mystery" to PaletteDto(label = "Mystery", accent = "#ffffff"),
                "grok" to PaletteDto(label = "Not Grok"),
            ),
        )
        assertEquals(Color(0xFF484CF4), before)
        assertEquals(Color(0xFF112233), Accent.Codex.tint)
        assertEquals(Color(0xFF010203), Accent.Codex.onTint)
        assertEquals("Codex CLI", Accent.Codex.label)
        assertEquals(Color(0xFF445566), DaemonVocabulary.colour(Accent.Codex, "heading"))
        // A role the daemon left out falls back (null here, built-in at the call site).
        assertNull(DaemonVocabulary.colour(Accent.Codex, "inline"))
        assertEquals("Grok", Accent.Grok.label)
        assertEquals("Agent", Accent.Neutral.label)
    }

    @Test
    fun statesCarryLabelAndColourPerSurface() {
        assertNull(DaemonVocabulary.stateColour("needs_answer", dark = true))
        DaemonVocabulary.useStates(
            listOf(
                com.bb10d.remote.data.StateDto(
                    id = "needs_answer", label = "needs answer", role = "attention",
                    color = "#e0b454", colorLight = "#8a6100",
                ),
                com.bb10d.remote.data.StateDto(id = "turn_finished", label = "turn finished"),
            ),
        )
        assertEquals(Color(0xFFE0B454), DaemonVocabulary.stateColour("needs_answer", dark = true))
        assertEquals(Color(0xFF8A6100), DaemonVocabulary.stateColour("needs_answer", dark = false))
        assertEquals("needs answer", DaemonVocabulary.focusLabel("needs_answer"))
        // No state colour: the caller keeps its own dim text.
        assertNull(DaemonVocabulary.stateColour("turn_finished", dark = true))
    }

    @Test
    fun focusLabelsComeFromTheDaemon() {
        assertNull(DaemonVocabulary.focusLabel("working"))
        DaemonVocabulary.useFocusLabels(mapOf("working" to "busy", "failed" to ""))
        assertEquals("busy", DaemonVocabulary.focusLabel("working"))
        assertNull(DaemonVocabulary.focusLabel("failed"))
    }
}
