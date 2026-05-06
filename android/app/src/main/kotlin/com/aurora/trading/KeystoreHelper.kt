package com.aurora.trading

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Android Keystore 기반 API 키 AES-GCM 암호화 저장소.
 *
 * 암호화 키 = AndroidKeyStore provider (TEE/HSM 하드웨어 보안 모듈).
 * 암호문 = Base64(IV):Base64(ciphertext) 형식으로 SharedPreferences 저장.
 *
 * Why: filesDir/.env 평문 저장 대신 하드웨어 보안 모듈에 키를 위탁.
 *      루팅 없이 암호화 키 추출 불가 (TEE 보장).
 *      외부 라이브러리 불필요 — 표준 Android API (API 23+) 만 사용.
 */
object KeystoreHelper {

    private const val KEY_ALIAS = "aurora_api_key_v1"
    private const val PREFS_NAME = "aurora_secure_prefs"
    private const val TRANSFORM = "AES/GCM/NoPadding"
    private const val GCM_TAG_BITS = 128

    private fun secretKey(): SecretKey {
        val keyStore = KeyStore.getInstance("AndroidKeyStore").also { it.load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
            .also {
                it.init(
                    KeyGenParameterSpec.Builder(
                        KEY_ALIAS,
                        KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                    )
                        .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                        .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                        .build(),
                )
            }
            .generateKey()
    }

    private fun encrypt(plain: String): String {
        val cipher = Cipher.getInstance(TRANSFORM).also { it.init(Cipher.ENCRYPT_MODE, secretKey()) }
        val iv = Base64.encodeToString(cipher.iv, Base64.NO_WRAP)
        val ct = Base64.encodeToString(cipher.doFinal(plain.toByteArray()), Base64.NO_WRAP)
        return "$iv:$ct"
    }

    private fun decrypt(blob: String): String {
        val parts = blob.split(":", limit = 2)
        if (parts.size != 2) return ""
        val iv = Base64.decode(parts[0], Base64.NO_WRAP)
        val ct = Base64.decode(parts[1], Base64.NO_WRAP)
        val cipher = Cipher.getInstance(TRANSFORM).also {
            it.init(Cipher.DECRYPT_MODE, secretKey(), GCMParameterSpec(GCM_TAG_BITS, iv))
        }
        return String(cipher.doFinal(ct))
    }

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    /** API 키 + 시크릿 암호화 저장. */
    fun save(context: Context, exchange: String, apiKey: String, apiSecret: String) {
        prefs(context).edit()
            .putString("${exchange}_key", encrypt(apiKey))
            .putString("${exchange}_secret", encrypt(apiSecret))
            .apply()
    }

    fun loadKey(context: Context, exchange: String): String {
        val blob = prefs(context).getString("${exchange}_key", "") ?: return ""
        return if (blob.isEmpty()) "" else runCatching { decrypt(blob) }.getOrDefault("")
    }

    fun loadSecret(context: Context, exchange: String): String {
        val blob = prefs(context).getString("${exchange}_secret", "") ?: return ""
        return if (blob.isEmpty()) "" else runCatching { decrypt(blob) }.getOrDefault("")
    }

    /** 해당 거래소 키가 저장되어 있는지 확인. */
    fun has(context: Context, exchange: String): Boolean =
        prefs(context).getString("${exchange}_key", "").orEmpty().isNotEmpty()

    /** 거래소 키 삭제 (초기화 시). */
    fun clear(context: Context, exchange: String) {
        prefs(context).edit()
            .remove("${exchange}_key")
            .remove("${exchange}_secret")
            .apply()
    }
}
