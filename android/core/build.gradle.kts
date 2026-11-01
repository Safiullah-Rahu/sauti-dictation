import org.gradle.api.tasks.testing.logging.TestExceptionFormat
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

// Pure Kotlin/JVM library: protocol models, REST client, WebSocket dictation client and the
// insertion ledger. No Android dependencies, so it compiles and tests without an Android SDK.
// Only APIs that exist on Android API 26+ are used, because the app module links it.

plugins {
    id("org.jetbrains.kotlin.jvm")
    id("org.jetbrains.kotlin.plugin.serialization")
    `java-library`
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    api("com.squareup.okhttp3:okhttp:4.12.0")
    api("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")

    testImplementation("junit:junit:4.13.2")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
}

val integrationEnv = listOf("SAUTI_TEST_SERVER", "SAUTI_TEST_TOKEN", "SAUTI_TEST_WAV", "SAUTI_TEST_REALTIME")

tasks.test {
    useJUnit()
    // The integration test is skipped unless these are set; declaring them as inputs makes
    // Gradle re-run the tests when they change instead of reporting UP-TO-DATE.
    integrationEnv.forEach { name ->
        inputs.property(name, providers.environmentVariable(name).orElse(""))
    }
    testLogging {
        events("passed", "skipped", "failed")
        exceptionFormat = TestExceptionFormat.FULL
        showStandardStreams = false
    }
}
