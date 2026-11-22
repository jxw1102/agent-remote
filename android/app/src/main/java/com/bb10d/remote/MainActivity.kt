package com.bb10d.remote

import android.Manifest
import android.content.Intent
import androidx.compose.ui.Modifier
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.key
import androidx.compose.runtime.setValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.Box
import com.bb10d.remote.ui.screens.SheetMotion
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import androidx.core.net.toUri
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.bb10d.remote.data.SessionRef
import com.bb10d.remote.service.JobWatchService
import com.bb10d.remote.ui.screens.DropScreen
import com.bb10d.remote.ui.screens.DropViewModel
import com.bb10d.remote.ui.screens.NewSessionScreen
import com.bb10d.remote.ui.screens.NewSessionViewModel
import com.bb10d.remote.ui.screens.ProfileEditorScreen
import com.bb10d.remote.ui.screens.ProfilesScreen
import com.bb10d.remote.ui.screens.ProfilesViewModel
import com.bb10d.remote.ui.screens.SessionsScreen
import com.bb10d.remote.ui.screens.SessionsViewModel
import com.bb10d.remote.ui.screens.SettingsScreen
import com.bb10d.remote.ui.screens.SettingsViewModel
import com.bb10d.remote.ui.screens.TranscriptScreen
import com.bb10d.remote.ui.screens.TranscriptViewModel
import com.bb10d.remote.ui.screens.UsageScreen
import com.bb10d.remote.ui.screens.UsageViewModel
import com.bb10d.remote.ui.theme.Accent
import com.bb10d.remote.ui.theme.AgentRemoteTheme
import kotlinx.coroutines.flow.MutableStateFlow
// Accent/AgentRemoteTheme: app shell uses Neutral; TranscriptScreen re-themes per harness.

class MainActivity : ComponentActivity() {

    /** Session a notification asked us to open; survives onNewIntent. */
    private val openRequest = MutableStateFlow<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val app = RemoteApp.get(this)
        openRequest.value = intent?.getStringExtra(EXTRA_OPEN_SESSION)
        setContent {
            val pendingOpen by openRequest.collectAsStateWithLifecycle()
            val settings by app.repository.settings.collectAsStateWithLifecycle()
            val dark = when (settings.theme) {
                "dark" -> true
                "light" -> false
                else -> isSystemInDarkTheme()
            }
            AgentRemoteTheme(accent = Accent.Neutral, dark = dark) {
                AppNav(
                    openOnStart = pendingOpen,
                    onOpenConsumed = { openRequest.value = null },
                    dark = dark,
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        // Only a notification tap carries a session to open. Plain relaunches
        // (the launcher icon, task switcher) must NOT recreate — that would
        // throw away whatever the user was in the middle of typing.
        intent.getStringExtra(EXTRA_OPEN_SESSION)?.let { openRequest.value = it }
    }

    companion object {
        const val EXTRA_OPEN_SESSION = "open_session"
    }
}

private object Routes {
    const val SESSIONS = "sessions"
    const val PROFILES = "profiles"
    const val PROFILE_EDIT = "profile"
    const val SETTINGS = "settings"
    const val USAGE = "usage"
    const val DROP = "drop"
    const val NEW_SESSION = "new-session"
    const val TRANSCRIPT = "transcript"

    fun transcript(
        profileId: String,
        sessionId: String,
        jobId: String = "",
        provider: String = "",
    ) =
        "$TRANSCRIPT/$profileId/${sessionId.ifEmpty { NONE }}" +
            "?job=${jobId}&provider=${provider}"

    const val NONE = "-"
}

@Composable
private fun AppNav(openOnStart: String?, onOpenConsumed: () -> Unit, dark: Boolean) {
    val context = LocalContext.current
    val app = RemoteApp.get(context)
    val repo = app.repository
    val nav = rememberNavController()

    val factory = remember(repo) {
        viewModelFactory {
            initializer { SessionsViewModel(repo) }
            initializer { ProfilesViewModel(repo) }
            initializer { NewSessionViewModel(repo) }
            initializer { SettingsViewModel(repo) }
            initializer { UsageViewModel(repo) }
            initializer { DropViewModel(repo) }
        }
    }

    // Ask once, at the point it becomes useful: the app is only worth
    // notifying from if there is a daemon to watch.
    val notifications = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { }
    val profiles by repo.profiles.collectAsStateWithLifecycle()
    LaunchedEffect(profiles.enabled.isNotEmpty()) {
        if (profiles.enabled.isNotEmpty() && Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            notifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    // Keep the watch service alive exactly while something is running.
    val active by repo.active.collectAsStateWithLifecycle()
    val settings by repo.settings.collectAsStateWithLifecycle()
    LaunchedEffect(active.values.sumOf { it.size }, settings.backgroundWatch) {
        val running = active.values.sumOf { it.size } > 0
        JobWatchService.sync(context, running && settings.backgroundWatch)
    }

    var sheet by rememberSaveable(stateSaver = SheetRoute.Saver) { mutableStateOf<SheetRoute?>(null) }
    val openSheet: (SheetRoute) -> Unit = { sheet = it }

    LaunchedEffect(openOnStart) {
        val ref = openOnStart?.let { SessionRef.parse(it) } ?: return@LaunchedEffect
        onOpenConsumed()
        openSheet(SheetRoute(ref.profileId, ref.sessionId))
    }

    // The session page is drawn over the navigation host, in the activity's
    // own window: the list stays drawn (and live) under the session sheet,
    // and the page reaches the full height of the screen (a dialog window
    // could not reach under the status bar on every phone).

    Box(Modifier.fillMaxSize()) {
    NavHost(navController = nav, startDestination = Routes.SESSIONS) {
        composable(Routes.SESSIONS) {
            SessionsScreen(
                vm = viewModel(factory = factory),
                onOpen = { ref, provider ->
                    openSheet(SheetRoute(ref.profileId, ref.sessionId, provider = provider))
                },
                onNewSession = { nav.navigate(Routes.NEW_SESSION) },
                onProfiles = { nav.navigate(Routes.PROFILES) },
                onSettings = { nav.navigate(Routes.SETTINGS) },
                onDrop = { nav.navigate(Routes.DROP) },
                onUsage = { nav.navigate(Routes.USAGE) },
            )
        }

        composable(Routes.PROFILES) {
            ProfilesScreen(
                vm = viewModel(factory = factory),
                onBack = { nav.popBackStack() },
                onEdit = { id -> nav.navigate("${Routes.PROFILE_EDIT}?id=${id.orEmpty()}") },
            )
        }

        composable(
            route = "${Routes.PROFILE_EDIT}?id={id}",
            arguments = listOf(navArgument("id") { defaultValue = "" }),
        ) { entry ->
            ProfileEditorScreen(
                vm = viewModel(factory = factory),
                profileId = entry.arguments?.getString("id")?.takeIf { it.isNotEmpty() },
                onDone = { nav.popBackStack() },
            )
        }

        composable(Routes.SETTINGS) {
            SettingsScreen(
                vm = viewModel(factory = factory),
                onBack = { nav.popBackStack() },
                onProfiles = { nav.navigate(Routes.PROFILES) },
            )
        }

        composable(Routes.USAGE) {
            UsageScreen(
                vm = viewModel(factory = factory),
                onBack = { nav.popBackStack() },
                onOpenWeb = { url -> openUrl(context, url) },
            )
        }

        composable(Routes.DROP) {
            DropScreen(vm = viewModel(factory = factory), onBack = { nav.popBackStack() })
        }

        composable(Routes.NEW_SESSION) {
            NewSessionScreen(
                vm = viewModel(factory = factory),
                onBack = { nav.popBackStack() },
                onStarted = { profileId, jobId, provider ->
                    nav.popBackStack()
                    openSheet(SheetRoute(profileId, "", jobId, provider))
                },
            )
        }

    }
    sheet?.let { route ->
        key(route.key) {
            SessionSheetHost(
                repo = repo,
                route = route,
                onBack = { sheet = null },
                onOpenTab = { tab ->
                    // The sheet is already up: the next one does not slide in.
                    SheetMotion.switching = true
                    openSheet(SheetRoute(tab.profileId, tab.sessionId, provider = tab.provider))
                },
            )
        }
    }
    }
}

/** Which session the sheet shows (saved across process death). */
private data class SheetRoute(
    val profileId: String,
    val sessionId: String,
    val jobId: String = "",
    val provider: String = "",
) {
    val key: String get() = "$profileId/$sessionId/$jobId/$provider"

    companion object {
        val Saver = androidx.compose.runtime.saveable.listSaver<SheetRoute?, String>(
            save = { r -> if (r == null) emptyList() else listOf(r.profileId, r.sessionId, r.jobId, r.provider) },
            restore = { l -> if (l.size == 4) SheetRoute(l[0], l[1], l[2], l[3]) else null },
        )
    }
}

/**
 * One session page, with view models of its own that are cleared when the
 * page goes (it is not a navigation destination, so nothing else would).
 */
@Composable
private fun SessionSheetHost(
    repo: com.bb10d.remote.data.AgentRepository,
    route: SheetRoute,
    onBack: () -> Unit,
    onOpenTab: (com.bb10d.remote.data.SessionTab) -> Unit,
) {
    val owner = remember {
        object : androidx.lifecycle.ViewModelStoreOwner {
            override val viewModelStore = androidx.lifecycle.ViewModelStore()
        }
    }
    DisposableEffect(owner) { onDispose { owner.viewModelStore.clear() } }
    val ref = SessionRef(route.profileId, route.sessionId)
    val factory = remember {
        viewModelFactory {
            initializer { TranscriptViewModel(repo, ref, route.jobId, route.provider) }
        }
    }
    androidx.compose.runtime.CompositionLocalProvider(
        androidx.lifecycle.viewmodel.compose.LocalViewModelStoreOwner provides owner,
    ) {
        // Theme is applied inside TranscriptScreen from the session harness
        // (the route's provider is the initial hint for multi hosts).
        TranscriptScreen(
            vm = viewModel(factory = factory),
            onBack = onBack,
            onOpenTab = onOpenTab,
        )
    }
}

private fun openUrl(context: android.content.Context, url: String) {
    runCatching {
        context.startActivity(
            Intent(Intent.ACTION_VIEW, url.toUri()).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
    }
}
