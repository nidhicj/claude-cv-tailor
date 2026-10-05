"""One-time (and whenever cv_master.md changes): build master_cv.html with stable IDs.

cv_master.md -> pandoc HTML -> tag every editable element with a data-id:
  summary     the intro paragraph
  b1..bN      every bullet
  s1..sN      every skills line
Then drop it into template.html (exact docx fonts/margins/photo), with the header
filled from profile.json. No Claude, no tokens.
"""
import re
import subprocess
from pathlib import Path
import sys

from bs4 import BeautifulSoup

from cv_profile import header_fields, load_profile, tool

HERE = Path(__file__).parent
if not (HERE / "cv_master.md").exists():
    sys.exit("No cv_master.md - copy cv_master.example.md to cv_master.md and write your CV in it.")
profile = load_profile()

body_html = subprocess.run(
    [tool("pandoc"), str(HERE / "cv_master.md"), "-t", "html"],
    capture_output=True, text=True, check=True,
).stdout
soup = BeautifulSoup(body_html, "html.parser")

# pandoc renders the docx's empty spacer headings as empty tags. The empty Heading1
# after each section title carries a Word horizontal rule (o:hr) - keep it as <hr>.
for h in soup.find_all(["h1", "h2", "h3"]):
    if not h.get_text(strip=True):
        if h.name == "h1":
            h.replace_with(soup.new_tag("hr", attrs={"class": "section-rule"}))
        else:
            h.decompose()

# Split "ML & CV ...<br>Infrastructure: ..." into two skills lines
for p in soup.find_all("p"):
    if p.find("br") and p.find("strong"):
        before, _, after = p.decode_contents().partition("<br/>")
        p.clear()
        p.append(BeautifulSoup(before, "html.parser"))
        label, _, rest = after.strip().partition(":")
        new_p = soup.new_tag("p")
        new_p.append(BeautifulSoup(f"<strong>{label.strip()} :</strong>{rest}", "html.parser"))
        p.insert_after(new_p)

# Role (h2) and education (h3) lines end in a date range that the docx pushes to a
# right tab stop. Split it into its own span so CSS can pin it right and never wrap it.
DATE_RE = re.compile(r"\s*(\d{2}/\d{4}\s*-\s*(?:\d{2}/\d{4}|Present))\s*$")
for h in soup.find_all(["h2", "h3"]):
    text = " ".join(h.get_text(" ", strip=True).split())
    m = DATE_RE.search(text)
    if not m:
        continue
    h.clear()
    h["class"] = "dated"
    role = soup.new_tag("span", attrs={"class": "role"})
    role.string = text[: m.start()].strip()
    date = soup.new_tag("span", attrs={"class": "date"})
    date.string = m.group(1)
    h.append(role)
    h.append(date)

summary = soup.find("p")
summary["data-id"] = "summary"

for i, li in enumerate(soup.find_all("li"), 1):
    li["data-id"] = f"b{i}"

skills = [p for p in soup.find_all("p") if p.find("strong") and p is not summary]
for i, p in enumerate(skills, 1):
    p["data-id"] = f"s{i}"

template = (HERE / "template.html").read_text(encoding="utf-8")
for placeholder, value in header_fields(profile).items():
    template = template.replace(placeholder, value)
(HERE / "master_cv.html").write_text(template.replace("{{CV_BODY}}", str(soup)), encoding="utf-8")
print(f"master_cv.html built: summary + {len(soup.find_all('li'))} bullets + {len(skills)} skills lines")
