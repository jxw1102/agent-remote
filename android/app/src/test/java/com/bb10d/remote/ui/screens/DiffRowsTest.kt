package com.bb10d.remote.ui.screens

import com.bb10d.remote.data.ChangedFileDto
import com.bb10d.remote.data.ChangesDto
import org.junit.Assert.assertEquals
import org.junit.Test

class DiffRowsTest {

    private val diff = """
        diff --git a/src/auth.py b/src/auth.py
        index 1111111..2222222 100644
        --- a/src/auth.py
        +++ b/src/auth.py
        @@ -1,3 +1,4 @@
         def login(user):
        +    check(user)
        -    pass
        diff --git a/old name.txt b/new name.txt
        similarity index 100%
        rename from old name.txt
        rename to new name.txt
    """.trimIndent() + "\n"

    @Test
    fun splitsIntoFilesAndTaggedLines() {
        val rows = diffRows(diff)
        assertEquals(
            listOf("src/auth.py", "new name.txt"),
            rows.filterIsInstance<DiffRow.File>().map { it.path },
        )
        val kinds = rows.filterIsInstance<DiffRow.Line>().joinToString("") { it.kind.toString() }
        // ---, +++, @@, context, added, removed, then the two rename lines.
        assertEquals("mm@ +-mm", kinds)
    }

    @Test
    fun linesThatStartWithDashesOrPlusesAreChangesInsideAHunk() {
        val sql = """
            diff --git a/q.sql b/q.sql
            --- a/q.sql
            +++ b/q.sql
            @@ -1,3 +1,3 @@
             select 1;
            --- drop legacy table
            +++count;
        """.trimIndent() + "\n"
        val kinds = diffRows(sql).filterIsInstance<DiffRow.Line>().joinToString("") { it.kind.toString() }
        // The header's --- and +++ are names; the same marks after @@ are a
        // removed "-- drop legacy table" and an added "++count;".
        assertEquals("mm@ -+", kinds)
    }

    @Test
    fun emptyDiffHasNoRows() {
        assertEquals(emptyList<DiffRow>(), diffRows(""))
    }

    @Test
    fun summaryLine() {
        val one = ChangesDto(
            repo = true, branch = "main", ahead = 2, behind = 1, added = 12, removed = 4,
            files = listOf(ChangedFileDto(path = "a")),
        )
        assertEquals("main · ↑2 · ↓1 · 1 file · +12 −4", changesSummary(one))
        assertEquals("detached · no changes", changesSummary(ChangesDto(repo = true)))
    }
}
