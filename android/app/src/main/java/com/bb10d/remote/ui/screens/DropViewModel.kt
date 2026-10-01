package com.bb10d.remote.ui.screens

import android.content.ContentValues
import android.content.Context
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.bb10d.remote.data.AgentRepository
import com.bb10d.remote.data.DropFileDto
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream
import java.io.OutputStream

data class DropFeed(
    val loading: Boolean = false,
    val path: String = "",
    val files: List<DropFileDto> = emptyList(),
    val error: String? = null,
)

/** One row of the merged inbox: a file plus the daemon that holds it. */
data class DropRow(
    val profileId: String,
    val profileName: String,
    val provider: String,
    val file: DropFileDto,
    /** Profiles whose identical copy was hidden by the dedup. */
    val alsoOn: List<String> = emptyList(),
)

class DropViewModel(private val repo: AgentRepository) : ViewModel() {
    val profiles = repo.profiles

    private val _state = MutableStateFlow<Map<String, DropFeed>>(emptyMap())
    val state: StateFlow<Map<String, DropFeed>> = _state.asStateFlow()

    /**
     * The merged inbox: every daemon's files in one list, newest first.
     *
     * Identical (name, size, mtime) entries from different profiles are one
     * file — the common cause is two profiles reaching the SAME daemon via
     * different URLs. The first-listed profile's copy wins (that order is the
     * user's preference), and the hidden sources ride along as `alsoOn` so
     * a delete never looks like it magically resurrected the file.
     */
    val rows: StateFlow<List<DropRow>> =
        combine(repo.profiles, _state) { profiles, feeds ->
            val byKey = LinkedHashMap<String, DropRow>()
            profiles.enabled.forEach { profile ->
                val feed = feeds[profile.id] ?: return@forEach
                feed.files.forEach { file ->
                    val key = "${file.name}|${file.size}|${file.mtime}"
                    val existing = byKey[key]
                    if (existing == null) {
                        byKey[key] = DropRow(
                            profileId = profile.id,
                            profileName = profile.displayName,
                            provider = profile.provider,
                            file = file,
                        )
                    } else {
                        byKey[key] = existing.copy(
                            alsoOn = existing.alsoOn + profile.displayName,
                        )
                    }
                }
            }
            byKey.values.sortedByDescending { it.file.mtime }
        }.stateIn(viewModelScope, SharingStarted.Eagerly, emptyList())

    private val _message = MutableStateFlow<String?>(null)
    val message: StateFlow<String?> = _message.asStateFlow()

    private var downloading by mutableStateOf<Set<String>>(emptySet())

    fun isDownloading(profileId: String, name: String) = downloading.contains("$profileId/$name")

    /** Fraction received per download key; absent while the size is unknown. */
    private var progress by mutableStateOf<Map<String, Float>>(emptyMap())

    fun downloadProgress(profileId: String, name: String): Float? = progress["$profileId/$name"]

    fun clearMessage() {
        _message.value = null
    }

    fun refresh() {
        repo.profiles.value.enabled.forEach { profile ->
            val client = repo.client(profile)
            _state.value = _state.value + (profile.id to DropFeed(loading = true))
            viewModelScope.launch {
                runCatching { client.dropList() }
                    .onSuccess {
                        _state.value = _state.value + (
                            profile.id to DropFeed(path = it.path, files = it.files)
                            )
                    }
                    .onFailure {
                        _state.value = _state.value + (
                            profile.id to DropFeed(error = repo.reason(it))
                            )
                    }
            }
        }
    }

    fun delete(profileId: String, name: String) {
        val client = repo.client(profileId) ?: return
        viewModelScope.launch {
            runCatching { client.dropDelete(name) }
                .onSuccess {
                    val feed = _state.value[profileId] ?: return@onSuccess
                    _state.value = _state.value + (
                        profileId to feed.copy(files = feed.files.filterNot { it.name == name })
                        )
                    _message.value = "Deleted $name on the host"
                }
                .onFailure { _message.value = repo.reason(it) }
        }
    }

    /** Saves into the phone's Downloads, written to disk as it arrives. */
    fun download(context: Context, profileId: String, name: String) {
        val client = repo.client(profileId) ?: return
        val key = "$profileId/$name"
        if (downloading.contains(key)) return
        downloading = downloading + key
        viewModelScope.launch {
            val sink = DownloadSink(context)
            var lastPct = -1
            runCatching {
                // Save under the name the daemon served, not the entry name:
                // a folder arrives zipped, so its download is "<name>.zip".
                val saved = client.dropDownload(name, sink::open) { got, total ->
                    if (total > 0) {
                        val pct = ((got * 100) / total).toInt()
                        if (pct != lastPct) {
                            lastPct = pct
                            viewModelScope.launch { progress = progress + (key to pct / 100f) }
                        }
                    }
                }
                withContext(Dispatchers.IO) { sink.finish() }
                saved
            }
                .onSuccess { _message.value = "Saved to ${sink.where}" }
                .onFailure {
                    withContext(Dispatchers.IO) { sink.discard() }
                    _message.value = repo.reason(it)
                }
            downloading = downloading - key
            progress = progress - key
        }
    }

    /**
     * Where a download lands in the phone's Downloads, written as it arrives.
     *
     * MediaStore is the only way to write there without a storage permission,
     * and it only exists from Android 10 — older devices fall back to the
     * app's own external files directory, which needs no permission either.
     * [open] may be called a second time (the host's file changed mid-resume):
     * the half-written entry is dropped and a fresh one begun.
     */
    private class DownloadSink(private val context: Context) {
        private var uri: android.net.Uri? = null
        private var file: File? = null
        var where: String = ""
            private set

        fun open(name: String): OutputStream {
            discard()
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val values = ContentValues().apply {
                    put(MediaStore.Downloads.DISPLAY_NAME, name)
                    // application/zip, so tapping the finished download opens
                    // the archive instead of the "can't open this file" dialog.
                    val mime = if (name.endsWith(".zip", ignoreCase = true)) {
                        "application/zip"
                    } else {
                        "application/octet-stream"
                    }
                    put(MediaStore.Downloads.MIME_TYPE, mime)
                    put(MediaStore.Downloads.IS_PENDING, 1)
                }
                val resolver = context.contentResolver
                val u = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                    ?: error("Could not create the download")
                uri = u
                where = "Downloads/$name"
                return resolver.openOutputStream(u) ?: error("Could not write the download")
            }
            val dir = context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS)
                ?: context.filesDir
            val f = File(dir, name)
            file = f
            where = f.absolutePath
            return FileOutputStream(f)
        }

        /** Publishes the finished MediaStore entry (no-op for a plain file). */
        fun finish() {
            val u = uri ?: return
            val values = ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }
            context.contentResolver.update(u, values, null, null)
            uri = null
        }

        /** Removes a half-written download so a failure leaves no stub. */
        fun discard() {
            uri?.let { runCatching { context.contentResolver.delete(it, null, null) } }
            file?.let { runCatching { it.delete() } }
            uri = null
            file = null
        }
    }
}
