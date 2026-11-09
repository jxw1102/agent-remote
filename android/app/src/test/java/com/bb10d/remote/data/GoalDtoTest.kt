package com.bb10d.remote.data

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The goal row, parsed from what the daemon actually sends.
 *
 * The payload below is a real `GET /api/goals` reply (agentremoted 2.19.0).
 * A renamed field would otherwise read as a silent default - a goal that
 * looks idle for ever, or a switch that never appears to be on.
 */
class GoalDtoTest {

    private val json = Json { ignoreUnknownKeys = true }

    private val live = """
        {"goals": [{
          "session_id": "01a1694f-0750-75b0-9c69-fb3db54d70fc",
          "objective": "have android 7 runtime working on my blackberry device",
          "status": "active",
          "tokens_used": 28143555.0,
          "time_used_s": 224285.0,
          "updated_at": 1793232883.087,
          "watching": true,
          "resumes": 2,
          "last_resume_at": 1793232894.4,
          "stopped_why": "",
          "live_tui": false,
          "working": false,
          "idle_s": 612.5,
          "idle_threshold_s": 300,
          "provider": "codex"
        }]}
    """.trimIndent()

    @Test
    fun `a live goals reply parses`() {
        val row = json.decodeFromString(GoalsDto.serializer(), live).goals.single()
        assertEquals("01a1694f-0750-75b0-9c69-fb3db54d70fc", row.sessionId)
        assertEquals("active", row.status)
        assertTrue(row.watching)
        assertEquals(2, row.resumes)
        assertEquals(612.5, row.idleS, 0.01)
        assertEquals(300.0, row.idleThresholdS, 0.01)
        assertEquals(false, row.liveTui)
        assertEquals("", row.stoppedWhy)
    }

    @Test
    fun `a daemon with no goals is not an error`() {
        val empty = json.decodeFromString(GoalsDto.serializer(),
            """{"goals": [], "supported": false}""")
        assertTrue(empty.goals.isEmpty())
    }

    @Test
    fun `the watch reply carries the updated row`() {
        val reply = json.decodeFromString(
            GoalWatchReplyDto.serializer(),
            """{"ok": true, "goal": {"session_id": "s", "status": "active",
               "watching": true, "stopped_why": "", "idle_s": 0}}""",
        )
        assertTrue(reply.ok)
        assertEquals(true, reply.goal?.watching)
    }
}
