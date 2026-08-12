package com.bb10d.remote.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import java.util.UUID

/**
 * One daemon the app can talk to.
 *
 * A profile is not "a provider": the provider is whatever `/api/ping` on that
 * host answers with, so pointing a profile at a different daemon re-badges it
 * automatically. `provider` here is only the last known answer, used to paint
 * the row before the first ping of a cold start lands.
 */
@Serializable
data class Profile(
    val id: String = UUID.randomUUID().toString(),
    val name: String = "",
    val baseUrl: String = "",
    val token: String = "",
    /** Include this daemon's sessions in the unified list. */
    val enabled: Boolean = true,
    /** Last known /api/ping result, cached across launches. */
    val caps: Caps = Caps(),
    /** Per-profile composer defaults (each daemon has its own habits). */
    val execMode: String = "",
    val model: String = "",
    val effort: String = "",
) {
    val provider: String get() = caps.provider
    /** Focus support, cached from the last ping. */
    val focus: Boolean get() = caps.focus
    val displayName: String get() = name.ifBlank { hostLabel }

    /** "192.168.1.5:8473" — what the user recognises a daemon by. */
    val hostLabel: String
        get() = baseUrl
            .removePrefix("https://")
            .removePrefix("http://")
            .trimEnd('/')
            .ifBlank { "unconfigured" }

    val configured: Boolean get() = baseUrl.isNotBlank() && token.isNotBlank()

    /** Stored execution mode (interactive | headless), defaulting by caps. */
    fun effectiveExecMode(): String = when {
        execMode.isNotBlank() -> ExecMode.normalize(execMode, caps.interactive)
        caps.interactive -> ExecMode.INTERACTIVE
        else -> ExecMode.HEADLESS
    }

    /** `permission_mode` value for the HTTP API. */
    fun wireExecMode(): String = ExecMode.wire(effectiveExecMode())
}

@Serializable
private data class ProfileEnvelope(
    val profiles: List<Profile> = emptyList(),
    val activeId: String = "",
)

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore("agentremote")

/**
 * Persistent profile list. Tokens are wrapped by [Crypto] on the way out and
 * unwrapped on the way in, so the on-disk JSON never carries a usable secret.
 */
class ProfileStore(context: Context) {
    private val store = context.applicationContext.dataStore
    private val key = stringPreferencesKey("profiles.v1")

    val state: Flow<ProfileState> = store.data.map { prefs ->
        val raw = prefs[key].orEmpty()
        if (raw.isBlank()) return@map ProfileState()
        val env = runCatching { Json.decodeFromString<ProfileEnvelope>(raw) }
            .getOrElse { return@map ProfileState() }
        val decoded = env.profiles.map { it.copy(token = Crypto.decrypt(it.token)) }
        ProfileState(
            profiles = decoded,
            activeId = env.activeId.takeIf { id -> decoded.any { it.id == id } }
                ?: decoded.firstOrNull()?.id.orEmpty(),
        )
    }

    suspend fun upsert(profile: Profile) = mutate { state ->
        val list = state.profiles.toMutableList()
        val at = list.indexOfFirst { it.id == profile.id }
        if (at >= 0) list[at] = profile else list.add(profile)
        state.copy(
            profiles = list,
            activeId = state.activeId.ifBlank { profile.id },
        )
    }

    suspend fun delete(id: String) = mutate { state ->
        val list = state.profiles.filterNot { it.id == id }
        state.copy(
            profiles = list,
            activeId = if (state.activeId == id) list.firstOrNull()?.id.orEmpty()
            else state.activeId,
        )
    }

    suspend fun setActive(id: String) = mutate { it.copy(activeId = id) }

    suspend fun setEnabled(id: String, enabled: Boolean) = mutate { state ->
        state.copy(profiles = state.profiles.map {
            if (it.id == id) it.copy(enabled = enabled) else it
        })
    }

    suspend fun reorder(from: Int, to: Int) = mutate { state ->
        val list = state.profiles.toMutableList()
        if (from !in list.indices || to !in list.indices) return@mutate state
        list.add(to, list.removeAt(from))
        state.copy(profiles = list)
    }

    /** Merge a fresh ping into the cached caps without touching credentials. */
    suspend fun updateCaps(id: String, caps: Caps) = mutate { state ->
        state.copy(profiles = state.profiles.map {
            if (it.id == id) it.copy(caps = caps) else it
        })
    }

    suspend fun updateComposerDefaults(
        id: String,
        execMode: String? = null,
        model: String? = null,
        effort: String? = null,
    ) = mutate { state ->
        state.copy(profiles = state.profiles.map {
            if (it.id != id) it else it.copy(
                execMode = execMode ?: it.execMode,
                model = model ?: it.model,
                effort = effort ?: it.effort,
            )
        })
    }

    private suspend fun mutate(block: (ProfileState) -> ProfileState) {
        store.edit { prefs ->
            val raw = prefs[key].orEmpty()
            val env = if (raw.isBlank()) ProfileEnvelope()
            else runCatching { Json.decodeFromString<ProfileEnvelope>(raw) }
                .getOrElse { ProfileEnvelope() }
            val current = ProfileState(
                profiles = env.profiles.map { it.copy(token = Crypto.decrypt(it.token)) },
                activeId = env.activeId,
            )
            val next = block(current)
            prefs[key] = Json.encodeToString(
                ProfileEnvelope(
                    profiles = next.profiles.map { it.copy(token = Crypto.encrypt(it.token)) },
                    activeId = next.activeId,
                ),
            )
        }
    }
}

data class ProfileState(
    val profiles: List<Profile> = emptyList(),
    val activeId: String = "",
) {
    val enabled: List<Profile> get() = profiles.filter { it.enabled && it.configured }
    fun byId(id: String): Profile? = profiles.firstOrNull { it.id == id }
    val active: Profile? get() = byId(activeId) ?: profiles.firstOrNull()
}
