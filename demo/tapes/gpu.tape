# GPU: utilization/VRAM plot, the telemetry line, the process rows, and
# plotting only the selected processes.
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
Sleep 6s

# Select two rows for the plot. Focus order from the panel is: tab bar, then
# per row the select button and K/T/I -- so two Tabs reach row 1's button and
# four more reach row 2's. Count carefully when changing this: one Tab too
# many lands Enter on a K button, and that SIGKILLs a real process.
Tab 2
Sleep 1s
Enter
Sleep 2s
Tab 4
Sleep 1s
Enter
Sleep 3s

# 1 takes focus back to the panel and shows the plot, now of just those two
# processes (the bottom edge says how many are selected) ...
Type "1"
Sleep 9s

# ... and c goes back to the whole card.
Type "c"
Sleep 5s

Hide
Type "q"
Sleep 1s
