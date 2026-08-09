# Temperature: per-sensor traces with caution and warning lines.
Output assets/temperature.gif

Source demo/tapes/themes/gruvbox.tape
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 22
Set Width 1936
Set Height 822
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

Type "gc --temp"
Enter
Sleep 3s

# Temperature is the slowest metric on the machine -- it lags the CPU waveform
# by tens of seconds -- so this one gets a longer warm-up to have any shape.
Hide
Sleep 122s
Show

Sleep 18s

Hide
Type "q"
Sleep 1s
