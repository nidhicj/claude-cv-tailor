#!/usr/bin/env bash
# Usage: ./tailor_from_db.sh --db /path/to/my_database.db [--table jobs] [--job-id N] [--force]
#
# Reads LinkedIn_Scraper's SQLite DB (read-only, no code coupling with that repo)
# and tailors a CV for every job marked applied=1 that doesn't already have one,
# using tailor.py (one small Claude call returning a JSON edit list, plus a review gate) for each.
#
# --job-id N   only process that one job, regardless of its applied flag
# --force      regenerate even if an output PDF already exists for that job
#
# Output folder and PDF file name come from profile.json (output_dir, pdf_name).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON=python3
IFS=$'\t' read -r NEWCVS_DIR PDF_NAME < <("$PYTHON" "$SCRIPT_DIR/cv_profile.py" --shell)
[[ -n "$PDF_NAME" ]] || exit 1
DB=""; TABLE="jobs"; JOB_ID=""; FORCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db) DB="$2"; shift 2 ;;
    --table) TABLE="$2"; shift 2 ;;
    --job-id) JOB_ID="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [[ -z "$DB" ]]; then
  echo "Usage: $0 --db /path/to/my_database.db [--table jobs] [--job-id N] [--force]"
  exit 1
fi
if [[ ! -f "$DB" ]]; then
  echo "No database at $DB"; exit 1
fi

WORK_DIR="$SCRIPT_DIR/tmp/.pending"; rm -rf "$WORK_DIR"; mkdir -p "$WORK_DIR"

# Extract candidate jobs to one file per job (avoids any shell-quoting issue with
# descriptions containing quotes/newlines). Opens the DB read-only.
python3 - "$DB" "$TABLE" "$JOB_ID" "$WORK_DIR" <<'PYEOF'
import sqlite3, sys, re, pathlib

db, table, job_id, work_dir = sys.argv[1:5]
work_dir = pathlib.Path(work_dir)

conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

if job_id:
    cur.execute(f"SELECT id, title, company, job_description FROM {table} WHERE id = ?", (job_id,))
else:
    cur.execute(f"SELECT id, title, company, job_description FROM {table} WHERE applied = 1 ORDER BY id")

rows = cur.fetchall()
if not rows:
    print("No matching jobs found.")
    sys.exit(0)

manifest = []
for row in rows:
    company = (row["company"] or "unknown").strip()
    safe_company = re.sub(r'[\\/:*?"<>|]', "", company).strip() or "unknown"
    jd_path = work_dir / f"{row['id']}.txt"
    jd_path.write_text(row["job_description"] or "", encoding="utf-8")
    manifest.append(f"{row['id']}\t{safe_company}\t{row['title']}")

(work_dir / "manifest.tsv").write_text("\n".join(manifest) + "\n", encoding="utf-8")
print(f"{len(rows)} job(s) queued.")
PYEOF

MANIFEST="$WORK_DIR/manifest.tsv"
if [[ ! -f "$MANIFEST" ]]; then
  exit 0
fi

# mapfile (not `while read ... < "$MANIFEST"`) so the loop body's real stdin stays
# free for tailor.py's interactive review prompts.
mapfile -t JOB_LINES < "$MANIFEST"
for line in "${JOB_LINES[@]}"; do
  IFS=$'\t' read -r id company title <<< "$line"
  [[ -z "$id" ]] && continue
  PADDED_ID=$(printf "%04d" "$id")
  OUT_DIR="$NEWCVS_DIR/${PADDED_ID}_$company"
  PDF="$OUT_DIR/${PDF_NAME}_$company.pdf"

  if [[ -f "$PDF" && "$FORCE" -eq 0 ]]; then
    echo "Skipping job $id ($company - $title): already tailored at $PDF"
    continue
  fi

  echo ""
  echo "=== Job $id: $title @ $company ==="
  mkdir -p "$OUT_DIR"
  cp "$WORK_DIR/$id.txt" "$OUT_DIR/jd_source.txt"
  "$PYTHON" "$SCRIPT_DIR/tailor.py" "$OUT_DIR/jd_source.txt" "$company" "$OUT_DIR"
done

rm -rf "$WORK_DIR"
