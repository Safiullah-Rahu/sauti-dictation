package com.sauti.core

import com.sauti.core.api.ApiClient
import com.sauti.core.api.ApiException
import com.sauti.core.api.NetworkProblem
import com.sauti.core.api.ServerUrl
import com.sauti.core.protocol.SettingsPatch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.util.concurrent.TimeUnit

class ApiClientTest {

    private lateinit var server: MockWebServer
    private val http = OkHttpClient.Builder().connectTimeout(2, TimeUnit.SECONDS).readTimeout(5, TimeUnit.SECONDS).build()

    private val profileJson = """
        {"version":7,"user":{"id":3,"username":"amina","display_name":"Amina Yusuf","role":"user"},
         "settings":{"input_lang":"auto","output_lang":"en","mode":"clean","style":"neutral","history_enabled":false,"retention_days":30},
         "dictionary":[{"id":5,"term":"Hargeisa","aliases":["hargaysa"]}],
         "org_dictionary":[{"id":1,"term":"Proxmox","aliases":[]}],
         "languages":["en","so","ar","sw"],"modes":["verbatim","clean","translate"],"styles":["neutral","formal","casual","message"]}
    """.trimIndent()

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun api(token: String? = null) = ApiClient(server.url("/"), http, token)

    @Test
    fun claimPairing_postsCodeDeviceNameAndPlatform_withoutAuth() {
        server.enqueue(
            MockResponse().setResponseCode(201).setBody(
                """{"device_id":12,"device_token":"sdt_abc","user":{"id":3,"username":"amina","display_name":"Amina Yusuf","role":"user"}}""",
            ),
        )
        val reg = api().claimPairing("483920", "Pixel 8")
        assertEquals(12L, reg.deviceId)
        assertEquals("sdt_abc", reg.deviceToken)
        assertEquals("Amina Yusuf", reg.user.displayName)
        val req = server.takeRequest()
        assertEquals("POST", req.method)
        assertEquals("/v1/pairing/claim", req.path)
        assertNull(req.getHeader("Authorization"))
        assertEquals(
            Json.parseToJsonElement("""{"code":"483920","device_name":"Pixel 8","platform":"android"}"""),
            Json.parseToJsonElement(req.body.readUtf8()),
        )
    }

    @Test
    fun claimPairing_invalidCode_isAnApiExceptionWithTheServerCode() {
        server.enqueue(MockResponse().setResponseCode(400).setBody("""{"error":{"code":"invalid_code","message":"Wrong or expired code."}}"""))
        try {
            api().claimPairing("000000", "Pixel 8")
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(400, e.httpStatus)
            assertEquals("invalid_code", e.code)
            assertEquals("Wrong or expired code.", e.message)
        }
    }

    @Test
    fun claimPairing_rateLimited() {
        server.enqueue(MockResponse().setResponseCode(429).setBody("""{"error":{"code":"rate_limited","message":"Too many attempts."}}"""))
        try {
            api().claimPairing("111111", "Pixel 8")
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(429, e.httpStatus)
            assertEquals("rate_limited", e.code)
        }
    }

    @Test
    fun getProfile_sendsBearerToken() {
        server.enqueue(MockResponse().setBody(profileJson))
        val p = api("sdt_abc").getProfile()
        assertEquals(7L, p.version)
        assertEquals("Hargeisa", p.dictionary.single().term)
        assertEquals("clean", p.settings.mode)
        val req = server.takeRequest()
        assertEquals("GET", req.method)
        assertEquals("/v1/me/profile", req.path)
        assertEquals("Bearer sdt_abc", req.getHeader("Authorization"))
    }

    @Test
    fun patchSettings_sendsOnlyChangedFields_andReturnsTheProfile() {
        server.enqueue(MockResponse().setBody(profileJson))
        api("sdt_abc").patchSettings(SettingsPatch(mode = "translate", outputLang = "so", style = "formal"))
        val req = server.takeRequest()
        assertEquals("PATCH", req.method)
        assertEquals("/v1/me/settings", req.path)
        assertEquals(
            Json.parseToJsonElement("""{"output_lang":"so","mode":"translate","style":"formal"}""").jsonObject,
            Json.parseToJsonElement(req.body.readUtf8()).jsonObject,
        )
    }

    @Test
    fun unauthorized_isFlagged() {
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"error":{"code":"unauthorized","message":"Token revoked."}}"""))
        try {
            api("sdt_old").getProfile()
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertTrue(e.isUnauthorized)
        }
    }

    @Test
    fun health_andPathPrefix() {
        server.enqueue(MockResponse().setBody("""{"status":"degraded","version":"0.1.0"}"""))
        val prefixed = ApiClient(server.url("/sauti"), http)
        val h = prefixed.health()
        assertEquals("degraded", h.status)
        assertEquals("0.1.0", h.version)
        assertEquals("/sauti/v1/health", server.takeRequest().path)
    }

    @Test
    fun revokeDevice_deletes() {
        server.enqueue(MockResponse().setResponseCode(204))
        api("sdt_abc").revokeDevice(12)
        val req = server.takeRequest()
        assertEquals("DELETE", req.method)
        assertEquals("/v1/me/devices/12", req.path)
    }

    @Test
    fun nonJsonErrorBody_stillGivesTheStatus() {
        server.enqueue(MockResponse().setResponseCode(502).setBody("<html>Bad gateway</html>"))
        try {
            api().health()
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(502, e.httpStatus)
            assertNull(e.code)
        }
    }

    @Test
    fun connectionRefused_isClassifiedAsUnreachable() {
        val port = server.port
        server.shutdown()
        // Windows retries refused loopback connects for ~2 s, so allow more than that.
        val patient = http.newBuilder().connectTimeout(15, TimeUnit.SECONDS).build()
        try {
            ApiClient("http://127.0.0.1:$port/".toHttpUrl(), patient).health()
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(NetworkProblem.UNREACHABLE, e.network)
        }
        server = MockWebServer().also { it.start() } // for tearDown
    }

    @Test
    fun serverUrl_parsing() {
        fun ok(input: String, debug: Boolean = false) =
            (ServerUrl.parse(input, debug) as ServerUrl.Result.Ok).url.toString()
        assertEquals("https://dictate.example.lan/", ok("dictate.example.lan"))
        assertEquals("https://dictate.example.lan/", ok("  https://dictate.example.lan/  "))
        assertEquals("https://example.org/sauti", ok("https://example.org/sauti/?x=1#y"))
        assertEquals("http://10.0.2.2:8700/", ok("http://10.0.2.2:8700", debug = true))
        assertEquals(
            ServerUrl.Result.Invalid(ServerUrl.Reason.CLEARTEXT_NOT_ALLOWED),
            ServerUrl.parse("http://10.0.2.2:8700", allowCleartext = false),
        )
        assertEquals(ServerUrl.Result.Invalid(ServerUrl.Reason.UNSUPPORTED_SCHEME), ServerUrl.parse("ftp://x", true))
        assertEquals(ServerUrl.Result.Invalid(ServerUrl.Reason.EMPTY), ServerUrl.parse("   ", true))
        assertEquals(
            "https://example.org/sauti/v1/dictate",
            ServerUrl.resolve("https://example.org/sauti".toHttpUrl(), "v1/dictate").toString(),
        )
        assertEquals("https://x.lan/v1/dictate", ServerUrl.resolve("https://x.lan/".toHttpUrl(), "/v1/dictate").toString())
    }
}
