// Root build script.
//
// Plugins are put on the root build-script classpath (instead of `plugins { ... apply false }`)
// so that the Android Gradle plugin is only resolved when an Android SDK is present
// (see settings.gradle.kts). Keeping the Kotlin and Android plugins in the same root
// classloader is what the Kotlin plugin expects in multi-module Android builds.

buildscript {
    val kotlinVersion = "2.0.21"
    val androidGradlePluginVersion = "8.7.3"
    val androidSdkAvailable =
        (gradle.extensions.extraProperties.properties["sauti.androidSdkAvailable"] as? Boolean) ?: false

    repositories {
        google()
        mavenCentral()
    }
    dependencies {
        classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:$kotlinVersion")
        classpath("org.jetbrains.kotlin:kotlin-serialization:$kotlinVersion")
        if (androidSdkAvailable) {
            classpath("com.android.tools.build:gradle:$androidGradlePluginVersion")
        }
    }
}
