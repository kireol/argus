' Argus Demo channel entry point.
'
' Creates the root SceneGraph screen and pumps its message loop. All demo
' behaviour (counter, navigation, theme) lives in components/DemoScene.brs so
' this file stays a minimal, boilerplate launcher.

sub Main()
    screen = CreateObject("roSGScreen")
    m.port = CreateObject("roMessagePort")
    screen.setMessagePort(m.port)

    scene = screen.CreateScene("DemoScene")
    screen.show()

    while true
        msg = wait(0, m.port)
        msgType = type(msg)
        if msgType = "roSGScreenEvent"
            if msg.isScreenClosed()
                return
            end if
        end if
    end while
end sub
