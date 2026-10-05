# Claude CV Tailor

Tailor your CV to a job description with **one small Claude call per job**, then get a print-ready PDF that keeps your CV's exact layout.

Claude never rewrites your whole CV. It sees a numbered plain-text version of it and returns a short **JSON list of edits**: which bullets to keep, in which order, which to reword in the job ad's language, and which skills to add. Everything else (applying the edits, checking for made-up facts, building the HTML/PDF/Markdown, scoring the keyword match) runs locally and costs no tokens.

---

## What you get per job

```
out/0042_Acme/
├── CV_YourName_Acme.pdf   # the tailored CV, same fonts and layout as your master
├── cv_Acme.html           # source of the PDF
├── cv_Acme.md             # Markdown copy (paste into web forms)
├── edits.json             # Claude's edit list (editable, re-renderable for free)
├── reasoning.md           # what was cut, rewritten, and why; real skill gaps
├── match_report.md        # keyword coverage and similarity to the JD, before vs after
└── jd_source.txt          # the job description used (when run from the database)
```

## How it stays truthful

The system prompt in `tailor.py` sets strict rules, and local checks back them up:

- Rewrites must keep **every factual claim**: same numbers, same scope, same employer, no new tools.
- **Implied skills** (things your work could not have been done without) may be named in the bullet that shows them.
- **Adjacent skills** (JD wants AWS, you used GCP) only go in the skills section as `AWS (equivalent: GCP)`, never in a bullet.
- Every important JD term must either be covered truthfully or declared a **gap**, which goes to `reasoning.md` for your cover letter or interview prep instead of onto the CV.
- **Guard warnings** (local, no AI) flag any rewritten bullet that contains a number not in the original, or an adjacent tool inside a bullet.
- An interactive **review step** shows every change before the PDF is made. You can untick skill additions or stop.

---

## Requirements

- **Python 3.9+**
- **[Claude Code](https://docs.claude.com/en/docs/claude-code)** CLI (`claude`), logged in. Calls go through `claude -p`, so they use your Claude subscription instead of paid API billing.
- **[pandoc](https://pandoc.org/installing.html)**: on your `PATH`, or the binary copied to `bin/pandoc`.
- **WeasyPrint** system libraries (Pango): see [WeasyPrint install notes](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html). On Debian/Ubuntu: `sudo apt install libpango-1.0-0 libpangoft2-1.0-0`.

## Setup

```bash
git clone <this-repo-url> ClaudeCVTailor
cd ClaudeCVTailor
python -m venv venv
venv/bin/pip install -r requirements.txt

# Your personal files, from the examples. All of these are git-ignored.
cp profile.example.json profile.json
cp cv_master.example.md cv_master.md
cp skills_inventory.example.md skills_inventory.md

# Edit the three files above, then build the master CV:
venv/bin/python build_master.py
```

### Your personal files

| File | What it holds |
|---|---|
| `profile.json` | Name, headline, email, phone, links, photo path, PDF file name prefix, output folder |
| `cv_master.md` | Your full CV in Markdown: every bullet you might ever want. Tailoring only cuts and rewords; it never adds bullets. |
| `skills_inventory.md` | Skills you really have but your CV doesn't say outright. Claude may use them. Lines starting with `#` are ignored. |
| `template_assets/` | Optional photo, referenced from `profile.json` (`"photo": "template_assets/photo.png"`, or `""` for none) |

`profile.json` fields:

| Field | Meaning |
|---|---|
| `name`, `headline` | Header title and subtitle |
| `email`, `phone` | Contact line; leave either empty to omit it |
| `links` | List of `{"label": "...", "url": "..."}` shown under the contact line |
| `photo` | Path to a square image, or `""` for no photo |
| `pdf_name` | PDF prefix: `CV_AlexExample` gives `CV_AlexExample_<Company>.pdf` |
| `output_dir` | Where `tailor_from_db.sh` writes job folders (relative to this folder, or absolute). Default `out` |

### `cv_master.md` format

Follow `cv_master.example.md`. `build_master.py` relies on this shape:

- The **first paragraph** is the summary.
- `# SECTION` headings, each followed by an empty `# ` line (rendered as a horizontal rule).
- `## Role - Company \| City, Country MM/YYYY - MM/YYYY` (or `- Present`). The date is pinned to the right. Role lines don't wrap, so keep them short enough for one line.
- `### Project or degree line` (also takes an optional trailing date range).
- `- bullet` lines under each role or project.
- Skills lines as `**Label :** item, item, item`.

`build_master.py` gives every bullet an id (`b1`, `b2`, ...), every skills line an id (`s1`, ...), and the summary the id `summary`, then writes `master_cv.html`. **Re-run it every time you edit `cv_master.md`.**

---

## Usage

### Tailor from a job description file

```bash
venv/bin/python tailor.py jd.txt Acme out/Acme
#                          JD_FILE COMPANY OUT_DIR
```

You'll see the strategy, cuts, rewrites, gaps, and guard warnings, then choose which skill additions to keep and whether to generate the PDF.

### Tailor from the LinkedIn_Scraper database

Works with the SQLite database from the companion job scraper (or any table with `id`, `title`, `company`, `job_description`, `applied`). The database is opened **read-only**.

```bash
./tailor_from_db.sh --db ../LinkedIn_Scraper/data/my_database.db
```

| Command | What it does |
|---|---|
| `./tailor_from_db.sh --db DB` | Tailors every job marked Applied that doesn't have a PDF yet |
| `./tailor_from_db.sh --db DB --job-id 294` | Only job #294, applied or not |
| `./tailor_from_db.sh --db DB --job-id 294 --force` | Redo #294 even though its PDF exists |
| `./tailor_from_db.sh --db DB --force` | Redo every applied job |
| `./tailor_from_db.sh --db DB --table filtered_jobs` | Read another table (default `jobs`) |

Output goes to `<output_dir>/<0000-padded id>_<Company>/`.

### Re-render without Claude (0 tokens)

Edit a saved `edits.json` by hand, then rebuild from it:

```bash
venv/bin/python tailor.py jd.txt Acme out/Acme --reuse-edits out/Acme/edits.json
```

### Options

| Environment variable | Effect |
|---|---|
| `EFFORT=low\|medium\|high` | How hard Claude thinks (default `low`). Higher gives better edits but uses more tokens. |
| `AUTO_YES=1` | Skip the review questions and keep everything. For testing only. |

Each Claude call's token usage is appended to `usage_log.tsv`.

---

## Customising the look

- `template.html`: page skeleton. `{{NAME}}`, `{{HEADLINE}}`, `{{CONTACT}}`, `{{PHOTO}}` are filled from `profile.json`; `{{CV_BODY}}` is your CV.
- `template_style.css`: A4 page, margins, fonts, spacing, bullets. Values were copied from a Word CV so the PDF matches it; change freely.
- `fonts/`: [Spectral](https://fonts.google.com/specimen/Spectral), SIL Open Font License.

## Files

```
tailor.py              # one Claude call -> edit list -> review -> HTML/PDF/MD + reports
build_master.py        # cv_master.md -> master_cv.html with stable ids
cv_profile.py          # reads profile.json; finds pandoc / weasyprint
tailor_from_db.sh      # batch-tailor applied jobs from a SQLite job database
template.html, template_style.css, fonts/
*.example.*            # templates for your personal, git-ignored files
```

## Troubleshooting

| Problem | Fix |
|---|---|
| `No profile.json` / `No cv_master.md` | Copy the matching `*.example.*` file and fill it in. |
| `pandoc not found` | Install pandoc, or copy its binary to `bin/pandoc`. |
| `claude failed` | Install Claude Code and run `claude` once to log in. |
| WeasyPrint errors about Pango or cairo | Install the system libraries listed in its docs. |
| Date overlaps the role title in the PDF | Shorten that `##` line in `cv_master.md` and re-run `build_master.py`. |
| Tailored CV mentions something untrue | Check `reasoning.md` and the guard warnings, edit `edits.json`, re-render with `--reuse-edits`, and tighten `skills_inventory.md`. |

**Always read the PDF before you send it.** The checks catch the common problems, not all of them.
