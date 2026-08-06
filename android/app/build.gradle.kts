plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("org.jetbrains.kotlin.plugin.serialization")
}

android {
    namespace = "com.bb10d.remote"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.bb10d.remote"
        minSdk = 26
        targetSdk = 36
        versionCode = 2
        versionName = "1.1.0"
        resourceConfigurations += listOf("en")
    }

    signingConfigs {
        // Deterministic debug-grade key so sideloaded builds upgrade in place
        // on a device that never talks to Play.
        create("stable") {
            storeFile = file("../keystore/agentremote.jks")
            storePassword = "agentremote"
            keyAlias = "agentremote"
            keyPassword = "agentremote"
        }
    }

    buildTypes {
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
        release {
            signingConfig = signingConfigs.getByName("stable")
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlin {
        compilerOptions {
            jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources.excludes += setOf(
            "/META-INF/{AL2.0,LGPL2.1}",
            "/META-INF/*.kotlin_module",
            "DebugProbesKt.bin",
        )
    }

    lint {
        abortOnError = false
    }
}

/*
 * Every release build lands in ~/Public as AgentRemote.apk.
 *
 * That folder is also the Claude daemon's drop directory, so the phone can
 * pull its own next build from the app's "Files from host" screen. Wiring it
 * into the build rather than doing it by hand means the file is never a stale
 * copy of an older APK.
 */
val publicApk = tasks.register<Copy>("copyApkToPublic") {
    description = "Copies the release APK to ~/Public for sideloading."
    // Skip on CI / hosts without ~/Public (GitHub Actions release workflow).
    onlyIf { File(System.getProperty("user.home"), "Public").isDirectory }
    from(layout.buildDirectory.dir("outputs/apk/release")) {
        include("app-release.apk")
        rename { "AgentRemote.apk" }
    }
    into(File(System.getProperty("user.home"), "Public"))
}

tasks.matching { it.name == "assembleRelease" }.configureEach {
    finalizedBy(publicApk)
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2025.12.01")
    implementation(composeBom)
    androidTestImplementation(composeBom)

    implementation("androidx.core:core-ktx:1.17.0")
    implementation("androidx.activity:activity-compose:1.11.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.9.4")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.9.4")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.9.4")
    implementation("androidx.lifecycle:lifecycle-process:2.9.4")
    implementation("androidx.navigation:navigation-compose:2.9.8")
    implementation("androidx.datastore:datastore-preferences:1.1.7")

    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended:1.7.8")

    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.okhttp3:okhttp-sse:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.9.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")

    debugImplementation("androidx.compose.ui:ui-tooling")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.2")
}
