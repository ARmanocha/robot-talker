# Talking robot for PowerPoint

Type the words, and a cartoon robot says them out loud with your Mac's voice, lip-syncs, acts them
out (waves, points, thumbs up…), and shows them in a speech bubble, optionally with a translation.
You get an MP4 for any presentation, a ready-made `.pptx` slide, or a **Robot** button inside PowerPoint.

## Setup (macOS)

```
python3 -m pip install -r requirements.txt
xcode-select --install   # if you don't have the command line tools yet
```

## In PowerPoint (Robot tab)

Once set up, go to a slide, click **Robot → Add talking robot**, type the words, and wait
about 10 seconds. The robot is added to the bottom-left of the slide. When you present,
it walks in from the edge and says the line. It matches the slide's background color.

One-time setup (files are in `addin/`):

0. Run `addin/install.sh` (again if you move this folder). It installs the helper PowerPoint uses.
1. Open `RobotTalker-setup.pptx`. A **Robot** tab appears (the button works after step 4).
2. **Tools → Macro → Visual Basic Editor**, then **File → Import File…** and choose
   `RobotTalker.bas`. Close the editor.
3. **File → Save As…**, set File Format to **PowerPoint Add-in (.ppam)**, name it
   `RobotTalker`, save it in this project's `addin` folder. Close the setup file without saving.
4. **Tools → PowerPoint Add-ins…**, click **+**, choose `RobotTalker.ppam`, and click
   **Enable Macros** when asked.

If the Robot tab doesn't show up, run `python3 addin/build_addin.py --check addin/RobotTalker.ppam`,
then remove and re-add the add-in. You can also run it from **Tools → Macro → Macros → AddTalkingRobot**.

How it fits together: the add-in's macro calls `RobotTalker.scpt` (installed in
`~/Library/Application Scripts/com.microsoft.Powerpoint/`, source in `addin/RobotTalker.applescript`),
which runs `make_robot.py --enter`. `addin/install.sh` fills in this folder's location and your Python. Videos go to the shared Office folder
`~/Library/Group Containers/UBF8T346G9.Office/RobotTalker/`, and each one is embedded in your
presentation when inserted.

## From the command line

Type the words. The robot says them out loud, moves its mouth in sync, acts them out,
and you get an MP4 plus a ready-made `.pptx` slide that plays it automatically.

```
python3 make_robot.py "Hello everyone, welcome!"
```

Results go in `output/`: `hello-everyone-welcome.mp4` and `hello-everyone-welcome.pptx`.
To use it in your own deck, copy the slide over, or drag the MP4 onto any slide
(then set Playback → Start → Automatically).

## Actions

The robot picks actions from your words (hello → wave, look → point, great → thumbs up,
a question → think, yes → nod, we did it → celebrate). To choose one yourself, put a tag
**before** the words it goes with:

| Tag           | What it does                                   |
|---------------|------------------------------------------------|
| `[wave]`      | waves hello or goodbye                         |
| `[point]`     | points right, toward your slide content        |
| `[thumbsup]`  | thumbs up with happy eyes                      |
| `[nod]`       | nods yes                                       |
| `[think]`     | scratches its head and looks up                |
| `[celebrate]` | jumps with both arms up                        |
| `[talk]`      | plain hand gestures                            |
| `[exit]`      | walks off the frame waving (put it last)       |
| `[pause]`     | short silence; `[pause 2]` = exactly 2 seconds, `[pause 0]` = no gap |

Add `fast` or `slow` to any action to change how quickly those words are spoken,
for example `[think fast] Sorry, sorry...`. Fast also squeezes out the pauses inside the phrase.

A tag with no words after it plays the action silently, which makes a good ending:

```
python3 make_robot.py "[wave] Hi team! [point] Look at these numbers. [thumbsup] Great work, everyone. [celebrate]"
```

## Two languages in the bubble

Put a translation in `{curly braces}` after the words. It shows in the bubble under the spoken
words but isn't spoken. Pick a voice for the spoken language:

```
python3 make_robot.py --voice Zosia --rate 160 --enter sneak -f scripts/polish-class-intro.txt
```

`scripts/polish-class-intro.txt` is a full example: Polish voice with English underneath, exact pauses,
a fast "sorry", a sneaky entrance and an exit. Actions are also picked from Polish words
(cześć → wave, dziękuję → thumbs up…).

## Options

| Option            | Example              | Meaning                                              |
|-------------------|----------------------|------------------------------------------------------|
| `--voice NAME`    | `--voice Junior`     | macOS voice (default Samantha). `--voices` lists them |
| `--rate N`        | `--rate 150`         | speaking speed, words per minute (default 175)       |
| `--bg HEX`        | `--bg 1E2761`        | background color to match your slide                 |
| `--no-bubble`     |                      | square robot-only video with no speech bubble        |
| `--enter`         |                      | robot walks in from the left before it speaks        |
| `--enter sneak`   |                      | peeks in from the side first, then walks in          |
| `--character NAME`| `--character engineer` | who talks: `robot` (default) or `engineer` (safety engineer) |
| `--poses`         |                      | save a picture of the character doing every action   |
| `--name NAME`     | `--name intro`       | output file name                                     |
| `-f FILE`         | `-f script.txt`      | read a longer script from a text file                |
| `--no-pptx`       |                      | only make the MP4                                    |
| `--sheet`         |                      | also save a preview image, one frame per action      |

## Requirements

macOS with Python 3 and the packages in `requirements.txt`, plus Xcode command line tools.
On first run it builds a small video encoder from `encoder.swift` (no ffmpeg needed). Voices are
macOS's own; more can be downloaded in System Settings → Accessibility → Spoken Content.
