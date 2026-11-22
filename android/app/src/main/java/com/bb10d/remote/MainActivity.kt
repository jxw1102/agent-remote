package com.bb10d.remote

import android.Manifest
import android.content.Intent
import android.view.WindowManager
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.window.DialogProperties
import androidx.compose.ui.window.DialogWindowProvider
import androidx.navigation.compose.dialog
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

private const val TRANSCRIPT_ROUTE =
    "${Routes.TRANSCRIPT}/{profileId}/{sessionId}?job={job}&provider={provider}"

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

    LaunchedEffect(openOnStart) {
        val ref = openOnStart?.let { SessionRef.parse(it) } ?: return@LaunchedEffect
        onOpenConsumed()
        nav.navigate(Routes.transcript(ref.profileId, ref.sessionId))
    }

    NavHost(navController = nav, startDestination = Routes.SESSIONS) {
        composable(Routes.SESSIONS) {
            SessionsScreen(
                vm = viewModel(factory = factory),
                onOpen = { ref, provider ->
                    nav.navigate(
                        Routes.transcript(ref.profileId, ref.sessionId, provider = provider),
                    )
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
                    nav.navigate(
                        Routes.transcript(profileId, "", jobId, provider = provider),
                    )
                },
            )
        }

        // A window over the list rather than a screen in its place: the
        // list stays drawn (and live) under the session sheet, as the page a
        // modal sheet covers. TranscriptScreen draws its own scrim and runs
        // its own slide in and out.
        dialog(
            route = TRANSCRIPT_ROUTE,
            arguments = listOf(
                navArgument("profileId") { type = NavType.StringType },
                navArgument("sessionId") { type = NavType.StringType },
                navArgument("job") { defaultValue = "" },
                navArgument("provider") { defaultValue = "" },
            ),
            dialogProperties = DialogProperties(
                usePlatformDefaultWidth = false,
                decorFitsSystemWindows = false,
                // Back is handled inside, so the sheet can slide away first.
                dismissOnBackPress = false,
                dismissOnClickOutside = false,
            ),
        ) { entry ->
            FullScreenDialogWindow()
            val profileId = entry.arguments?.getString("profileId").orEmpty()
            val rawSession = entry.arguments?.getString("sessionId").orEmpty()
            val sessionId = if (rawSession == Routes.NONE) "" else rawSession
            val jobId = entry.arguments?.getString("job").orEmpty()
            val provider = entry.arguments?.getString("provider").orEmpty()
            val ref = SessionRef(profileId, sessionId)
            val transcriptFactory = remember(ref.key, jobId, provider) {
                viewModelFactory {
                    initializer { TranscriptViewModel(repo, ref, jobId, provider) }
                }
            }
            // Theme is applied inside TranscriptScreen from the session harness
            // (nav provider is the initial hint for multi hosts).
            TranscriptScreen(
                vm = viewModel(key = ref.key + jobId + provider, factory = transcriptFactory),
                onBack = { nav.popBackStack() },
                onOpenTab = { tab ->
                    // Another tab: this page is replaced, so Back still goes
                    // to the list rather than through every tab visited. The
                    // sheet is already up: the next one does not slide in.
                    SheetMotion.switching = true
                    nav.navigate(Routes.transcript(tab.profileId, tab.sessionId, provider = tab.provider)) {
                        popUpTo(entry.destination.id) { inclusive = true }
                    }
                },
            )
        }
    }
}

private fun openUrl(context: android.content.Context, url: String) {
    runCatching {
        context.startActivity(
            Intent(Intent.ACTION_VIEW, url.toUri()).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
    }
}

/**
 * The session page's dialog window: full screen, no system dim (the page
 * draws a scrim that follows the sheet), no window animation (the sheet
 * slides itself), and resized for the keyboard like the activity is.
 */
@Composable
private fun FullScreenDialogWindow() {
    val window = (LocalView.current.parent as? DialogWindowProvider)?.window ?: return
    androidx.compose.runtime.SideEffect {
        window.setDimAmount(0f)
        // The page draws under the status bar itself (with its own
        // translucent band); the bar is not filled in by the window.
        @Suppress("DEPRECATION")
        window.statusBarColor = android.graphics.Color.TRANSPARENT
        @Suppress("DEPRECATION")
        window.navigationBarColor = android.graphics.Color.TRANSPARENT
        window.clearFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND)
        window.setWindowAnimations(0)
        @Suppress("DEPRECATION")
        window.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
        window.setLayout(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.MATCH_PARENT,
        )
    }
}
