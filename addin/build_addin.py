#!/usr/bin/env python3
"""Builds the PowerPoint add-in pieces for the talking robot.

    python3 build_addin.py                    # makes RobotTalker-setup.pptx (Robot tab + button icon)
    python3 build_addin.py --check FILE.ppam  # adds the Robot tab to a saved add-in if it's missing
"""

import argparse
import io
import shutil
import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import make_robot as mr  # noqa: E402

UI_REL_TYPE = "http://schemas.microsoft.com/office/2007/relationships/ui/extensibility"
IMG_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"

CUSTOM_UI = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<customUI xmlns="http://schemas.microsoft.com/office/2009/07/customui">
  <ribbon>
    <tabs>
      <tab id="robotTab" label="Robot">
        <group id="robotGroup" label="Talking robot">
          <button id="addRobot" label="Add talking robot" size="large" image="robotIcon"
                  onAction="AddTalkingRobotButton" screentip="Add a talking robot"
                  supertip="Type the words, and a robot walks onto this slide and says them."/>
        </group>
      </tab>
    </tabs>
  </ribbon>
</customUI>
"""

CUSTOM_UI_RELS = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="robotIcon" Type="{IMG_REL_TYPE}" Target="images/robot.png"/>
</Relationships>
"""


def make_icon(size=64) -> bytes:
    lay = mr.layouts(no_bubble=True)
    canvas = Image.new("RGBA", (lay["W"] * mr.SS, lay["H"] * mr.SS), (0, 0, 0, 0))
    p = mr.pose("idle", 0.0, 0.0)
    p.update(happy=1.0, tilt=0)
    mr.draw_robot(ImageDraw.Draw(canvas), lay, (255, 255, 255), 0.4, "idle", p, 0.0)
    img = canvas.reduce(mr.SS)
    # Head and antenna only.
    x0 = lay["ox"] + 172 * lay["scale"]
    y0 = lay["oy"] + 30 * lay["scale"]
    side = 176 * lay["scale"]
    img = img.crop((int(x0), int(y0), int(x0 + side), int(y0 + side))).resize((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def has_custom_ui(path: Path) -> bool:
    with zipfile.ZipFile(path) as z:
        return "customUI/customUI14.xml" in z.namelist()


def add_custom_ui(src: Path, dst: Path):
    icon = make_icon()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "_rels/.rels":
                rel = f'<Relationship Id="rIdRobotUI" Type="{UI_REL_TYPE}" Target="customUI/customUI14.xml"/>'
                data = data.decode("utf-8").replace("</Relationships>", rel + "</Relationships>").encode("utf-8")
            elif item.filename == "[Content_Types].xml":
                text = data.decode("utf-8")
                if 'Extension="png"' not in text:
                    text = text.replace("<Default ", '<Default Extension="png" ContentType="image/png"/><Default ', 1)
                data = text.encode("utf-8")
            zout.writestr(item, data)
        zout.writestr("customUI/customUI14.xml", CUSTOM_UI)
        zout.writestr("customUI/_rels/customUI14.xml.rels", CUSTOM_UI_RELS)
        zout.writestr("customUI/images/robot.png", icon)


def build_setup(out: Path):
    from pptx import Presentation
    from pptx.util import Emu

    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(12192000), Emu(6858000)
    plain = out.with_suffix(".plain.pptx")
    prs.save(plain)
    add_custom_ui(plain, out)
    plain.unlink()
    (HERE / "robot-icon.png").write_bytes(make_icon(256))
    print(f"Made {out}")


def check_addin(path: Path):
    if has_custom_ui(path):
        print(f"{path.name} already has the Robot tab. Nothing to do.")
        return
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    tmp = path.with_suffix(".tmp.zip")
    add_custom_ui(path, tmp)
    tmp.replace(path)
    print(f"Added the Robot tab to {path.name} (backup: {backup.name})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", help="saved .ppam add-in to check and fix")
    args = ap.parse_args()
    if args.check:
        check_addin(Path(args.check).expanduser())
    else:
        build_setup(HERE / "RobotTalker-setup.pptx")
