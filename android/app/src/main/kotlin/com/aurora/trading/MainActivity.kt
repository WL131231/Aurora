package com.aurora.trading

import android.os.Bundle
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.ProgressBar
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.*
import java.net.HttpURLConnection
import java.net.URL

class MainActivity : AppCompatActivity() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    // 더블-스타트 방지 — Activity recreate 시 Python 서버 중복 기동 차단
    private var serverLaunched = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        startAuroraHeadless()
        setupWebView()
    }

    private fun startAuroraHeadless() {
        if (serverLaunched) return
        serverLaunched = true

        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(this))
        }
        scope.launch {
            try {
                val py = Python.getInstance()
                val bridge = py.getModule("aurora_bridge")
                bridge.callAttr("start")
            } catch (e: Exception) {
                e.printStackTrace()
            }
        }
    }

    private fun setupWebView() {
        val webView = findViewById<WebView>(R.id.webView)
        val spinner = findViewById<ProgressBar>(R.id.loadingSpinner)

        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            cacheMode = WebSettings.LOAD_NO_CACHE
            // file:///android_asset/ 에서 fetch("http://127.0.0.1:8765") 허용
            // Why: UI 는 assets 번들, API 는 localhost uvicorn 으로 분리된 구조.
            @Suppress("DEPRECATION")
            allowUniversalAccessFromFileURLs = true
        }
        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                spinner.visibility = View.GONE
                webView.visibility = View.VISIBLE
            }
        }
        // JS → Kotlin 브리지 — "Android" 객체로 WebView JS 에서 접근 가능
        webView.addJavascriptInterface(UpdateBridge(), "Android")
        webView.visibility = View.INVISIBLE

        // uvicorn 준비될 때까지 1초 간격 폴링 (최대 90초) 후 WebView 로드
        // Why: 콜드 스타트 시 Python 인터프리터 + ccxt/pandas import 에 10~30초 소요.
        //      고정 딜레이 대신 /health 응답 확인으로 정확한 시점에 로드.
        scope.launch {
            // 서버 준비 전에도 HTML 은 assets 에서 즉시 로드 가능하지만,
            // JS 의 첫 API 호출이 실패하지 않도록 서버 준비 후 로드.
            pollUntilReady(timeoutMs = 90_000L, intervalMs = 1_000L)
            withContext(Dispatchers.Main) {
                webView.loadUrl("file:///android_asset/ui/index.html")
            }
        }
    }

    /** /health 엔드포인트가 200 응답할 때까지 폴링. 타임아웃 초과 시 false 반환. */
    private suspend fun pollUntilReady(timeoutMs: Long, intervalMs: Long): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (isServerReady()) return true
            delay(intervalMs)
        }
        return false
    }

    private fun isServerReady(): Boolean {
        return try {
            val conn = URL("http://127.0.0.1:8765/health").openConnection() as HttpURLConnection
            conn.connectTimeout = 500
            conn.readTimeout = 500
            conn.requestMethod = "GET"
            val code = conn.responseCode
            conn.disconnect()
            code == 200
        } catch (_: Exception) {
            false
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        scope.cancel()
    }

    /** JS → Kotlin APK 설치 브리지 — app.js 의 window.Android.installApk() 수신. */
    inner class UpdateBridge {
        @android.webkit.JavascriptInterface
        fun installApk(apkPath: String) {
            runOnUiThread {
                val apkFile = java.io.File(apkPath)
                if (!apkFile.exists()) return@runOnUiThread
                val uri = androidx.core.content.FileProvider.getUriForFile(
                    this@MainActivity,
                    "${packageName}.fileprovider",
                    apkFile,
                )
                val intent = android.content.Intent(android.content.Intent.ACTION_VIEW).apply {
                    setDataAndType(uri, "application/vnd.android.package-archive")
                    addFlags(android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
                }
                try {
                    startActivity(intent)
                } catch (e: Exception) {
                    e.printStackTrace()
                }
            }
        }
    }
}
