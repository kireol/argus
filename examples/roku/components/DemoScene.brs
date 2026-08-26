' Argus Demo: BrightScript SceneGraph implementation.
'
' Navigation (documented in examples/roku/README.md):
'   - Home:     OK increments the counter; right opens Settings.
'   - Settings: OK toggles the theme (light/dark); back returns to Home.
' The counter is preserved across navigation; theme and screen are only
' reset by relaunching the channel (device.reset in Argus terms).
'
' Every state change prints one exact log line to the debug console so
' `log_contains` assertions can observe the app without screenshots:
'   "App ready", "Counter: N", "Screen: home", "Screen: settings",
'   "Theme: light", "Theme: dark".

sub init()
    m.background = m.top.findNode("background")
    m.swatch = m.top.findNode("swatch")
    m.title = m.top.findNode("title")
    m.counterLabel = m.top.findNode("counter")
    m.settingsTitle = m.top.findNode("settingsTitle")
    m.toggleLabel = m.top.findNode("toggleLabel")
    m.backLabel = m.top.findNode("backLabel")

    m.count = 0
    m.theme = "light"
    m.screen = "home"

    m.top.setFocus(true)

    applyTheme()
    showHome()

    print "App ready"
end sub

sub showHome()
    m.screen = "home"
    m.title.visible = true
    m.counterLabel.visible = true
    m.settingsTitle.visible = false
    m.toggleLabel.visible = false
    m.backLabel.visible = false
    print "Screen: home"
end sub

sub showSettings()
    m.screen = "settings"
    m.title.visible = false
    m.counterLabel.visible = false
    m.settingsTitle.visible = true
    m.toggleLabel.visible = true
    m.backLabel.visible = true
    print "Screen: settings"
end sub

sub applyTheme()
    if m.theme = "dark"
        m.background.color = "#1e1e2e"
        m.swatch.color = "#8e44ad"
        textColor = "#ffffff"
    else
        m.background.color = "#ffffff"
        m.swatch.color = "#2ecc71"
        textColor = "#000000"
    end if

    m.title.color = textColor
    m.counterLabel.color = textColor
    m.settingsTitle.color = textColor
    m.toggleLabel.color = textColor
    m.backLabel.color = textColor

    print "Theme: " + m.theme
end sub

function onKeyEvent(key as string, press as boolean) as boolean
    if not press then return false

    handled = false

    if m.screen = "home"
        if key = "OK"
            m.count = m.count + 1
            m.counterLabel.text = "Count: " + m.count.toStr()
            print "Counter: " + m.count.toStr()
            handled = true
        else if key = "right"
            showSettings()
            handled = true
        end if
    else if m.screen = "settings"
        if key = "OK"
            if m.theme = "dark"
                m.theme = "light"
            else
                m.theme = "dark"
            end if
            applyTheme()
            handled = true
        else if key = "back"
            showHome()
            handled = true
        end if
    end if

    return handled
end function
