#!/usr/bin/env bash
# Verification for the offer-checkpost entry: structural and secret-hygiene checks, then
# lint and tests.
# Kept compatible with the bash 3.2 that ships on macOS: no mapfile, no negative array
# indices.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

fail() { echo "ERROR: $1" >&2; exit 1; }

# bin/publish.sh leaves SPEC.md out of the public repo, so the spec checks run only inside
# the monorepo; the published copy still runs every other check.
in_monorepo=false
[ -f ../../bin/publish.sh ] && [ -f ../../bin/verify.sh ] && in_monorepo=true

echo "== entry root files =="
for f in README.md LICENSE .gitignore .env.example docs/submission.md; do
  [ -f "$f" ] || fail "$f is missing from the entry root"
  echo "  ok: $f"
done

echo "== LICENSE is MIT, with the copyright line =="
grep -q '^MIT License' LICENSE || fail "LICENSE is not the MIT licence text"
grep -q '^Copyright (c) 2026 Chetan Gupta$' LICENSE || fail "LICENSE lost its copyright line"

if [ "$in_monorepo" = true ]; then
  [ -f SPEC.md ] || fail "SPEC.md is missing from the entry root"
  echo "  ok: SPEC.md"

  echo "== SPEC.md sections, in the order the spec fixes =="
  expected=(
    "1. Concept"
    "2. User and problem"
    "3. Why this wins"
    "4. Architecture and stack"
    "5. Tools and human-only verbs"
    "6. Providers"
    "7. Demo script"
    "8. Build plan"
    "9. Owner steps"
    "10. Risks"
    "11. Submission checklist"
  )
  found=()
  while IFS= read -r line; do
    found+=("$line")
  done < <(grep -E '^## ' SPEC.md)

  [ "${#found[@]}" -eq "${#expected[@]}" ] ||
    fail "SPEC.md has ${#found[@]} top-level sections, expected ${#expected[@]}"

  i=0
  while [ "$i" -lt "${#expected[@]}" ]; do
    case "${found[$i]}" in
      "## ${expected[$i]}"*) echo "  ok: ${expected[$i]}" ;;
      *) fail "section $((i + 1)) is '${found[$i]}', expected to start with '## ${expected[$i]}'" ;;
    esac
    i=$((i + 1))
  done

  echo "== the submission checklist is the final section =="
  last="${found[$((${#found[@]} - 1))]}"
  [ "$last" = "## 11. Submission checklist" ] ||
    fail "the last section of SPEC.md is '$last', not the submission checklist"

  echo "== the checklist carries every rule line, verbatim (read live 28 Sep 2026) =="
  checklist="$(awk '/^## 11\. Submission checklist/ { on = 1 } on' SPEC.md)"
  items=(
    "A complete submission must be received before October 10, 2026 at 23:59 IST."
    "Sign in through the Hackathon website using a GitHub account and complete the submission form."
    "Provide a public GitHub repository containing the submitted project, documentation, and sufficient setup or usage instructions."
    "Explain which SerpApi products, APIs, SDKs, search engines, MCP features, or tools the project uses and why that usage matters."
    "Provide the lead participant's name, email address, mobile number, occupation, and years of professional experience."
    "Select one track, state whether the project existed before the Hackathon, and indicate how the participant learned about the event."
    "Provide a publicly accessible demonstration video."
    "List any AI development or content-generation tools used. Omit this field if no AI tools were used."
    "Accept these Rules and the Terms & Conditions."
    "The demo must be a screen recording under three minutes that shows the project running locally and its core functionality working."
    "demonstrates meaningful SerpApi usage"
    "Demo video under three minutes"
    "FINAL CHECK"
  )
  for item in "${items[@]}"; do
    printf '%s\n' "$checklist" | grep -qF -- "$item" ||
      fail "the submission checklist does not carry: $item"
    echo "  ok: ${item:0:70}"
  done

  echo "== every judging criterion is mapped =="
  for c in "Idea strength" "Originality" "Technical complexity" "Usefulness" "Meaningful SerpApi usage"; do
    grep -qF "**$c:**" SPEC.md || fail "SPEC.md does not map the criterion: $c"
    echo "  ok: $c"
  done
fi

echo "== the published tree stands alone =="
published=(README.md .env.example)
for f in docs/*.md; do published+=("$f"); done
if grep -nwE 'SPEC(\.md)?' "${published[@]}"; then
  fail "a published file mentions SPEC, which bin/publish.sh never publishes"
fi
# The monorepo's name is split so this file doesn't itself contain it: bin/ready.sh flags
# every published line that does.
mono_name="hackathons"'-2026'
if grep -nE "entries/[a-z0-9-]+|${mono_name}([^-]|\$)|\.\./\.\./" "${published[@]}"; then
  fail "a published file points into the private monorepo"
fi
echo "  ok: no SPEC or monorepo references in ${published[*]}"

echo "== no unresolved placeholders =="
placeholder_files=("${published[@]}")
[ "$in_monorepo" = true ] && placeholder_files+=(SPEC.md)
if grep -nE 'TODO|TBD' "${placeholder_files[@]}"; then
  fail "a placeholder is left in: ${placeholder_files[*]}"
fi
echo "  ok"

echo "== secrets stay out of the repository =="
grep -qx '\.env' .gitignore || fail ".gitignore does not list .env (publish.sh's fresh repo relies on it)"
grep -qx 'SERPAPI_KEY=' .env.example || fail ".env.example must carry an empty SERPAPI_KEY="
# A SerpApi key is 64 hex characters. Lock-file hashes are sha256 (also 64 hex), so locks,
# the local .env, the virtualenv and the local cache are excluded from the scan.
if grep -rIlE '[0-9a-f]{64}' . \
  --exclude-dir=.git --exclude-dir=.venv --exclude-dir=.cache --exclude-dir=out \
  --exclude='*.lock' --exclude='.env'; then
  fail "a file above contains a 64-hex string, the shape of a SerpApi key"
fi
echo "  ok: .env ignored, example key empty, nothing key-shaped in the tree"

echo "== recorded search content carries its notice =="
if [ -d recordings ]; then
  [ -f recordings/NOTICE.md ] ||
    fail "recordings/ exists without NOTICE.md (third-party search content, not under MIT)"
  echo "  ok: recordings/NOTICE.md"
else
  echo "  skipped: no recordings yet"
fi

echo "== the structural human-only gate is named, not just described =="
grep -qi "never registered as a tool" README.md ||
  fail "README.md no longer states that the human-only verbs are never registered as a tool"

echo "== the privacy statement stays exact =="
# The earlier draft said only the company, role and city leave the machine, which was false:
# the official domain and, for some checks, a recruiter's domain or contact are sent too.
grep -q "What leaves your machine, exactly" README.md ||
  fail "README.md lost its exact statement of what the searches send"
if grep -nE "Only the company, role and city" README.md docs/*.md; then
  fail "a published file repeats the incomplete privacy claim"
fi
echo "  ok"

echo "== setup =="
# make setup's two installs, run directly: macOS's /usr/bin/make can refuse to run until the
# Xcode licence is accepted. The stamp holds the lock and pyproject.toml the venv was built
# from, so a fresh checkout that reuses an installed .venv (newer files, same pins) is
# current without a reinstall.
stamp() { cat requirements-dev.lock pyproject.toml; }
if [ -x .venv/bin/python ] && stamp | cmp -s - .venv/.installed; then
  echo "  ok: .venv is current"
else
  "${PY:-python3.13}" -m venv .venv
  .venv/bin/pip --disable-pip-version-check install --quiet --require-hashes -r requirements-dev.lock
  .venv/bin/pip --disable-pip-version-check install --quiet --no-deps -e .
  stamp >.venv/.installed
  echo "  ok: .venv built from requirements-dev.lock"
fi

echo "== lint =="
.venv/bin/ruff check . || fail "ruff check found problems"
.venv/bin/ruff format --check . || fail "ruff format --check found unformatted files"

echo "== tests =="
.venv/bin/python -m pytest || fail "pytest failed"

echo "offer-checkpost: all checks passed."
