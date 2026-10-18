package com.bb10d.remote.data

/**
 * One daemon, handed over by camera.
 *
 * The web client draws `agentremote://profile?url=…&token=…&name=…` as a QR
 * code on its daemon form; this reads it back. Typing a 40-character token on
 * a phone keyboard is the worst part of setting a profile up, so the camera
 * does it instead.
 *
 * Parsed by hand rather than with `android.net.Uri`: the payload is a fixed
 * shape, this keeps it unit-testable off-device, and `Uri` would decode a
 * literal `+` in a token as a space.
 */
data class Pairing(val name: String, val baseUrl: String, val token: String) {

    companion object {
        private const val PREFIX = "agentremote://profile?"

        /** null when this is not a pairing payload, or carries no address. */
        fun parse(raw: String): Pairing? {
            val text = raw.trim()
            if (!text.startsWith(PREFIX, ignoreCase = true)) return null
            var name = ""
            var url = ""
            var token = ""
            for (pair in text.substring(PREFIX.length).split('&')) {
                if (pair.isEmpty()) continue
                val eq = pair.indexOf('=')
                if (eq <= 0) continue
                val value = percentDecode(pair.substring(eq + 1))
                when (pair.substring(0, eq).lowercase()) {
                    "name" -> name = value
                    "url" -> url = value
                    "token" -> token = value
                }
            }
            if (url.isBlank()) return null
            return Pairing(name.trim(), url.trim(), token.trim())
        }

        /** %XX -> bytes -> UTF-8. A stray `%` is kept as itself. */
        private fun percentDecode(s: String): String {
            if (!s.contains('%')) return s
            val out = java.io.ByteArrayOutputStream(s.length)
            var i = 0
            while (i < s.length) {
                val c = s[i]
                val hex = if (c == '%' && i + 2 < s.length) {
                    s.substring(i + 1, i + 3).toIntOrNull(16)
                } else {
                    null
                }
                if (hex != null) {
                    out.write(hex)
                    i += 3
                } else {
                    // Anything unescaped is already text; keep its own bytes.
                    out.write(c.toString().toByteArray(Charsets.UTF_8))
                    i++
                }
            }
            return out.toString(Charsets.UTF_8.name())
        }
    }
}
