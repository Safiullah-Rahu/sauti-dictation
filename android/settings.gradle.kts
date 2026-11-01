// Sauti Android: a pure-Kotlin protocol/client library (:core) and the keyboard app (:app).
//
// :app is only included when an Android SDK is available (sdk.dir in local.properties, or
// ANDROID_HOME / ANDROID_SDK_ROOT pointing at a directory). Without an SDK, `gradlew :core:test`
// still works, which is how the protocol client is developed and tested on machines that do
// not have Android tooling installed. CI (GitHub Actions) always has an SDK and builds both.

pluginManagement {
    repositories {
        google()
        mavenCentral()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "sauti-android"

include(":core")

fun findAndroidSdk(): String? {
    val localProperties = java.io.File(rootDir, "local.properties")
    if (localProperties.isFile) {
        val props = java.util.Properties()
        localProperties.inputStream().use { props.load(it) }
        props.getProperty("sdk.dir")?.takeIf { it.isNotBlank() }?.let { return it }
    }
    return System.getenv("ANDROID_HOME")?.takeIf { it.isNotBlank() }
        ?: System.getenv("ANDROID_SDK_ROOT")?.takeIf { it.isNotBlank() }
}

val androidSdk = findAndroidSdk()?.takeIf { java.io.File(it).isDirectory }
// Shared with the root build script so the Android Gradle plugin is only put on the
// classpath (and downloaded) when the app module is actually part of the build.
gradle.extensions.extraProperties["sauti.androidSdkAvailable"] = androidSdk != null

if (androidSdk != null) {
    include(":app")
} else {
    logger.lifecycle("Sauti: no Android SDK found (sdk.dir / ANDROID_HOME / ANDROID_SDK_ROOT); building :core only.")
}
