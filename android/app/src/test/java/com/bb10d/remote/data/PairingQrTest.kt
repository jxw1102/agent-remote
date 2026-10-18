package com.bb10d.remote.data

import com.google.zxing.BinaryBitmap
import com.google.zxing.LuminanceSource
import com.google.zxing.MultiFormatReader
import com.google.zxing.common.HybridBinarizer
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The two halves of the camera, meeting.
 *
 * `pairing-qr.txt` is the module grid the web client's own encoder
 * (web/src/qr.js) produced for the payload `pairingUri()` builds; this
 * decodes it with the same ZXing the phone runs. A change to either side
 * that breaks pairing fails here rather than on someone's kitchen table.
 *
 * Regenerate with web/src/qr.js `qrMatrix(uri, "M")` if the payload changes.
 */
class PairingQrTest {

    @Test
    fun `a QR drawn by the web client decodes into a profile`() {
        val grid = javaClass.getResourceAsStream("/pairing-qr.txt")!!
            .bufferedReader().readLines().filter { it.isNotBlank() }
        val result = MultiFormatReader().decode(
            BinaryBitmap(HybridBinarizer(ModuleGridSource(grid))),
        )
        val paired = Pairing.parse(result.text)!!
        assertEquals("http://192.168.1.20:8473", paired.baseUrl)
        assertEquals("s3cr3t-token-abc", paired.token)
        assertEquals("Mac · Claude", paired.name)
    }

    /**
     * The grid as pixels: four pixels per module and a four-module quiet
     * zone, i.e. what a camera would see of the code on screen.
     */
    private class ModuleGridSource(
        grid: List<String>,
        private val scale: Int = 4,
        private val quiet: Int = 4,
    ) : LuminanceSource(
        (grid.size + quiet * 2) * scale,
        (grid.size + quiet * 2) * scale,
    ) {
        private val pixels = ByteArray(width * height) { -1 }.also { out ->
            grid.forEachIndexed { my, row ->
                row.forEachIndexed { mx, cell ->
                    if (cell != '#') return@forEachIndexed
                    for (dy in 0 until scale) {
                        val y = (my + quiet) * scale + dy
                        for (dx in 0 until scale) {
                            out[y * width + (mx + quiet) * scale + dx] = 0
                        }
                    }
                }
            }
        }

        override fun getRow(y: Int, row: ByteArray?): ByteArray {
            val out = if (row != null && row.size >= width) row else ByteArray(width)
            System.arraycopy(pixels, y * width, out, 0, width)
            return out
        }

        override fun getMatrix(): ByteArray = pixels
    }
}
