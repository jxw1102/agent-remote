package com.bb10d.remote.data

import java.time.Instant
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * Timestamp handling for two agents that disagree on precision.
 *
 * Claude writes `2026-08-02T04:43:38.996Z`; grok writes nanoseconds
 * (`2026-07-29T02:50:57.252366406Z`) and occasionally a zone-less local time.
 * Sorting the unified list needs all of them on one axis, so parse
 * defensively and treat "unreadable" as epoch rather than dropping the row.
 */
object Time {
    private val zone: ZoneId get() = ZoneId.systemDefault()
    private val timeFmt = DateTimeFormatter.ofPattern("HH:mm", Locale.getDefault())
    private val dayFmt = DateTimeFormatter.ofPattern("d MMM", Locale.getDefault())
    private val fullFmt = DateTimeFormatter.ofPattern("d MMM yyyy, HH:mm", Locale.getDefault())

    fun epochMs(iso: String): Long {
        if (iso.isBlank()) return 0
        runCatching { return Instant.parse(iso).toEpochMilli() }
        runCatching { return OffsetDateTime.parse(iso).toInstant().toEpochMilli() }
        runCatching {
            return LocalDateTime.parse(iso).atZone(zone).toInstant().toEpochMilli()
        }
        return 0
    }

    /** "14:32" today, "28 Jul" this year, "28 Jul 2025" beyond. */
    fun relativeStamp(epochMs: Long, nowMs: Long = System.currentTimeMillis()): String {
        if (epochMs <= 0) return ""
        val then = Instant.ofEpochMilli(epochMs).atZone(zone)
        val now = Instant.ofEpochMilli(nowMs).atZone(zone)
        return when {
            then.toLocalDate() == now.toLocalDate() -> then.format(timeFmt)
            then.year == now.year -> then.format(dayFmt)
            else -> then.format(fullFmt)
        }
    }

    fun fullStamp(epochMs: Long): String =
        if (epochMs <= 0) "" else Instant.ofEpochMilli(epochMs).atZone(zone).format(fullFmt)

    /** "3m 12s" — the elapsed counter in the live status banner. */
    fun elapsed(seconds: Int): String {
        if (seconds < 60) return "${seconds}s"
        val m = seconds / 60
        val s = seconds % 60
        if (m < 60) return "${m}m ${s}s"
        return "${m / 60}h ${m % 60}m"
    }

    fun humanSize(bytes: Long): String = when {
        bytes <= 0 -> ""
        bytes < 1024 -> "$bytes B"
        bytes < 1024 * 1024 -> "${bytes / 1024} KB"
        bytes < 1024L * 1024 * 1024 -> String.format(Locale.US, "%.1f MB", bytes / 1048576.0)
        else -> String.format(Locale.US, "%.1f GB", bytes / 1073741824.0)
    }
}
