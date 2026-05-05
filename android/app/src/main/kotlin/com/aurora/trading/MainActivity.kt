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

class MainActivity : AppCompatActivity() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        startAuroraHeadless()
        setupWebView()
    }

    private fun startAuroraHeadless() {
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
        }
        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                spinner.visibility = View.GONE
                webView.visibility = View.VISIBLE
            }
        }
        webView.visibility = View.INVISIBLE

        // uvicorn 기동 대기 (3초) 후 UI 로드
        CoroutineScope(Dispatchers.Main).launch {
            delay(3000)
            webView.loadUrl("http://127.0.0.1:8765")
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        scope.cancel()
    }
}
