package com.bb10d.remote.ui.screens

import androidx.compose.animation.core.Animatable
import androidx.compose.runtime.mutableStateOf
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.animation.core.FastOutLinearInEasing
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.activity.compose.BackHandler
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
import androidx.compose.ui.graphics.luminance
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.windowInsetsTopHeight
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Add
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
 * switch, + to go to the list for another, the open one's × (or a long
 * press on any) to close it. Dragging the grip down closes the sheet.
 */
/** Hand-over between two session sheets when switching tabs. */
object SheetMotion {
    /** Set just before another tab replaces this sheet: it does not slide in. */
    @Volatile var switching = false
}

/**
 * The session page as a sheet over the list, which stays drawn underneath
 * (the page is a window over it), with the open conversations above it as
 * bubbles, like a browser's tabs: tap one to switch, + to go to the list for
 * another, the open one's × (or a long press on any) to close it.
 *
 * The sheet slides up when it opens and down when it goes (Back, +, closing
 * the last tab, or dragging its grip), the bubbles riding with it, and the
 * dark overlay on the list follows its position.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
internal fun SessionSheetFrame(
    tabs: List<SessionTab>,
    current: SessionRef,
    working: Set<String>,
    blocked: Set<String>,
    /** Each daemon's connection, for the bubbles' live dot. */
    conn: Map<String, com.bb10d.remote.data.ConnState>,
    onHome: () -> Unit,
    onOpen: (SessionTab) -> Unit,
    /** Close a tab; true when the sheet should go (no tab left to show). */
    onClose: (SessionTab) -> Boolean,
    content: @Composable BoxScope.() -> Unit,
) {
    val density = LocalDensity.current
    val scope = rememberCoroutineScope()
    BoxWithConstraints(Modifier.fillMaxSize()) {
        val height = with(density) { maxHeight.toPx() }.coerceAtLeast(1f)
        // How far down the sheet is: the screen's height when hidden, 0 up.
        val offset = remember { Animatable(if (SheetMotion.switching) 0f else height) }
        var leaving by remember { mutableStateOf(false) }
        LaunchedEffect(Unit) {
            SheetMotion.switching = false
            offset.animateTo(0f, tween(320, easing = FastOutSlowInEasing))
        }
        fun dismiss() {
            if (leaving) return
            leaving = true
            scope.launch {
                offset.animateTo(height, tween(260, easing = FastOutLinearInEasing))
                onHome()
            }
        }
        BackHandler { dismiss() }
        val open = (1f - offset.value / height).coerceIn(0f, 1f)
        Box(
            Modifier
                .fillMaxSize()
                // The list shows through, dimmed as much as the sheet is up.
                .background(Color.Black.copy(alpha = 0.6f * open)),
        ) {
            // The sheet reaches the top of the screen; the bubbles float
            // over its top, and ride with it.
            Box(Modifier.fillMaxSize().offset { IntOffset(0, offset.value.roundToInt()) }) {
                Box(
                    Modifier
                        .fillMaxSize()
                        .shadow(18.dp, RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp))
                        .clip(RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp))
                        .background(MaterialTheme.colorScheme.background),
                ) {
                    content()
                    // The grip: drag down to close.
                    Box(
                        Modifier
                            .align(Alignment.TopCenter)
                            // Under the floating bubbles, over the session bar.
                            .statusBarsPadding()
                            .padding(top = TAB_STRIP_HEIGHT - 6.dp)
                            .width(120.dp)
                            .height(18.dp)
                            .pointerInput(Unit) {
                                detectVerticalDragGestures(
                                    onDragEnd = {
                                        if (offset.value > with(density) { 110.dp.toPx() }) dismiss()
                                        else scope.launch { offset.animateTo(0f) }
                                    },
                                    onDragCancel = { scope.launch { offset.animateTo(0f) } },
                                ) { _, dy ->
                                    if (!leaving) scope.launch {
                                        offset.snapTo((offset.value + dy).coerceAtLeast(0f))
                                    }
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
                // The system status bar over the sheet's top: translucent, so
                // the conversation shows through it as it scrolls under.
                StatusBarScrim()
                TabStrip(tabs, current, working, blocked, conn, ::dismiss, onOpen) { tab ->
                    if (onClose(tab)) dismiss()
                }
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
    conn: Map<String, com.bb10d.remote.data.ConnState>,
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
            .height(TAB_STRIP_HEIGHT)
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
            // +, like a browser's new tab: it goes to the list, where the
            // next conversation is picked or begun.
            modifier = Modifier.size(44.dp).semantics { contentDescription = "Open another session" },
        ) {
            Box(contentAlignment = Alignment.Center) {
                Icon(Icons.Outlined.Add, contentDescription = null)
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
                // The live status, as a dot on the bubble: green while its
                // daemon is live, breathing while the agent works, "?" when it
                // waits on you, grey while the daemon is out of reach. It
                // replaces the live strip under the session bar.
                val live = conn[tab.profileId] == com.bb10d.remote.data.ConnState.LIVE
                when {
                    tab.ref.key in blocked -> Box(
                        Modifier
                            .align(Alignment.TopEnd)
                            .size(17.dp)
                            .clip(CircleShape)
                            .background(pal.warn),
                        contentAlignment = Alignment.Center,
                    ) { Text("?", fontSize = 11.sp, fontWeight = FontWeight.Black, color = Color.Black) }
                    !live -> Box(
                        Modifier
                            .align(Alignment.TopEnd)
                            .size(12.dp)
                            .clip(CircleShape)
                            .background(pal.dim),
                    )
                    tab.ref.key in working -> WorkingPulse(
                        pal.ok,
                        Modifier.align(Alignment.TopEnd),
                        size = 12,
                    )
                    else -> Box(
                        Modifier
                            .align(Alignment.TopEnd)
                            .size(12.dp)
                            .clip(CircleShape)
                            .background(pal.ok),
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

/** Height of the floating bubbles over the sheet's top (below the status bar). */
internal val TAB_STRIP_HEIGHT = 62.dp

/**
 * A translucent band the height of the system status bar, so the page can
 * run to the top of the screen under it and still keep the clock and icons
 * readable. It also sets the icons light or dark for the page behind them.
 */
@Composable
internal fun StatusBarScrim() {
    val bg = MaterialTheme.colorScheme.background
    val view = androidx.compose.ui.platform.LocalView.current
    val light = bg.luminance() > 0.5f
    androidx.compose.runtime.SideEffect {
        val window = (view.parent as? androidx.compose.ui.window.DialogWindowProvider)?.window
            ?: (view.context as? android.app.Activity)?.window
        if (window != null) {
            androidx.core.view.WindowCompat.getInsetsController(window, view)
                .isAppearanceLightStatusBars = light
        }
    }
    Box(
        Modifier
            .fillMaxWidth()
            .windowInsetsTopHeight(androidx.compose.foundation.layout.WindowInsets.statusBars)
            .background(bg.copy(alpha = 0.72f)),
    )
}
