package com.argus.demo

import android.graphics.Color
import android.os.Bundle
import android.util.Log
import android.view.View
import android.widget.Button
import android.widget.CompoundButton
import android.widget.Switch
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

private const val TAG = "ArgusDemo"

/**
 * Argus Demo — a single-activity app with a home screen (counter + colour
 * swatch) and a settings screen (dark-theme toggle), used as the Argus
 * Android example. See examples/android/README.md.
 */
class MainActivity : AppCompatActivity() {

    private var counter = 0
    private var theme = "light" // "light" | "dark"
    private var screen = "home" // "home" | "settings"

    private lateinit var rootLayout: View
    private lateinit var homeScreen: View
    private lateinit var settingsScreen: View
    private lateinit var tvTitle: TextView
    private lateinit var tvCounter: TextView
    private lateinit var tvSettingsTitle: TextView
    private lateinit var swatch: View
    private lateinit var switchDark: Switch

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        rootLayout = findViewById(R.id.root_layout)
        homeScreen = findViewById(R.id.home_screen)
        settingsScreen = findViewById(R.id.settings_screen)
        tvTitle = findViewById(R.id.tvTitle)
        tvCounter = findViewById(R.id.tvCounter)
        tvSettingsTitle = findViewById(R.id.tvSettingsTitle)
        swatch = findViewById(R.id.viewSwatch)
        switchDark = findViewById(R.id.switchDark)

        val btnPlus: Button = findViewById(R.id.btnPlus)
        val btnSettings: Button = findViewById(R.id.btnSettings)
        val btnBack: Button = findViewById(R.id.btnBack)

        btnPlus.setOnClickListener { incrementCounter() }
        btnSettings.setOnClickListener { showSettings() }
        btnBack.setOnClickListener { showHome() }
        switchDark.setOnCheckedChangeListener { _: CompoundButton, checked: Boolean ->
            setTheme(if (checked) "dark" else "light")
        }

        applyTheme()
        updateCounterText()
        showHome()

        // Instrumentation is a debug/test-only HTTP listener; never shipped in release.
        if (BuildConfig.DEBUG) {
            InstrumentationServer.start()
        }
        InstrumentationServer.ready = true

        Log.i(TAG, "App ready")
    }

    @Suppress("DEPRECATION", "OVERRIDE_DEPRECATION")
    override fun onBackPressed() {
        if (screen == "settings") {
            showHome()
        } else {
            super.onBackPressed()
        }
    }

    private fun incrementCounter() {
        counter += 1
        updateCounterText()
        InstrumentationServer.counter = counter
        Log.i(TAG, "Counter: $counter")
    }

    private fun updateCounterText() {
        tvCounter.text = "Count: $counter"
    }

    private fun showSettings() {
        screen = "settings"
        homeScreen.visibility = View.GONE
        settingsScreen.visibility = View.VISIBLE
        InstrumentationServer.screen = screen
        Log.i(TAG, "Screen: settings")
    }

    private fun showHome() {
        screen = "home"
        homeScreen.visibility = View.VISIBLE
        settingsScreen.visibility = View.GONE
        InstrumentationServer.screen = screen
        Log.i(TAG, "Screen: home")
    }

    private fun setTheme(newTheme: String) {
        theme = newTheme
        applyTheme()
        InstrumentationServer.theme = theme
        Log.i(TAG, "Theme: $theme")
    }

    private fun applyTheme() {
        val isDark = theme == "dark"
        val backgroundColor = if (isDark) Color.parseColor("#1e1e2e") else Color.WHITE
        val textColor = if (isDark) Color.WHITE else Color.BLACK
        val swatchColor = if (isDark) Color.parseColor("#8e44ad") else Color.parseColor("#2ecc71")

        rootLayout.setBackgroundColor(backgroundColor)
        swatch.setBackgroundColor(swatchColor)
        tvTitle.setTextColor(textColor)
        tvCounter.setTextColor(textColor)
        tvSettingsTitle.setTextColor(textColor)
        switchDark.setTextColor(textColor)
    }
}
