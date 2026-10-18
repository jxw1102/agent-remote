package com.bb10d.remote.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * The payloads here are exactly what web/src/app.js `pairingUri()` emits —
 * the two sides of the camera have to agree, and only one of them is Kotlin.
 */
class PairingTest {

    @Test
    fun `reads a full payload`() {
        val p = Pairing.parse(
            "agentremote://profile?url=http%3A%2F%2F192.168.1.20%3A8473" +
                "&token=s3cr3t-token-abc&name=Mac%20%C2%B7%20Claude",
        )!!
        assertEquals("http://192.168.1.20:8473", p.baseUrl)
        assertEquals("s3cr3t-token-abc", p.token)
        assertEquals("Mac · Claude", p.name)
    }

    @Test
    fun `name is optional`() {
        val p = Pairing.parse("agentremote://profile?url=https%3A%2F%2Fx.example&token=t")!!
        assertEquals("https://x.example", p.baseUrl)
        assertEquals("t", p.token)
        assertEquals("", p.name)
    }

    @Test
    fun `keeps a literal plus in a token`() {
        // Uri.getQueryParameter would hand back a space here.
        val p = Pairing.parse("agentremote://profile?url=http%3A%2F%2Fh&token=a+b%2Bc")!!
        assertEquals("a+b+c", p.token)
    }

    @Test
    fun `decodes multi-byte characters`() {
        val p = Pairing.parse(
            "agentremote://profile?url=http%3A%2F%2Fh&token=t&name=%E6%97%A5%E6%9C%AC",
        )!!
        assertEquals("日本", p.name)
    }

    @Test
    fun `rejects anything that is not a pairing payload`() {
        assertNull(Pairing.parse("https://github.com/jxw1102/agent-remote"))
        assertNull(Pairing.parse("agentremote://session?name=foo"))
        assertNull(Pairing.parse(""))
        // A payload with no address is useless even though it parses.
        assertNull(Pairing.parse("agentremote://profile?token=t"))
    }
}
