package com.bb10d.remote.ui.screens

import androidx.compose.animation.core.Animatable
import androidx.compose.runtime.rememberCoroutineScope
import kotlinx.coroutines.launch
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.List
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.bb10d.remote.data.SessionRef
import com.bb10d.remote.data.SessionTab
import com.bb10d.remote.ui.components.WorkingPulse
import com.bb10d.remote.ui.theme.Accent
import com.bb10d.remote.ui.theme.palette
import kotlin.math.roundToInt

/** A phone held upright: the session page is a sheet with tabs above it. */
@Composable
internal fun isSheetLayout(): Boolean {
    val c = LocalConfiguration.current
    return c.screenWidthDp < 600 && c.screenHeightDp > c.screenWidthDp
}

/**
 * The session page as a sheet over a dimmed band, with the open
 * conversations above it as bubbles, like a browser's tabs: tap one to
 * switch, the list button to go back, the open one's × (or a long press on
 * any) to close it. Dragging the grip down closes the sheet.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
internal fun SessionSheetFrame(
    tabs: List<SessionTab>,
    current: SessionRef,
    working: Set<String>,
    blocked: Set<String>,
    onHome: () -> Unit,
    onOpen: (SessionTab) -> Unit,
    onClose: (SessionTab) -> Unit,
    content: @Composable BoxScope.() -> Unit,
) {
    val pal = palette
    val density = LocalDensity.current
    // How far the sheet has been dragged down by its grip.
    val drag = remember { Animatable(0f) }
    val scope = rememberCoroutineScope()
    // The band behind the sheet: the app's background, darkened, as a
    // modal sheet's scrim would leave the page under it.
    val band = Color.Black.copy(alpha = 0.55f)
    Column(
        Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background)
            .background(band),
    ) {
        TabStrip(tabs, current, working, blocked, onHome, onOpen, onClose)
        Box(
            Modifier
                .weight(1f)
                .fillMaxWidth()
                .offset { IntOffset(0, drag.value.roundToInt()) }
                .shadow(18.dp, RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp))
                .clip(RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp))
                .background(MaterialTheme.colorScheme.background),
        ) {
            content()
            // The grip: drag down to close.
            Box(
                Modifier
                    .align(Alignment.TopCenter)
                    .width(120.dp)
                    .height(18.dp)
                    .pointerInput(Unit) {
                        detectVerticalDragGestures(
                            onDragEnd = {
                                if (drag.value > with(density) { 110.dp.toPx() }) onHome()
                                else scope.launch { drag.animateTo(0f) }
                            },
                            onDragCancel = { scope.launch { drag.animateTo(0f) } },
                        ) { _, dy ->
                            scope.launch { drag.snapTo((drag.value + dy).coerceAtLeast(0f)) }
                        }
                    }
                    .semantics { contentDescription = "Drag down to close" },
                contentAlignment = Alignment.Center,
            ) {
                Box(
                    Modifier
                        .width(36.dp)
                        .height(4.dp)
                        .clip(RoundedCornerShape(2.dp))
                        .background(MaterialTheme.colorScheme.onBackground.copy(alpha = 0.3f)),
                )
            }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun TabStrip(
    tabs: List<SessionTab>,
    current: SessionRef,
    working: Set<String>,
    blocked: Set<String>,
    onHome: () -> Unit,
    onOpen: (SessionTab) -> Unit,
    onClose: (SessionTab) -> Unit,
) {
    val pal = palette
    val scroll = rememberScrollState()
    val activeIndex = tabs.indexOfFirst { it.ref.key == current.key }
    // Keep the open bubble in view.
    LaunchedEffect(activeIndex, tabs.size) {
        if (activeIndex >= 0) scroll.animateScrollTo((activeIndex * 140).coerceAtMost(scroll.maxValue))
    }
    Row(
        Modifier
            .fillMaxWidth()
            .statusBarsPadding()
            .height(66.dp)
            .horizontalScroll(scroll)
            .padding(horizontal = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Surface(
            onClick = onHome,
            shape = CircleShape,
            color = MaterialTheme.colorScheme.surfaceContainerHigh,
            shadowElevation = 4.dp,
            modifier = Modifier.size(44.dp).semantics { contentDescription = "All sessions" },
        ) {
            Box(contentAlignment = Alignment.Center) {
                Icon(Icons.AutoMirrored.Outlined.List, contentDescription = null)
            }
        }
        tabs.forEach { tab ->
            val active = tab.ref.key == current.key
            val tint = Accent.forProvider(tab.provider).tint
            Box {
                val size = if (active) 50.dp else 44.dp
                Box(
                    Modifier
                        .size(size)
                        .shadow(if (active) 8.dp else 4.dp, CircleShape)
                        .clip(CircleShape)
                        .background(MaterialTheme.colorScheme.surfaceContainerHigh)
                        .background(tint.copy(alpha = if (active) 0.32f else 0.16f))
                        .border(2.dp, tint.copy(alpha = if (active) 1f else 0.55f), CircleShape)
                        .combinedClickable(
                            onClick = { if (!active) onOpen(tab) },
                            onLongClick = { onClose(tab) },
                            onLongClickLabel = "close tab",
                        )
                        .semantics {
                            contentDescription = tab.title
                            role = Role.Tab
                            selected = active
                        },
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        tabInitials(tab.title),
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.onSurface,
                    )
                }
                when {
                    tab.ref.key in blocked -> Box(
                        Modifier
                            .align(Alignment.TopEnd)
                            .size(17.dp)
                            .clip(CircleShape)
                            .background(pal.warn),
                        contentAlignment = Alignment.Center,
                    ) { Text("?", fontSize = 11.sp, fontWeight = FontWeight.Black, color = Color.Black) }
                    tab.ref.key in working -> WorkingPulse(
                        tint,
                        Modifier.align(Alignment.TopEnd),
                    )
                }
                if (active) {
                    Surface(
                        onClick = { onClose(tab) },
                        shape = CircleShape,
                        color = MaterialTheme.colorScheme.surfaceContainerHighest,
                        modifier = Modifier
                            .align(Alignment.BottomEnd)
                            .offset(x = 6.dp, y = 4.dp)
                            .size(22.dp)
                            .semantics { contentDescription = "Close tab ${tab.title}" },
                    ) {
                        Box(contentAlignment = Alignment.Center) {
                            Icon(Icons.Outlined.Close, contentDescription = null, modifier = Modifier.size(14.dp))
                        }
                    }
                }
            }
        }
    }
}

/** Two letters for a bubble: the first letters of the title's first words. */
internal fun tabInitials(title: String): String {
    val words = title.replace(Regex("[^\\p{L}\\p{N}\\s]"), " ").trim().split(Regex("\\s+"))
        .filter { it.isNotEmpty() }
    if (words.isEmpty()) return "?"
    val first = words[0].first()
    val second = if (words.size > 1) words[1].first() else words[0].getOrNull(1)
    return (first.toString() + (second?.toString() ?: "")).uppercase()
}
