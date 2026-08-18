package com.bb10d.remote.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.ProvidableCompositionLocal
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/**
 * One binary, two agent identities.
 *
 * The BB10 apps baked the accent in at compile time (brand.hpp); here the
 * accent is data — it follows whichever profile's content is on screen, so a
 * Claude session is warm orange and a Grok session is icon cyan even though
 * they share every screen.
 */
enum class Accent(val tint: Color, val onTint: Color, val label: String) {
    Claude(Color(0xFFD97757), Color(0xFF20120C), "Claude"),
    Grok(Color(0xFF00D4FF), Color(0xFF04222A), "Grok"),
    Codex(Color(0xFF10A37F), Color(0xFF042018), "Codex"),
    DeepSeek(Color(0xFF4D6BFE), Color(0xFF0B1024), "DeepSeek"),
    Neutral(Color(0xFF9AA4B2), Color(0xFF11151A), "Agent"),
    ;

    companion object {
        fun forProvider(provider: String?): Accent = when (provider?.lowercase()) {
            "claude" -> Claude
            "grok" -> Grok
            "codex" -> Codex
            "deepseek", "dsh" -> DeepSeek
            else -> Neutral
        }
    }
}

/** Extra roles Material3's scheme has no slot for. */
data class RemotePalette(
    val accent: Color,
    val onAccent: Color,
    val userWell: Color,
    val liveWell: Color,
    val codeBg: Color,
    val codeBorder: Color,
    val inlineCode: Color,
    val heading: Color,
    val thought: Color,
    val toolTint: Color,
    val danger: Color,
    val ok: Color,
    val warn: Color,
    val hairline: Color,
    val dim: Color,
)

val LocalPalette: ProvidableCompositionLocal<RemotePalette> = staticCompositionLocalOf {
    darkPalette(Accent.Neutral)
}

private fun darkPalette(accent: Accent) = RemotePalette(
    accent = accent.tint,
    onAccent = accent.onTint,
    userWell = Color(0xFF1B1D22),
    liveWell = Color(0xFF14161A),
    codeBg = Color(0xFF101216),
    codeBorder = Color(0xFF23262D),
    inlineCode = when (accent) {
        Accent.Claude -> Color(0xFFE0A183)
        Accent.Codex -> Color(0xFF6EE7B7)
        Accent.DeepSeek -> Color(0xFF93A8FF)
        else -> Color(0xFF67E8F9)
    },
    heading = when (accent) {
        Accent.Claude -> Color(0xFFE9A47F)
        Accent.Codex -> Color(0xFF3DD68C)
        Accent.DeepSeek -> Color(0xFF7B93FF)
        else -> Color(0xFFB9A2F0)
    },
    thought = Color(0xFF8B93A3),
    toolTint = Color(0xFF7DD3A8),
    danger = Color(0xFFE5706B),
    ok = Color(0xFF5FC98B),
    warn = Color(0xFFE0B454),
    hairline = Color(0xFF23262D),
    dim = Color(0xFF7A8394),
)

private fun lightPalette(accent: Accent) = RemotePalette(
    accent = accent.tint,
    onAccent = accent.onTint,
    userWell = Color(0xFFEDEFF3),
    liveWell = Color(0xFFF4F6F9),
    codeBg = Color(0xFFF3F4F7),
    codeBorder = Color(0xFFDDE1E8),
    inlineCode = when (accent) {
        Accent.Claude -> Color(0xFFA9532F)
        Accent.Codex -> Color(0xFF0D7A5A)
        else -> Color(0xFF0B7C93)
    },
    heading = when (accent) {
        Accent.Claude -> Color(0xFFA9532F)
        Accent.Codex -> Color(0xFF0D7A5A)
        else -> Color(0xFF5B3FA8)
    },
    thought = Color(0xFF5C6472),
    toolTint = Color(0xFF1F7A50),
    danger = Color(0xFFB3261E),
    ok = Color(0xFF1F7A50),
    warn = Color(0xFF8A6100),
    hairline = Color(0xFFDDE1E8),
    dim = Color(0xFF5C6472),
)

private fun schemeFor(accent: Accent, dark: Boolean) = if (dark) {
    darkColorScheme(
        primary = accent.tint,
        onPrimary = accent.onTint,
        primaryContainer = accent.tint.copy(alpha = 0.20f),
        onPrimaryContainer = accent.tint,
        secondary = accent.tint.copy(alpha = 0.85f),
        background = Color(0xFF0B0B0D),
        onBackground = Color(0xFFE6E8EC),
        surface = Color(0xFF101114),
        onSurface = Color(0xFFE6E8EC),
        surfaceVariant = Color(0xFF191B20),
        onSurfaceVariant = Color(0xFFB6BDC9),
        surfaceContainer = Color(0xFF15171B),
        surfaceContainerHigh = Color(0xFF1B1D22),
        surfaceContainerHighest = Color(0xFF212429),
        outline = Color(0xFF3A3E46),
        outlineVariant = Color(0xFF23262D),
        error = Color(0xFFE5706B),
    )
} else {
    lightColorScheme(
        primary = accent.tint,
        onPrimary = Color.White,
        primaryContainer = accent.tint.copy(alpha = 0.16f),
        onPrimaryContainer = accent.onTint,
        secondary = accent.tint,
        background = Color(0xFFFAFAFC),
        onBackground = Color(0xFF15171B),
        surface = Color.White,
        onSurface = Color(0xFF15171B),
        surfaceVariant = Color(0xFFEDEFF3),
        onSurfaceVariant = Color(0xFF454B56),
        surfaceContainer = Color(0xFFF2F4F7),
        surfaceContainerHigh = Color(0xFFEDEFF3),
        surfaceContainerHighest = Color(0xFFE6E9EE),
        outline = Color(0xFFB9BFC9),
        outlineVariant = Color(0xFFDDE1E8),
        error = Color(0xFFB3261E),
    )
}

private val AppTypography = Typography().let { base ->
    base.copy(
        headlineSmall = base.headlineSmall.copy(fontWeight = FontWeight.SemiBold),
        titleMedium = base.titleMedium.copy(fontWeight = FontWeight.SemiBold),
        bodyMedium = base.bodyMedium.copy(lineHeight = 21.sp),
        labelSmall = base.labelSmall.copy(letterSpacing = 0.4.sp),
    )
}

/** Monospace style used by code blocks and the tool ticker. */
val MonoStyle = TextStyle(
    fontFamily = FontFamily.Monospace,
    fontSize = 12.5.sp,
    lineHeight = 18.sp,
)

@Composable
fun AgentRemoteTheme(
    accent: Accent = Accent.Neutral,
    dark: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    val palette = if (dark) darkPalette(accent) else lightPalette(accent)
    CompositionLocalProvider(LocalPalette provides palette) {
        MaterialTheme(
            colorScheme = schemeFor(accent, dark),
            typography = AppTypography,
            content = content,
        )
    }
}

val palette: RemotePalette
    @Composable @ReadOnlyComposable get() = LocalPalette.current
