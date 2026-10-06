#!/usr/bin/env bash
# Bench for dz-clean-worktrees: builds throwaway repositories with one worktree
# per situation and checks how wt_scan classifies them and what wt_remove does.
# Touches nothing outside its mktemp sandbox.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
S="$HERE/../scripts"
SB="$(mktemp -d)"; SB="$(cd "$SB" && pwd -P)"
SLEEPER=""
cleanup() { [ -n "$SLEEPER" ] && kill "$SLEEPER" 2>/dev/null; rm -rf "$SB"; }
trap cleanup EXIT
FAILS=0
ok()  { printf 'ok   %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; FAILS=$((FAILS + 1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
export GIT_CONFIG_GLOBAL="$SB/gitconfig" GIT_CONFIG_NOSYSTEM=1
git config --global init.defaultBranch main
git config --global protocol.file.allow always
git config --global commit.gpgsign false
commit() { echo "$3" > "$1/$2"; git -C "$1" add "$2"; git -C "$1" -c commit.gpgsign=false commit -qm "$4"; }

ROOT="$SB/dev"; mkdir -p "$ROOT"
git init -q --bare "$SB/remote.git"
R="$ROOT/app"; git clone -q "$SB/remote.git" "$R" 2>/dev/null
commit "$R" base.txt base "initial"
git -C "$R" push -q origin main
git -C "$R" remote set-head origin main
# Most cases sit where forgotten worktrees usually do: hidden inside the repository.
WT="$R/.worktrees"; mkdir -p "$WT"; echo .worktrees/ >> "$R/.git/info/exclude"

# merged: branch merged with a merge commit and pushed
git -C "$R" worktree add -q -b feat/merged "$WT/merged"
commit "$WT/merged" a.txt a "feat: merged work"
git -C "$R" merge -q --no-ff -m "Merge feat/merged" feat/merged && git -C "$R" push -q origin main

# squashed: same change squashed onto main, branch has two commits
git -C "$R" worktree add -q -b feat/squash "$WT/squash" main
commit "$WT/squash" b.txt b1 "feat: squash part 1"
commit "$WT/squash" b.txt b2 "feat: squash part 2"
git -C "$R" merge -q --squash feat/squash >/dev/null && git -C "$R" commit -qm "feat: squash (#1)" && git -C "$R" push -q origin main

# rebased: commit cherry-picked onto main
git -C "$R" worktree add -q -b feat/rebased "$WT/rebased" main
commit "$WT/rebased" c.txt c "feat: rebased work"
git -C "$R" cherry-pick -x feat/rebased >/dev/null && git -C "$R" push -q origin main

# unmerged
git -C "$R" worktree add -q -b feat/open "$WT/open" main
commit "$WT/open" d.txt d "feat: still open"

# dirty: merged but with an untracked file
git -C "$R" worktree add -q -b feat/dirty "$WT/dirty" main
echo x > "$WT/dirty/scratch.txt"

# no commits
git -C "$R" worktree add -q -b claude/idle "$WT/idle" main

# locked
git -C "$R" worktree add -q -b feat/locked "$WT/locked" main
git -C "$R" worktree lock --reason "on a USB disk" "$WT/locked"

# in use by a process
git -C "$R" worktree add -q -b feat/busy "$WT/busy" main
(cd "$WT/busy" && exec sleep 600) & SLEEPER=$!; disown

# ignored .env is reported
git -C "$R" worktree add -q -b feat/env "$WT/env" origin/main
echo .env.local >> "$(git -C "$R" rev-parse --path-format=absolute --git-common-dir)/info/exclude"
echo SECRET=1 > "$WT/env/.env.local"

# prunable: directory deleted by hand
git -C "$R" worktree add -q -b feat/gone "$WT/gone" main
rm -rf "$WT/gone"

# placement: a managed folder (parent named *-wt) is kept by default; a worktree
# itself named *-wt is not managed; tmp/ is hidden
mkdir -p "$ROOT/app-wt" "$ROOT/tmp"
git -C "$R" worktree add -q -b feat/managed "$ROOT/app-wt/managed" main
git -C "$R" worktree add -q -b feat/managed-gone "$ROOT/app-wt/managed-gone" main; rm -rf "$ROOT/app-wt/managed-gone"
git -C "$R" worktree add -q -b feat/solo "$ROOT/solo-wt" main
git -C "$R" worktree add -q -b feat/tmp "$ROOT/tmp/in-tmp" main
echo /worktrees/ >> "$R/.git/info/exclude"
git -C "$R" worktree add -q -b feat/inrepo "$R/worktrees/in-repo" main

# outside ROOT: must not be listed
git -C "$R" worktree add -q -b feat/outside "$SB/outside" main

# submodules: one pushed (removable, needs --force), one with a local-only commit (blocked)
git init -q --bare "$SB/lib.git"; L="$SB/lib-src"; git clone -q "$SB/lib.git" "$L" 2>/dev/null
commit "$L" lib.txt 1 "lib 1"; git -C "$L" push -q origin main
git -C "$R" submodule add -q "$SB/lib.git" lib 2>/dev/null; git -C "$R" commit -qm "add lib"; git -C "$R" push -q origin main
for n in subok subbad; do
  git -C "$R" worktree add -q -b "feat/$n" "$WT/$n" main
  git -C "$WT/$n" submodule update -q --init 2>/dev/null
done
commit "$WT/subbad/lib" lib.txt 2 "lib local"

# merged locally only (last: later pushes of main would publish it)
git -C "$R" worktree add -q -b feat/localonly "$WT/localonly" main
commit "$WT/localonly" e.txt e "feat: local only"
git -C "$R" merge -q --ff-only feat/localonly

check "a nested worktree path is not the main checkout" "[ -d '$WT/merged' ]"

python3 "$S/wt_scan.py" "$ROOT" --json > "$SB/scan.json" 2> "$SB/scan.err"
check "scan exits cleanly" "[ ! -s '$SB/scan.err' ]"
state() { python3 - "$SB/scan.json" "$1" <<'PY'
import json, sys
rows = {r["path"].rsplit("/", 1)[-1]: r for r in json.load(open(sys.argv[1]))["worktrees"]}
r = rows.get(sys.argv[2])
print("absent" if r is None else "%s %s" % (r["state"], "removable" if r["removable"] else "kept"))
PY
}
expect() { local got; got="$(state "$1")"; check "$1 → $2 (got: $got)" "[ '$got' = '$2' ]"; }
expect merged    "merged removable"
expect squash    "merged removable"
expect rebased   "merged removable"
expect open      "unmerged kept"
expect dirty     "no-commits kept"
expect idle      "no-commits removable"
expect locked    "no-commits kept"
expect busy      "no-commits kept"
expect localonly "merged-local removable"
expect env       "no-commits removable"
expect gone      "prunable removable"
expect outside   "absent"

field() { python3 -c 'import json,sys; r=[r for r in json.load(open(sys.argv[1]))["worktrees"] if r["path"].endswith("/"+sys.argv[2])][0]; print(r[sys.argv[3]])' "$SB/scan.json" "$1" "$2"; }
check "managed folder → keep"          "[ \"\$(field managed placement) \$(field managed recommend)\" = 'managed keep' ]"
check "managed but gone → delete"      "[ \"\$(field managed-gone recommend)\" = delete ]"
check "worktree named *-wt → delete"   "[ \"\$(field solo-wt placement) \$(field solo-wt recommend)\" = 'other delete' ]"
check "inside the repo → hidden"       "[ \"\$(field merged placement) \$(field merged recommend)\" = 'hidden delete' ]"
check "inside the repo, no dot → hidden" "[ \"\$(field in-repo placement)\" = hidden ]"
check "under tmp/ → hidden"            "[ \"\$(field in-tmp placement)\" = hidden ]"
check "blocked is never recommended"   "[ \"\$(field open recommend)\" = keep ]"
python3 "$S/wt_scan.py" "$ROOT" --managed-suffix '' --json > "$SB/nosuffix.json"
check "--managed-suffix '' disables it" "grep -q '\"placement\": \"other\"' '$SB/nosuffix.json' && ! grep -q '\"placement\": \"managed\"' '$SB/nosuffix.json'"
expect subok     "no-commits removable"
expect subbad    "no-commits kept"

how() { python3 -c 'import json,sys; r=[r for r in json.load(open(sys.argv[1]))["worktrees"] if r["path"].endswith("/"+sys.argv[2])][0]; print(r["merge"].get("how"))' "$SB/scan.json" "$1"; }
check "squash detected as squash-merged" "[ \"\$(how squash)\" = squashed ]"
check "cherry-pick detected as rebased"  "[ \"\$(how rebased)\" = rebased ]"
check "busy names the process"   "grep -q '\"in use: sleep' '$SB/scan.json'"
check "env warning names .env.local" "grep -q 'ignored env files.*\\.env\\.local' '$SB/scan.json'"
check "subbad blocker names the submodule" "grep -q 'submodule lib: HEAD .* is on no remote branch' '$SB/scan.json'"
check "local-only merge warns" "grep -q 'merged only into local main' '$SB/scan.json'"
check "own commit subjects listed" "grep -q 'feat: squash part 2' '$SB/scan.json'"

python3 "$S/wt_scan.py" "$ROOT" > "$SB/scan.txt"
check "text listing has sizes and summary line" "grep -q 'recommended for deletion (' '$SB/scan.txt' && grep -q 'feat: merged work' '$SB/scan.txt'"
check "text listing has a managed section" "grep -q 'recommended: keep (managed folder)' '$SB/scan.txt'"

# ---- removal -----------------------------------------------------------------
python3 "$S/wt_remove.py" "$WT/merged" > "$SB/dry.txt"
check "dry run leaves the worktree" "[ -d '$WT/merged' ] && grep -q 'WOULD REMOVE' '$SB/dry.txt'"

python3 "$S/wt_remove.py" --yes "$WT/merged" "$WT/squash" "$WT/gone" "$WT/subok" > "$SB/rm.txt" 2>&1; rc=$?
check "removal exits 0" "[ $rc -eq 0 ]"
check "merged dir gone"  "[ ! -e '$WT/merged' ]"
check "squash dir gone"  "[ ! -e '$WT/squash' ]"
check "submodule worktree removed" "[ ! -e '$WT/subok' ]"
check "branches deleted" "! git -C '$R' rev-parse -q --verify feat/merged >/dev/null && ! git -C '$R' rev-parse -q --verify feat/squash >/dev/null"
check "prunable entry pruned" "! git -C '$R' worktree list | grep -q '/gone'"
check "restore hint printed" "grep -q 'restore branch: git -C .* branch feat/squash' '$SB/rm.txt'"

python3 "$S/wt_remove.py" --yes "$WT/open" "$WT/busy" "$R" > "$SB/refuse.txt" 2>&1; rc=$?
check "refusals exit non-zero" "[ $rc -ne 0 ]"
check "unmerged refused"  "[ -d '$WT/open' ] && grep -q 'REFUSE .*/open: unmerged' '$SB/refuse.txt'"
check "busy refused"      "[ -d '$WT/busy' ] && grep -q 'REFUSE .*/busy: in use' '$SB/refuse.txt'"
check "main checkout never removed" "[ -d '$R/.git' ] && grep -q 'SKIP .*app: not a linked worktree' '$SB/refuse.txt'"

# listed as removable, then edited before the operator confirmed: recheck must refuse
echo late > "$WT/idle/late.txt"
python3 "$S/wt_remove.py" --yes "$WT/idle" > "$SB/late.txt" 2>&1
check "change after listing is refused" "[ -f '$WT/idle/late.txt' ] && grep -q 'REFUSE .*dirty' '$SB/late.txt'"

python3 "$S/wt_remove.py" --yes --keep-branch "$WT/rebased" > /dev/null 2>&1
check "--keep-branch keeps it" "[ ! -e '$WT/rebased' ] && git -C '$R' rev-parse -q --verify feat/rebased >/dev/null"

echo
[ "$FAILS" -eq 0 ] && echo "all passed" || { echo "$FAILS failed"; exit 1; }
