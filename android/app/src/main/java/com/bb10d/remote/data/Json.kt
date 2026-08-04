package com.bb10d.remote.data

import kotlinx.serialization.json.Json as KxJson

/**
 * One parser for the whole app.
 *
 * `ignoreUnknownKeys` is not laziness: the daemon adds fields ahead of the
 * clients (that is how caps roll out), and `coerceInputValues` keeps a null
 * where a non-null default is declared from killing a whole transcript load.
 */
val Json: KxJson = KxJson {
    ignoreUnknownKeys = true
    isLenient = true
    coerceInputValues = true
    explicitNulls = false
    encodeDefaults = true
}
