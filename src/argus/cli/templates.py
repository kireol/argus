"""Configuration template written by ``argus init``."""

CONFIG_TEMPLATE = """\
# Universal Test Framework — user configuration
#
# Secrets belong in environment variables (${VAR} syntax), never in this file.
# See docs/configuration.md for every option.

backend:
  base_url: ${BACKEND_URL}
  token: ${BACKEND_TOKEN}
  # timeout: 10s
  # verify_tls: true
  # state_endpoint: /api/state
  # health_endpoint: /health

devices:

  android:
    type: android
    serial: ${ANDROID_SERIAL}
    app_package: com.example.app
    # app_activity: .MainActivity
    instrumentation:
      base_url: http://127.0.0.1:8085

  living_room:
    type: yocto
    host: ${YOCTO_HOST}
    username: ${YOCTO_USER}
    private_key: ${YOCTO_KEY}
    # host_key_policy: reject   # or auto_add for lab devices
    screenshot:
      command: "weston-screenshooter -f {path}"
      remote_path: /tmp/utf_screenshot.png
    app:
      start: "systemctl start myapp"
      stop: "systemctl stop myapp"
      process: "myapp"
    instrumentation:
      base_url: http://${YOCTO_HOST}:8085

verification:
  image:
    default_threshold: 0.90
    grayscale: false

# Named screen regions usable in tests as `region: movie_artwork`
regions: {}
#  movie_artwork:
#    x: 100
#    y: 100
#    width: 500
#    height: 400

results:
  dir: results
  retain_on_success: false

logging:
  level: INFO
"""
