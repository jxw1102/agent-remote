package com.bb10d.remote.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * /api/ping must decode even when `caps` carries a non-boolean: agentremoted
 * 2.13.0 put `tui_keys: 2` there and the app refused every ping for it
 * ("The daemon sent something this app cannot read").
 */
class PingDtoTest {
    private fun resource(name: String): String =
        checkNotNull(javaClass.classLoader?.getResourceAsStream(name)) { "missing $name" }
            .bufferedReader().readText()

    @Test
    fun decodesARealMultiHarnessPing() {
        val ping = Json.decodeFromString(PingDto.serializer(), resource("ping-2.14.3.json"))
        assertTrue(ping.ok)
        assertEquals("2.14.3", ping.version)
        assertTrue(ping.multi)
        assertEquals(6, ping.providers.size)
        assertEquals(true, ping.caps["live_tui"])
        // The version number inside caps reads as a flag, not a failure.
        assertEquals(true, ping.caps["tui_keys"])
        assertEquals(true, ping.providerDetails.getValue("claude").caps["permissions"])
        assertEquals(false, ping.providerDetails.getValue("grok").caps["permissions"])
        assertEquals(256, ping.maxUploadMb)
    }

    @Test
    fun capsSkipWhatIsNeitherFlagNorNumber() {
        val ping = Json.decodeFromString(
            PingDto.serializer(),
            """{"ok": true, "caps": {"queue": true, "tui_keys": 2, "off": 0,
                "label": "x", "nested": {"a": 1}}}""",
        )
        assertEquals(mapOf("queue" to true, "tui_keys" to true, "off" to false), ping.caps)
    }
}
