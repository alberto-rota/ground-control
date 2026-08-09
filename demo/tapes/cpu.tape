# CPU: per-core heatmap, load telemetry, and the three view tabs.
Output assets/cpu.gif

Source demo/tapes/themes/catppuccin-mocha.tape
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 15
Set Width 1320
Set Height 580
Set Padding 12
Set BorderRadius 10
Set Framerate 20
Set TypingSpeed 60ms

Hide
Type "gc-prepare --theme catppuccin-mocha --layout horizontal --refresh 1 --history 120"
Enter
Sleep 2s
Type "clear"
Enter
Show

Type "gc --cpu"
Enter
Sleep 3s

Hide
Sleep 122s
Show

# The load generator drives each core with a phase-shifted duty cycle, so the
# heatmap ripples rather than flickering at random.
Sleep 8s

# Panel bindings need the panel focused first.
Type "]"
Sleep 1s
Type "2"
Sleep 4s
Type "3"
Sleep 5s
Type "1"
Sleep 5s

Hide
Type "q"
Sleep 1s
