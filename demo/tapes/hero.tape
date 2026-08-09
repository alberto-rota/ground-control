# Every widget at once, in the default grid layout. This is the banner.
Output assets/hero.gif

Source demo/tapes/themes/tokyo-night.tape
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 14
Set Width 1500
Set Height 900
Set Padding 12
Set BorderRadius 10
Set Framerate 20
Set TypingSpeed 60ms

# Prepare this recording's throwaway config off-camera.
Hide
Type "gc-prepare --theme tokyo-night --layout grid --refresh 1 --history 120"
Enter
Sleep 2s
Type "clear"
Enter
Show

Type "gc"
Enter
Sleep 3s

# Fill the 2-minute history off-camera. Without this the plots open with a
# flat run of zeros on the left -- an artifact of an empty deque that reads
# like a bug in the app.
Hide
Sleep 122s
Show

# The waveform's slow component has a 23s period, so 20s of frames never
# repeats itself.
Sleep 20s

Hide
Type "q"
Sleep 1s
