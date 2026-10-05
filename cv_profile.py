"""Personal settings (name, contact, photo, output folder) from profile.json.

profile.json is git-ignored; profile.example.json is the template. Also locates
the pandoc and weasyprint binaries. Shared by build_master.py, tailor.py and
tailor_from_db.sh (`python3 cv_profile.py --shell` prints OUTPUT_DIR and PDF_NAME).
"""
import html
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).parent
PROFILE = HERE / "profile.json"


def load_profile():
    if not PROFILE.exists():
        sys.exit("No profile.json - copy profile.example.json to profile.json and fill it in.")
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    profile.setdefault("pdf_name", "CV_" + "".join(profile.get("name", "").split()))
    profile.setdefault("output_dir", "out")
    return profile


def output_dir(profile):
    path = Path(profile["output_dir"])
    return path if path.is_absolute() else (HERE / path).resolve()


def header_fields(profile):
    """{{PLACEHOLDER}} -> HTML for template.html's header."""
    esc = html.escape
    photo = profile.get("photo")
    contact_line = " | ".join(f"{icon} {esc(profile[key])}"
                              for icon, key in (("📧", "email"), ("📞", "phone")) if profile.get(key))
    links = " |\n      ".join(f'<a href="{esc(l["url"])}">{esc(l["label"])}</a>'
                               for l in profile.get("links", []))
    return {
        "{{NAME}}": esc(profile.get("name", "")),
        "{{HEADLINE}}": esc(profile.get("headline", "")),
        "{{PHOTO}}": f'  <img class="photo" src="{esc(photo)}">' if photo else "",
        "{{CONTACT}}": "<br>\n      ".join(p for p in (contact_line, links) if p),
    }


def tool(name):
    """Bundled copy (bin/pandoc, venv/bin/weasyprint) if present, else whatever is on PATH."""
    for local in (HERE / "bin" / name, HERE / "venv" / "bin" / name):
        if local.exists():
            return str(local)
    found = shutil.which(name)
    if not found:
        sys.exit(f"{name} not found - install it or put it in bin/ (see README).")
    return found


if __name__ == "__main__" and sys.argv[1:] == ["--shell"]:
    p = load_profile()
    print(f"{output_dir(p)}\t{p['pdf_name']}")
