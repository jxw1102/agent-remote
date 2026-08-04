package com.bb10d.remote.data

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Wraps daemon tokens with a non-exportable AndroidKeyStore AES key.
 *
 * A daemon token is a bearer credential for a shell on someone's machine, so
 * it must not sit in DataStore as plain text where a rooted-device dump or a
 * mis-set backup rule would surface it. The key never leaves the keystore, so
 * a copied data directory decrypts to nothing.
 *
 * Failure is never fatal: if the keystore is unavailable (a few OEM builds
 * with a broken TEE), [encrypt] returns the value marked as plain so the app
 * still works — losing at-rest protection beats losing the profile.
 */
object Crypto {
    private const val KEY_ALIAS = "agentremote.tokens.v1"
    private const val TRANSFORM = "AES/GCM/NoPadding"
    private const val TAG_BITS = 128
    private const val PREFIX_ENC = "v1:"
    private const val PREFIX_PLAIN = "p0:"

    private fun key(): SecretKey? = try {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getEntry(KEY_ALIAS, null) as? KeyStore.SecretKeyEntry)?.secretKey ?: generate()
    } catch (t: Throwable) {
        null
    }

    private fun generate(): SecretKey? = try {
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        gen.init(
            KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build(),
        )
        gen.generateKey()
    } catch (t: Throwable) {
        null
    }

    fun encrypt(plain: String): String {
        if (plain.isEmpty()) return ""
        val k = key() ?: return PREFIX_PLAIN + plain
        return try {
            val cipher = Cipher.getInstance(TRANSFORM)
            cipher.init(Cipher.ENCRYPT_MODE, k)
            val out = cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
            PREFIX_ENC + b64(cipher.iv) + ":" + b64(out)
        } catch (t: Throwable) {
            PREFIX_PLAIN + plain
        }
    }

    fun decrypt(stored: String): String {
        if (stored.isEmpty()) return ""
        if (stored.startsWith(PREFIX_PLAIN)) return stored.removePrefix(PREFIX_PLAIN)
        if (!stored.startsWith(PREFIX_ENC)) return stored // pre-v1 value
        val parts = stored.removePrefix(PREFIX_ENC).split(":")
        if (parts.size != 2) return ""
        val k = key() ?: return ""
        return try {
            val cipher = Cipher.getInstance(TRANSFORM)
            cipher.init(Cipher.DECRYPT_MODE, k, GCMParameterSpec(TAG_BITS, unb64(parts[0])))
            String(cipher.doFinal(unb64(parts[1])), Charsets.UTF_8)
        } catch (t: Throwable) {
            ""
        }
    }

    private fun b64(bytes: ByteArray) = Base64.encodeToString(bytes, Base64.NO_WRAP)
    private fun unb64(s: String) = Base64.decode(s, Base64.NO_WRAP)
}
