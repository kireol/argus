package com.argus.demo

import android.util.Log
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStream
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread

private const val TAG = "ArgusDemo"
private const val PORT = 8085

/**
 * Minimal HTTP instrumentation server (see docs/instrumentation.md).
 *
 * Debug-build only: [MainActivity] starts it behind `BuildConfig.DEBUG` and
 * keeps its fields in sync with app state. Binds to all interfaces on
 * [PORT]; on a device/emulator, reach it from the host with
 * `adb forward tcp:8085 tcp:8085`.
 */
object InstrumentationServer {

    @Volatile var ready: Boolean = false
    @Volatile var screen: String = "home"
    @Volatile var counter: Int = 0
    @Volatile var theme: String = "light"

    private val started = AtomicBoolean(false)

    /** Starts the listener thread once per process; safe to call more than once. */
    fun start() {
        if (!started.compareAndSet(false, true)) return
        thread(name = "argus-instrumentation", isDaemon = true) {
            try {
                ServerSocket(PORT).use { server ->
                    while (true) {
                        val socket = server.accept()
                        handle(socket)
                    }
                }
            } catch (e: Exception) {
                Log.i(TAG, "Instrumentation server stopped: ${e.message}")
            }
        }
    }

    private fun handle(socket: Socket) {
        socket.use { client ->
            try {
                val reader = BufferedReader(InputStreamReader(client.getInputStream()))
                val requestLine = reader.readLine() ?: return
                val path = requestLine.split(" ").getOrNull(1) ?: "/"
                val (status, body) = routeFor(path)
                writeResponse(client.getOutputStream(), status, body)
            } catch (e: Exception) {
                Log.i(TAG, "Instrumentation request failed: ${e.message}")
            }
        }
    }

    private fun routeFor(path: String): Pair<Int, String> = when (path) {
        "/test/status" -> 200 to statusJson()
        "/test/state" -> 200 to stateJson()
        "/test/health" -> 200 to "{\"ok\":true}"
        else -> 404 to "{\"error\":\"not found\"}"
    }

    private fun statusJson(): String =
        "{\"application\":\"ArgusDemo\",\"version\":\"1.0.0\",\"ready\":$ready," +
            "\"screen\":\"$screen\",\"capabilities\":[\"status\",\"state\"]}"

    private fun stateJson(): String =
        "{\"counter\":$counter,\"theme\":\"$theme\",\"screen\":\"$screen\"}"

    private fun writeResponse(output: OutputStream, status: Int, body: String) {
        val reason = if (status == 200) "OK" else "Not Found"
        val bytes = body.toByteArray(Charsets.UTF_8)
        val header = "HTTP/1.1 $status $reason\r\n" +
            "Content-Type: application/json\r\n" +
            "Content-Length: ${bytes.size}\r\n" +
            "Connection: close\r\n\r\n"
        output.write(header.toByteArray(Charsets.UTF_8))
        output.write(bytes)
        output.flush()
    }
}
