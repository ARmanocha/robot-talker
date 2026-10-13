-- Helper for the Talking Robot PowerPoint add-in.
-- Don't compile this file directly: run addin/install.sh, which fills in the paths below and
-- installs it as ~/Library/Application Scripts/com.microsoft.Powerpoint/RobotTalker.scpt
-- PowerPoint calls makeRobot via AppleScriptTask with "backgroundHex|fileName|words".

property robotDir : "__ROBOT_DIR__"
property pythonPath : "__PYTHON__"
-- PowerPoint is sandboxed; it can always read files in the shared Office folder.
property officeDir : "__OFFICE_DIR__"

on makeRobot(params)
	return makeRobotIn(officeDir, params)
end makeRobot

on makeRobotIn(outDir, params)
	set AppleScript's text item delimiters to "|"
	set parts to text items of params
	set bgHex to item 1 of parts
	set outName to item 2 of parts
	set robotWords to (items 3 thru -1 of parts) as text
	set AppleScript's text item delimiters to ""
	set cmd to "mkdir -p " & quoted form of outDir & " && cd " & quoted form of robotDir & " && " & ¬
		quoted form of pythonPath & " make_robot.py --enter --no-pptx --bg " & quoted form of bgHex & ¬
		" --name " & quoted form of outName & " --outdir " & quoted form of outDir & ¬
		" -- " & quoted form of robotWords
	try
		do shell script cmd
		return "OK:" & outDir & "/" & outName & ".mp4"
	on error errMsg
		return "ERROR:" & errMsg
	end try
end makeRobotIn
