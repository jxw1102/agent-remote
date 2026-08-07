package com.bb10d.remote.ui.components

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathFillType
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.path
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.bb10d.remote.ui.theme.Accent
import com.bb10d.remote.ui.theme.palette

/**
 * Host profile name (text) + harness brand mark (logo).
 *
 * In a merged list the host name answers "which machine"; the logo answers
 * "which CLI". Compact mode is logo-only (profile lists of harnesses).
 */
@Composable
fun ProviderChip(
    provider: String,
    profileName: String,
    modifier: Modifier = Modifier,
    compact: Boolean = false,
) {
    val accent = Accent.forProvider(provider)
    val logo = remember(provider) { ProviderLogos.forProvider(provider) }
    Row(
        modifier = modifier,
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        if (!compact && profileName.isNotBlank()) {
            Text(
                text = profileName,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        Box(
            modifier = Modifier
                .size(if (compact) 20.dp else 22.dp)
                .clip(RoundedCornerShape(6.dp))
                .background(accent.tint.copy(alpha = 0.16f))
                .border(1.dp, accent.tint.copy(alpha = 0.4f), RoundedCornerShape(6.dp)),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                imageVector = logo,
                contentDescription = accent.label,
                tint = accent.tint,
                modifier = Modifier.size(if (compact) 12.dp else 14.dp),
            )
        }
    }
}

/** Simplified brand silhouettes for Claude / Grok / Codex (UI chrome only). */
object ProviderLogos {
    fun forProvider(provider: String?): ImageVector = when (provider?.lowercase()) {
        "claude" -> Claude
        "grok" -> Grok
        "codex" -> Codex
        else -> Neutral
    }

    val Claude: ImageVector by lazy {
        ImageVector.Builder(
            name = "provider.claude",
            defaultWidth = 24.dp,
            defaultHeight = 24.dp,
            viewportWidth = 24f,
            viewportHeight = 24f,
        ).apply {
            // Starburst / asterisk.
            path(fill = SolidColor(Color.Black)) {
                moveTo(12f, 2.2f)
                lineTo(13.55f, 8.25f)
                lineTo(19.5f, 8.05f)
                lineTo(14.6f, 11.55f)
                lineTo(16.55f, 17.35f)
                lineTo(12f, 14.2f)
                lineTo(7.45f, 17.35f)
                lineTo(9.4f, 11.55f)
                lineTo(4.5f, 8.05f)
                lineTo(10.45f, 8.25f)
                close()
            }
        }.build()
    }

    val Grok: ImageVector by lazy {
        ImageVector.Builder(
            name = "provider.grok",
            defaultWidth = 24.dp,
            defaultHeight = 24.dp,
            viewportWidth = 24f,
            viewportHeight = 24f,
        ).apply {
            // Geometric X.
            path(fill = SolidColor(Color.Black)) {
                moveTo(4.5f, 4.5f)
                horizontalLineTo(8.7f)
                lineTo(12f, 9.2f)
                lineTo(15.3f, 4.5f)
                horizontalLineTo(19.5f)
                lineTo(13.8f, 12f)
                lineTo(19.9f, 19.5f)
                horizontalLineTo(15.7f)
                lineTo(12f, 14.8f)
                lineTo(8.3f, 19.5f)
                horizontalLineTo(4.1f)
                lineTo(10.2f, 12f)
                close()
            }
        }.build()
    }

    val Codex: ImageVector by lazy {
        ImageVector.Builder(
            name = "provider.codex",
            defaultWidth = 24.dp,
            defaultHeight = 24.dp,
            viewportWidth = 24f,
            viewportHeight = 24f,
        ).apply {
            // Six-petal blossom + center.
            path(
                fill = SolidColor(Color.Black),
                pathFillType = PathFillType.EvenOdd,
            ) {
                moveTo(12f, 2.5f)
                cubicTo(13.4f, 4.1f, 13.7f, 6f, 13.2f, 7.7f)
                cubicTo(14.9f, 7.2f, 16.8f, 7.5f, 18.4f, 8.9f)
                cubicTo(16.8f, 10.3f, 14.9f, 10.6f, 13.2f, 10.1f)
                cubicTo(13.7f, 11.8f, 13.4f, 13.7f, 12f, 15.3f)
                cubicTo(10.6f, 13.7f, 10.3f, 11.8f, 10.8f, 10.1f)
                cubicTo(9.1f, 10.6f, 7.2f, 10.3f, 5.6f, 8.9f)
                cubicTo(7.2f, 7.5f, 9.1f, 7.2f, 10.8f, 7.7f)
                cubicTo(10.3f, 6f, 10.6f, 4.1f, 12f, 2.5f)
                close()
                moveTo(12f, 9.5f)
                cubicTo(13.38f, 9.5f, 14.5f, 10.62f, 14.5f, 12f)
                cubicTo(14.5f, 13.38f, 13.38f, 14.5f, 12f, 14.5f)
                cubicTo(10.62f, 14.5f, 9.5f, 13.38f, 9.5f, 12f)
                cubicTo(9.5f, 10.62f, 10.62f, 9.5f, 12f, 9.5f)
                close()
            }
        }.build()
    }

    val Neutral: ImageVector by lazy {
        ImageVector.Builder(
            name = "provider.neutral",
            defaultWidth = 24.dp,
            defaultHeight = 24.dp,
            viewportWidth = 24f,
            viewportHeight = 24f,
        ).apply {
            path(fill = SolidColor(Color.Black)) {
                moveTo(12f, 5f)
                cubicTo(15.87f, 5f, 19f, 8.13f, 19f, 12f)
                cubicTo(19f, 15.87f, 15.87f, 19f, 12f, 19f)
                cubicTo(8.13f, 19f, 5f, 15.87f, 5f, 12f)
                cubicTo(5f, 8.13f, 8.13f, 5f, 12f, 5f)
                close()
            }
        }.build()
    }
}

/** Slowly breathing dot: something is running on the other end. */
@Composable
fun WorkingPulse(color: Color, modifier: Modifier = Modifier, size: Int = 8) {
    val transition = rememberInfiniteTransition(label = "pulse")
    val alpha by transition.animateFloat(
        initialValue = 0.35f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(900), RepeatMode.Reverse),
        label = "alpha",
    )
    Box(
        modifier
            .size(size.dp)
            .alpha(alpha)
            .clip(CircleShape)
            .background(color),
    )
}

@Composable
fun ErrorBanner(
    text: String,
    modifier: Modifier = Modifier,
    action: (@Composable () -> Unit)? = null,
) {
    val pal = palette
    Row(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(10.dp))
            .background(pal.danger.copy(alpha = 0.12f))
            .border(1.dp, pal.danger.copy(alpha = 0.35f), RoundedCornerShape(10.dp))
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            Icons.Outlined.ErrorOutline,
            contentDescription = null,
            tint = pal.danger,
            modifier = Modifier.size(18.dp),
        )
        Spacer(Modifier.width(10.dp))
        Text(
            text = text,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.weight(1f),
        )
        if (action != null) {
            Spacer(Modifier.width(8.dp))
            action()
        }
    }
}

@Composable
fun EmptyState(
    title: String,
    body: String,
    modifier: Modifier = Modifier,
    action: (@Composable () -> Unit)? = null,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 32.dp, vertical = 48.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            text = title,
            style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.onSurface,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            text = body,
            style = MaterialTheme.typography.bodyMedium,
            color = palette.dim,
            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
        )
        if (action != null) {
            Spacer(Modifier.height(20.dp))
            action()
        }
    }
}

@Composable
fun SectionLabel(text: String, modifier: Modifier = Modifier) {
    Text(
        text = text.uppercase(),
        style = MaterialTheme.typography.labelSmall.copy(
            fontWeight = FontWeight.SemiBold,
            letterSpacing = 1.sp,
        ),
        color = palette.dim,
        modifier = modifier.padding(horizontal = 16.dp, vertical = 8.dp),
    )
}

@Composable
fun Hairline(modifier: Modifier = Modifier, inset: Int = 0) {
    Box(
        modifier
            .fillMaxWidth()
            .padding(start = inset.dp)
            .height(1.dp)
            .background(palette.hairline),
    )
}

/** Small monospace pill for models, branches, modes. */
@Composable
fun MetaPill(text: String, modifier: Modifier = Modifier, tint: Color? = null) {
    val pal = palette
    val color = tint ?: pal.dim
    Text(
        text = text,
        style = MaterialTheme.typography.labelSmall,
        color = color,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
        modifier = modifier
            .clip(RoundedCornerShape(5.dp))
            .background(color.copy(alpha = 0.10f))
            .padding(horizontal = 5.dp, vertical = 1.dp),
    )
}

val ScreenPadding = PaddingValues(horizontal = 16.dp)
