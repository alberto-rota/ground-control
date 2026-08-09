# Threshold alerts: breached panels get a coloured border and a ▲ or ■ marker.
Output assets/alerts.gif

Source demo/tapes/themes/neon.tape
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 14
Set Width 1500
Set Height 900
Set Padding 12
Set BorderRadius 10
Set Framerate 20
Set TypingSpeed 60ms

# --alerts-tuned lowers the thresholds to where the demo load actually crosses
# them. The shipped defaults are set for real incidents, which is exactly wrong
# for a 20-second recording of the alerting feature.
Hide
Type "gc-prepare --theme neon --layout grid --refresh 1 --history 120 --alerts-tuned"
Enter
Sleep 2s
Type "clear"
Enter
Type "gc"
Enter
Sleep 122s
Show

# Panels move between ok, warn (▲) and crit (■) as the load waveform rises and
# falls. The marker matters as much as the colour: it survives a monochrome
# terminal, and colour alone would not.
Sleep 14s

# a turns alerting off and on again at runtime.
Type "a"
Sleep 5s
Type "a"
Sleep 8s

Hide
Type "q"
Sleep 1s
