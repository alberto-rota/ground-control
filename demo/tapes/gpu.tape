# GPU: utilization/VRAM plot, the telemetry line, and the process rows.
Output assets/gpu.gif

Source demo/tapes/themes/gruvbox.tape
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 11
Set Width 1028
Set Height 456
Set Padding 9
Set BorderRadius 5
Set Framerate 20
Set TypingSpeed 60ms

Hide
Type "gc-prepare --theme gruvbox --layout horizontal --refresh 1 --history 120 --no-alerts"
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

# Then the process rows. train_load.py runs three PyTorch jobs with
# different VRAM footprints (train_resnet / train_vit / train_llm), so there
# is something to list -- and the K/T/I buttons on each row have a real PID
# to point at.
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
