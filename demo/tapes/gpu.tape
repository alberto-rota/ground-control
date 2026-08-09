# GPU: utilization/VRAM plot, the telemetry line, and the process rows.
Output assets/gpu.gif

Source demo/tapes/themes/one-dark.tape
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 15
Set Width 1400
Set Height 620
Set Padding 12
Set BorderRadius 10
Set Framerate 20
Set TypingSpeed 60ms

Hide
Type "gc-prepare --theme one-dark --layout horizontal --refresh 1 --history 120"
Enter
Sleep 2s
Type "clear"
Enter
Show

Type "gc --gpu"
Enter
Sleep 3s

Hide
Sleep 122s
Show

# Plot first: utilization above, VRAM below, with power/clocks/throttle state
# on the telemetry line under the split bar.
Sleep 10s

# Then the process rows. The load generator runs three CUDA processes with
# different VRAM footprints, so there is something to list -- and the K/T/I
# buttons on each row have a real PID to point at.
#
# "2", not "p": the help banner offers `p` for the Processes tab, but the
# widget binds it to show_plot alongside "1". Use the number.
Type "]"
Sleep 1s
Type "2"
Sleep 10s
Type "1"
Sleep 5s

Hide
Type "q"
Sleep 1s
