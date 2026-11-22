package com.bb10d.remote.ui.screens

import androidx.compose.animation.core.Animatable
import androidx.compose.ui.draw.alpha
import androidx.compose.animation.core.animateFloatAsState
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
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.graphics.luminance
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.windowInsetsTopHeight
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
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
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.graphics.CompositingStrategy
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.foundation.gestures.detectTapGestures
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
import com.bb10d.remote.ui.components.stateTint
import com.bb10d.remote.ui.theme.Accent
import com.bb10d.remote.ui.theme.palette
import kotlin.math.roundToInt

/**
 * A phone, upright or sideways: the session page is a sheet over the list,
 * with the tabs, the folding bar and the floating bottom bar. (A tablet
 * keeps the plain page.)
 */
@Composable
internal fun isSheetLayout(): Boolean = LocalConfiguration.current.smallestScreenWidthDp < 600

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
 * bubbles, like a browser's tabs: tap one to switch, the back button to go
 * to the list, the open one's × (or a long press on any) to close it.
 *
 * The sheet slides up when it opens and down when it goes (Back, the back
 * button, or
 * closing the last tab), the bubbles riding with it, and the dark overlay
 * on the list follows its position.
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
        // The session page reports its floating bottom bar's height here, so
        // the tab column stands just above it.
        val bottomInset = remember { mutableStateOf(0.dp) }
        val sideInsets = WindowInsets.safeDrawing.only(WindowInsetsSides.Horizontal)
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
                // The list shows through, dimmed as much as the sheet is up,
                // but takes no touch while the sheet is over it.
                .pointerInput(Unit) { detectTapGestures { } }
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
                    CompositionLocalProvider(LocalSheetBottomInset provides bottomInset) {
                        // Sideways, the camera cutout and a side navigation
                        // bar are at the page's ends: keep clear of them.
                        Box(Modifier.fillMaxSize().windowInsetsPadding(sideInsets)) { content() }
                    }
                }
                // The system status bar over the sheet's top: translucent, so
                // the conversation shows through it as it scrolls under.
                StatusBarScrim()
                TabStrip(
                    tabs, current, working, blocked, conn, ::dismiss, onOpen,
                    onClose = { tab -> if (onClose(tab)) dismiss() },
                    // The page's floating bottom bar, as it reported it. (The
                    // column sits outside the page, so it is handed the
                    // height; reading the local here gave it the default 0,
                    // which left the bubbles standing on the bar.)
                    bottomInset = bottomInset.value,
                    modifier = Modifier.align(Alignment.BottomEnd).windowInsetsPadding(sideInsets),
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
    conn: Map<String, com.bb10d.remote.data.ConnState>,
    onHome: () -> Unit,
    onOpen: (SessionTab) -> Unit,
    onClose: (SessionTab) -> Unit,
    bottomInset: androidx.compose.ui.unit.Dp,
    modifier: Modifier = Modifier,
) {
    val pal = palette
    val scroll = rememberScrollState()
    // One bubble stands for all the tabs, the open conversation's, with a
    // count of how many are open. A tap fans the others out above it;
    // picking one or a few seconds put them away again, so they are not
    // standing over the text being read.
    var fan by remember { mutableStateOf(false) }
    LaunchedEffect(fan) {
        scroll.scrollTo(0)      // start at the foot, by the open bubble
        if (fan) {
            kotlinx.coroutines.delay(6000)
            fan = false
        }
    }
    // Translucent while not in use, so the text under the bubble shows
    // through; solid when fanned out, and while any open conversation
    // is waiting on you.
    val solid = fan || tabs.any { it.ref.key in blocked }
    val bubbleAlpha by animateFloatAsState(if (solid) 1f else 0.45f, tween(180), label = "tab-alpha")
    // A column at the end of the screen, a little above the floating bottom
    // bar, the newest tab on top. (Back to the list is in the session bar.)
    Column(
        modifier
            // The fade is on the whole column, whose own padding holds the
            // bubbles' shadows: on a bubble alone it cut its shadow off at
            // the bubble's edge.
            .alpha(bubbleAlpha)
            .navigationBarsPadding()
            .imePadding()
            // A scrolling column clips at its edge, which cut the bubbles'
            // shadows off: the room for them is inside the scroll.
            // A shadow reaches further below a bubble than above it, so the
            // foot gets the most room (the column's own offset is shortened
            // by as much, and the bubbles stay where they were).
            .padding(bottom = (bottomInset - 10.dp).coerceAtLeast(0.dp))
            // Three bubbles at most show at once (the open one and two
            // more, with the shadows' room), two on a phone held sideways,
            // where there is less height; the rest scroll.
            .heightIn(
                max = if (LocalConfiguration.current.screenHeightDp < 480)
                    50.dp + 44.dp + 12.dp + 20.dp + 32.dp
                else 50.dp + 44.dp + 44.dp + 12.dp * 2 + 20.dp + 32.dp,
            )
            // Fanned out, the column fades away upward, so a bubble scrolled
            // past its top goes out softly and the fade says there is more
            // above.
            .then(
                if (fan) Modifier
                    .graphicsLayer { compositingStrategy = CompositingStrategy.Offscreen }
                    .drawWithContent {
                        drawContent()
                        drawRect(
                            brush = Brush.verticalGradient(
                                0f to Color.Transparent,
                                34.dp.toPx() to Color.Black,
                            ),
                            blendMode = BlendMode.DstIn,
                        )
                    }
                else Modifier,
            )
            .verticalScroll(scroll, reverseScrolling = true)
            // End padding: the bubbles' centres sit over Send's (the bar's
            // 10 dp inset, its row's 8 dp, half of Send's 42 dp).
            .padding(start = 16.dp, top = 20.dp, end = 14.dp, bottom = 32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        // The open conversation's bubble stays where it is, at the foot of
        // the column; the others fan out above it, the most recently opened
        // nearest.
        val ordered = tabs.filter { it.ref.key != current.key } +
            tabs.filter { it.ref.key == current.key }
        ordered.forEach { tab ->
            val active = tab.ref.key == current.key
            if (active || fan) TabBubble(
                tab, current, working, blocked, conn,
                count = if (!fan && tabs.size > 1) tabs.size else 0,
                showClose = fan,
                onOpen = { t -> fan = false; onOpen(t) },
                onClose = onClose,
                onFan = { if (tabs.size > 1) fan = !fan },
            )
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun TabBubble(
    tab: SessionTab,
    current: SessionRef,
    working: Set<String>,
    blocked: Set<String>,
    conn: Map<String, com.bb10d.remote.data.ConnState>,
    /** How many tabs this bubble stands for (0 = no count shown). */
    count: Int,
    showClose: Boolean,
    onOpen: (SessionTab) -> Unit,
    onClose: (SessionTab) -> Unit,
    /** The open bubble was tapped: fan the others out, or put them away. */
    onFan: () -> Unit,
) {
    val pal = palette
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
                        onClick = { if (!active) onOpen(tab) else onFan() },
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
                        .background(stateTint("needs_answer")),
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
                    stateTint("working"),
                    Modifier.align(Alignment.TopEnd),
                    size = 12,
                )
                else -> Box(
                    Modifier
                        .align(Alignment.TopEnd)
                        .size(12.dp)
                        .clip(CircleShape)
                        .background(stateTint("working")),
                )
            }
            if (count > 0) Box(
                Modifier
                    .align(Alignment.TopStart)
                    .offset(x = (-6).dp, y = (-4).dp)
                    .clip(CircleShape)
                    .background(MaterialTheme.colorScheme.surfaceContainerHighest)
                    .padding(horizontal = 6.dp, vertical = 1.dp),
            ) {
                Text(count.toString(), fontSize = 11.sp, fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.onSurface)
            }
            if (active && showClose) {
                Surface(
                    onClick = { onClose(tab) },
                    shape = CircleShape,
                    color = MaterialTheme.colorScheme.surfaceContainerHighest,
                    modifier = Modifier
                        // On the side away from the next bubble's dot.
                        .align(Alignment.BottomStart)
                        .offset(x = (-6).dp, y = 4.dp)
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

/** Two letters for a bubble: the first letters of the title's first words. */
internal fun tabInitials(title: String): String {
    val words = title.replace(Regex("[^\\p{L}\\p{N}\\s]"), " ").trim().split(Regex("\\s+"))
        .filter { it.isNotEmpty() }
    if (words.isEmpty()) return "?"
    val first = words[0].first()
    val second = if (words.size > 1) words[1].first() else words[0].getOrNull(1)
    return (first.toString() + (second?.toString() ?: "")).uppercase()
}

/** The session page's floating bottom bar height, for the tab column. */
internal val LocalSheetBottomInset =
    androidx.compose.runtime.staticCompositionLocalOf { mutableStateOf(0.dp) }

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
