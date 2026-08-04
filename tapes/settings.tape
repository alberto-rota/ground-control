# Ground Control - Settings tab (widget selection, refresh rate, history, theme)
# Regenerate from the repository root:  vhs tapes/settings.tape
# Conventions and requirements: tapes/README.md

Output assets/settings.gif

Set Shell "bash"
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 13
Set Width 1500
Set Height 900
Set Padding 10
Set Framerate 12

Require gc

Env XDG_CONFIG_HOME "/tmp/gc-vhs-settings"

Hide
Type@5ms "rm -rf /tmp/gc-vhs-settings && clear"
Enter
Type@5ms "gc --cpu --ram --net"
Enter
Sleep 6s
Type "a"
Sleep 6s
Show

# Open Settings on camera, then walk down the panel so the sections it holds are
# actually visible rather than implied.
Sleep 2s
Type "s"
Sleep 4s
Down
Sleep 1s
Down
Sleep 1s
Down
Sleep 1s
Down
Sleep 2s
Down
Sleep 1s
Down
Sleep 1s
Down
Sleep 3s

Hide
Type "q"
Sleep 1s
