# CPU: per-core heatmap, load telemetry, and the three view tabs.
Output assets/cpu.gif

Source demo/tapes/themes/gruvbox.tape
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 22
Set Width 1936
Set Height 852
Set Padding 18
Set BorderRadius 10
Set Framerate 20
Set TypingSpeed 60ms

Hide
Type "gc-prepare --theme gruvbox --layout horizontal --refresh 1 --history 120 --no-alerts"
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
