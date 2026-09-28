# gc --once: one sample, no TUI. For cron, CI and health checks.
Output assets/snapshot.gif

Source demo/tapes/themes/gruvbox.tape
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 12
Set Width 930
Set Height 556
Set Padding 10
Set BorderRadius 5
Set Framerate 20
Set TypingSpeed 45ms

# --alerts-tuned so a breach is reliably present in all three outputs. On a
# healthy machine the shipped thresholds are never crossed, which would make
# this a demo of --check that only ever prints 0 -- and of a JSON document whose
# "alerts" array is always empty.
Hide
Type "gc-prepare --theme gruvbox --alerts-tuned"
Enter
Sleep 2s
Type "clear"
Enter
Show

Sleep 1s
Type "# one sample, human-readable"
Enter
Type "gc --once"
Enter
Sleep 4s

Type "# ...or as JSON, for scripts"
Enter
Type "gc --once --json | head -20"
Enter
Sleep 5s

# --check turns the worst breach into an exit code, following the Nagios
# convention, so this drops into an existing monitoring setup unchanged.
Type "# ...or as an exit code: 0 ok, 1 warn, 2 crit, 3 collector failed"
Enter
Type "gc --once --check ; echo exit=$?"
Enter
Sleep 5s

Hide
Type "q"
Sleep 1s
