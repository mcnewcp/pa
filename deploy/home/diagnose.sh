#!/usr/bin/env bash
# Usage: diagnose.sh --referral FILE --name NAME --cut DIR [--eval-set FILE] [--runs LABEL=DIR]...
#          [--wiki LABEL=DIR]... [--earlier DIR]... [pa-eval diagnose options]
#
# Runs a whole diagnosis through the PA VM (ADR-0010), so a development session can ask about
# real content with no hand-off to the owner. A diagnostician's session in the VM answers the
# referral over the case, as the assistant user, under the VM's managed settings and sandbox,
# with exchange capture off. What it left comes back to $PA_DIAGNOSES/<UTC time>-NAME, for the
# owner alone, and the release check runs on the devbox (`pa-eval release`): only checked
# findings reach $PA_RELEASED/<UTC time>-NAME.
#
# The case is a cut (`pa cut` makes one) and whatever else the referral is about, each part named
# by where the devbox holds it:
#   --eval-set FILE    the eval set asked over the cut
#   --runs LABEL=DIR   a run directory of eval runs over the cut, under the referral's label for
#                      its set of runs; repeat it, and one label may have several
#   --wiki LABEL=DIR   a wiki, under the referral's label for it: DIR is the cut, for the cut's
#                      own wiki, or a dreamed copy of it (`librarian.sh dream DIR --cut CUT`)
#   --earlier DIR      an earlier diagnosis's directory, whose private analysis this one builds on
# The cut is pushed into the VM once and reused, as run-evals.sh does; the referral and the other
# parts are pushed each time, replacing the last diagnosis's. Each wiki's history is exported to
# files first (`pa-eval wiki-history`), since the session has no git directory to read. Every
# part is refused inside this checkout, so nothing real is ever committed. Default options:
# --agent claude. The diagnostician's model, effort, turn limit, and timeout are the environment's
# (PA_DIAGNOSTICIAN_MODEL, _EFFORT, _MAX_TURNS, _TIMEOUT_SECONDS), or the options'.
#
# The release check lets findings repeat what the referral holds, so the referral it reads on
# every pass is the devbox's own, as it was when the diagnosis started, and that is the copy kept
# with the diagnosis. One that comes back from the VM changed fails the diagnosis, with nothing
# checked.
#
# It refuses to start while the nightly job runs, whose librarian spends the same usage limit,
# and while another diagnosis runs. It never takes the data root's lock: a cut holds still.
#
# A diagnosis the release check refuses is revised without the owner, at most twice: the refused
# output and the check's notes go back into the VM, a new session there puts right what the
# notes name (`pa-eval diagnose --revise`), and the check runs again on what it left, exactly as
# strict. The notes quote real content, so they go into the VM on stdin and are never printed.
# Every pass, and the check's notes on each, is kept in the diagnosis's private directory.
#
# It prints the diagnosis's name, the session's cost, turns, resumes and revisions, where the
# diagnosis was left, and what the release check reports: counts and names of checks, never
# content. A revised diagnosis prints them once for each pass, the figures every pass's together.
#
# Exit codes: 0, the findings were released. 3, the release check refused them, and refused both
# revisions: nothing is released, and the check's notes are with the private analysis. 4, a
# session failed, ran out of time or turns, or left an output that isn't in form: what it left is
# kept, and it isn't checked. 1, anything else: it didn't start, or the chat inbox changed while
# a session ran, or the referral came back from the VM changed, or the release check couldn't run.
set -Eeuo pipefail
source "$(dirname "$0")/lib.sh"
# Every failure this script doesn't name exits 1: the other codes each mean one thing.
trap 'exit 1' ERR
# Whatever this makes on the devbox is the owner's alone.
umask 077

# labelled OPTION VALUE takes VALUE, a LABEL=DIR, apart into `label` and `path`.
labelled() {
  [[ $2 == ?*=?* ]] || die "$1 takes LABEL=DIR (got $2)"
  label=${2%%=*}
  path=${2#*=}
  # As pa_evals.findings.LABEL has it. A label names a directory in the VM.
  [[ $label =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] ||
    die "$1 $label: a label is lowercase letters, digits, and hyphens, at most 32, starting with a letter or a digit"
}

# run_results DIR prints each run's results.json in the run directory DIR, as a path in it: its
# own, or each run-<n>/'s (pa_evals.comparison.run_files). A case holds no more of a run.
run_results() {
  if [[ -f $1/results.json ]]; then
    echo ./results.json
  else
    (cd "$1" && find . -regex '\./run-[0-9]+/results\.json' -type f | sort)
  fi
}

# push_tree DIR VM_DIR MEMBER... unpacks DIR's members into VM_DIR in the VM, as the assistant.
push_tree() {
  local source=$1 target=$2
  shift 2
  tar -C "$source" -cf - "$@" |
    incus exec "$PA_VM" -- runuser -l "$PA_USER" -c "mkdir -p $target && tar -C $target -xf -"
}

referral=""
short=""
cut=""
eval_set=""
runs=()
wikis=()
earlier=()
args=()
while (($#)); do
  case $1 in
    --referral | --name | --cut | --eval-set | --runs | --wiki | --earlier)
      (($# >= 2)) || die "$1 needs a value"
      option=$1 value=$2
      shift 2
      ;;
    --referral=* | --name=* | --cut=* | --eval-set=* | --runs=* | --wiki=* | --earlier=*)
      option=${1%%=*} value=${1#*=}
      shift
      ;;
    --out | --out=*) die "diagnose.sh says where a diagnosis is left itself: drop --out" ;;
    --history | --history=*)
      die "diagnose.sh exports the history of each --wiki itself: drop --history"
      ;;
    --revise | --revise=* | --notes | --notes=*)
      die "diagnose.sh revises a refused diagnosis itself, by the release check's notes: drop ${1%%=*}"
      ;;
    *)
      args+=("$1")
      shift
      continue
      ;;
  esac
  case $option in
    --referral) referral=$value ;;
    --name) short=$value ;;
    --cut) cut=$value ;;
    --eval-set) eval_set=$value ;;
    --runs) runs+=("$value") ;;
    --wiki) wikis+=("$value") ;;
    --earlier) earlier+=("$value") ;;
  esac
done
[[ -n $referral ]] || die "--referral FILE is needed: the question, in Markdown"
[[ -n $short ]] || die "--name NAME is needed: a short name for the diagnosis"
[[ -n $cut ]] || die "--cut DIR is needed: the cut the question is about (pa cut makes one)"
# The name is the time and this, and pa_evals.diagnosis.NAME holds the whole to 80 characters.
[[ $short =~ ^[A-Za-z0-9][A-Za-z0-9-]{0,62}$ ]] ||
  die "--name is letters, digits, and hyphens, at most 63, starting with a letter or a digit"
[[ " ${args[*]-} " == *" --agent "* || " ${args[*]-} " == *" --agent="* ]] ||
  args=(--agent claude "${args[@]}")

# Every part is refused inside this checkout before any is read.
require_private cut "$cut"
require_private "diagnoses directory" "$PA_DIAGNOSES"
[[ -z $eval_set ]] || require_private "eval set" "$eval_set"
for value in "${runs[@]}"; do
  labelled --runs "$value"
  require_private "run directory under $label" "$path"
done
for value in "${wikis[@]}"; do
  labelled --wiki "$value"
  require_private "wiki under $label" "$path"
done
for path in "${earlier[@]}"; do
  require_private "earlier diagnosis" "$path"
done
# A released directory the release check would refuse (pa_home.config.load_released_directory)
# stops the diagnosis here, before a session is spent on it.
require_private "released directory" "$PA_RELEASED"
released=$(realpath -m -- "$PA_RELEASED")
root=$(realpath -m -- "$PA_DATA_ROOT")
[[ $released/ != "$root"/* ]] ||
  die "the released directory ($released) is inside the data root ($root): set PA_RELEASED_DIR to a directory a development session may read"
[[ $root/ != "$released"/* ]] ||
  die "the released directory ($released) holds the data root ($root): set PA_RELEASED_DIR to a directory a development session may read"

[[ -f $referral ]] || die "referral not found: $referral"
vm_cut=$PA_VM_EVAL_CUTS/$(cut_name "$cut")
# The live store is never a case, only a cut of it: a manifest doesn't make a data root a cut.
if is_live_store "$cut" || [[ -e $cut/wiki.git || $(realpath -- "$cut") == "$(realpath -m -- "$PA_DATA_ROOT")" ]]; then
  die "$cut isn't a cut: it holds what only a live store does. A case is a cut of the store (pa cut takes one), never the store itself"
fi
require_owner_env

# A diagnosis is never written over: its name is the time and the short name.
name=$(date -u +%Y%m%dT%H%M%SZ)-$short
private=$PA_DIAGNOSES/$name
[[ ! -e $private ]] || die "$private already exists: wait a second and retry"
vm_diagnosis=$PA_VM_EVAL_DIAGNOSES/$name
# This diagnosis's own files on the devbox, gone when it ends: the referral as it was asked, each
# wiki's exported history, and what the VM's command printed.
work=$PA_DIAGNOSES/.work-$name
# The referral as it is when the diagnosis starts. It is what goes into the VM, what the release
# check reads on every pass, and what is kept with the diagnosis: never the copy that comes back
# from the VM, which a session there could have changed to have the check excuse what it holds.
asked_referral=$work/referral.md

# The case, as each machine holds its copy: the session is given the VM's (vm_case), and the
# release check the devbox's (devbox_case), part for part. `pushes` is what goes into the VM
# each time: lines of a devbox directory, where it's unpacked in the VM, and the members pushed.
vm_case=(--cut "$vm_cut")
devbox_case=(--cut "$cut")
pushes=()
if [[ -n $eval_set ]]; then
  [[ -f $eval_set ]] || die "eval set not found: $eval_set"
  vm_case+=(--eval-set "$PA_VM_EVAL_CASE/eval-set.json")
  devbox_case+=(--eval-set "$eval_set")
fi
declare -A held=()
for value in "${runs[@]}"; do
  labelled --runs "$value"
  [[ -d $path ]] || die "run directory not found: $path"
  results=$(run_results "$path")
  [[ -n $results ]] || die "$path holds no eval runs: no results.json, and none in a run-<n>/"
  # A label's run directories are numbered as they were given.
  held[$label]=$((${held[$label]-0} + 1))
  vm_runs=$PA_VM_EVAL_CASE/runs/$label/${held[$label]}
  vm_case+=(--runs "$label=$vm_runs")
  devbox_case+=(--runs "$label=$path")
  pushes+=("$path"$'\n'"$vm_runs"$'\n'"$results")
done
declare -A named=()
histories=()
for value in "${wikis[@]}"; do
  labelled --wiki "$value"
  [[ -z ${named[$label]-} ]] || die "--wiki names the label $label twice: a label is one wiki's"
  named[$label]=1
  [[ -d $path/wiki ]] ||
    die "the wiki under $label ($path) has no wiki/: name the cut, for its own wiki, or a dreamed copy of it"
  vm_wiki=$PA_VM_EVAL_CASE/wikis/$label
  if [[ $(realpath -- "$path") == "$(realpath -- "$cut")" ]]; then
    # The cut's own wiki is in the VM with the cut. Its history is the live wiki's, up to the
    # commit the cut holds.
    histories+=("$label"$'\n'--cut$'\n'"$path")
    vm_case+=(--wiki "$label=$vm_cut/wiki")
  else
    [[ -d $path/wiki.git ]] ||
      die "the wiki under $label ($path) isn't a dreamed copy that has run: it has no wiki.git/"
    # A data root holds a wiki and its git directory too, and is never a part of a case.
    if is_live_store "$path" || [[ $(realpath -- "$path") == "$(realpath -m -- "$PA_DATA_ROOT")" ]]; then
      die "the wiki under $label ($path) is a live store, not a dreamed copy: a case holds a cut of the store and copies of the cut's wiki, never the store itself"
    fi
    # Its tree only, never the copy's git directory.
    histories+=("$label"$'\n'--copy$'\n'"$path")
    vm_case+=(--wiki "$label=$vm_wiki/wiki")
    pushes+=("$path"$'\n'"$vm_wiki"$'\n'wiki)
  fi
  vm_case+=(--history "$label=$vm_wiki/history")
  devbox_case+=(--wiki "$label=$path/wiki" --history "$label=$work/history/$label")
  pushes+=("$work/history/$label"$'\n'"$vm_wiki/history"$'\n'.)
done
unset named
declare -A named=()
for path in "${earlier[@]}"; do
  before=$(basename "$(realpath -- "$path")")
  # As pa_evals.diagnosis.NAME has it. A name names a directory in the VM.
  [[ $before =~ ^[A-Za-z0-9][A-Za-z0-9-]{0,79}$ ]] ||
    die "$path isn't named as a diagnosis is: --earlier takes a diagnosis's directory"
  [[ -z ${named[$before]-} ]] || die "--earlier names two diagnoses called $before"
  named[$before]=1
  [[ -f $path/output/analysis.md ]] ||
    die "the earlier diagnosis $before has no private analysis ($path/output/analysis.md)"
  # Its private analysis only: a case holds no more of an earlier diagnosis.
  vm_case+=(--earlier "$PA_VM_EVAL_CASE/earlier/$before")
  devbox_case+=(--earlier "$path")
  pushes+=("$path"$'\n'"$PA_VM_EVAL_CASE/earlier/$before"$'\n'output/analysis.md)
done

# A diagnosis spends the usage limit the librarian and the assistant do, so it stays clear of the
# nightly job, and one runs at a time: it holds a lock on the diagnoses directory until it ends,
# however it ends. Never the data root's lock: it reads a cut, which holds still.
if nightly_running; then
  die "the nightly job is running, and a diagnosis spends the usage limit its librarian does: start it once the job has ended (pa status says when)"
fi
mkdir -p "$PA_DIAGNOSES"
chmod 0700 "$PA_DIAGNOSES"
exec 9<"$PA_DIAGNOSES"
flock -n 9 || die "another diagnosis is running: start this one once it has ended"
# No other diagnosis is running, so whatever one left of its own files was left by one that was
# killed.
rm -rf "$PA_DIAGNOSES"/.work-*
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/history"
cp -- "$referral" "$asked_referral"

# run_session OPTION... runs `pa-eval diagnose` in the VM, as the assistant, with OPTION... after
# the options this script was given, between two listings of the chat inbox (`inbox_before`,
# `inbox_after`). It sets `session` to the command's exit status, shows what the command is
# written to say, and leaves the rest of what it printed in $work/unshown.
run_session() {
  local setting configuration=()
  # The diagnostician's model, effort, turn limit, and timeout are the devbox's to set
  # (pa_home.config.load_diagnostician_config): what its environment gives goes into the VM's,
  # and the rest stays the deployed code's default.
  for setting in "${PA_DIAGNOSTICIAN_SETTINGS[@]}"; do
    [[ -z ${!setting-} ]] || configuration+=("$setting=${!setting}")
  done
  inbox_before=$(inbox_listing)
  session=0
  # A login shell as the assistant starts from its own environment, so exchange capture, which
  # is on only where PA_CAPTURE_EXCHANGES is set (the Remote Control unit), is off.
  as_assistant "$PA_REPO_DIR" "$PA_VM_EVAL_TMP" "${configuration[@]}" "${args[@]}" "$@" \
    >"$work/stdout" 2>"$work/stderr" <<'EOF' || session=$?
set -euo pipefail
cd "$1"
# The session's throwaway copy of the case and its rendered project go in TMPDIR: on the VM's
# disk, since /tmp in the VM is in memory, and outside the assistant's home, whose CLAUDE.md
# files the managed settings exclude.
export TMPDIR=$2
shift 2
# The diagnostician's configuration, as the devbox gave it.
while [[ ${1-} == PA_DIAGNOSTICIAN_*=* ]]; do
  export "$1"
  shift
done
mkdir -p "$TMPDIR"
.venv/bin/pa-eval diagnose "$@"
EOF
  inbox_after=$(inbox_listing)
  # Whatever runs in the VM read real content. Of what its command printed, only what
  # `pa-eval diagnose` is written to say is shown: its counts and its own errors. Where it left
  # the diagnosis in the VM isn't: bring_back says where it is on the devbox.
  local says='^(diagnosis|case|session|resumes|revisions): '
  grep -E "$says" "$work/stdout" || true
  grep -vE "$says|^left: " "$work/stdout" >"$work/unshown" || true
  own_errors
}

# own_errors shows, of what a `pa-eval` command wrote to $work/stderr, the command's own errors,
# which never quote what it read, and adds the rest to $work/unshown: a traceback can quote the
# session's output or the case.
own_errors() {
  local errors='^pa-eval[ a-z-]*: '
  grep -E "$errors" "$work/stderr" >&2 || true
  grep -vE "$errors" "$work/stderr" >>"$work/unshown" || true
}

# keep_unshown COMMAND keeps what `pa-eval COMMAND` printed and wasn't shown ($work/unshown)
# with the diagnosis, for the owner, after what an earlier pass's left there, and says how much
# there is of it.
keep_unshown() {
  [[ -s $work/unshown ]] || return 0
  mkdir -p "$private"
  # Never through a link: the private directory came back from the VM.
  [[ ! -L $private/$1-output.txt ]] || rm -f "$private/$1-output.txt"
  cat "$work/unshown" >>"$private/$1-output.txt"
  log "$(wc -l <"$work/unshown") lines pa-eval $1 printed aren't shown: they are kept in $private/$1-output.txt"
  rm -f "$work/unshown"
}

# bring_back [VM_DIR] pulls the diagnosis from the VM to the private directory, for the owner
# alone, and sets `left` to where it is, or to nothing when the VM's command left nothing: one
# that failed before it started a session. VM_DIR is what a command that got that far made in
# the VM: the diagnosis's own directory, unless one is given.
#
# The VM's copy is the whole diagnosis, every pass with the check's notes on it (a revision
# keeps the pass it revises: pa_evals.diagnosis). So it is pulled beside the devbox's copy and
# takes its place whole. What only the devbox holds, the output of its commands that wasn't
# shown, goes with it.
#
# The referral kept with the diagnosis is the devbox's own (`asked_referral`). One that came back
# changed is kept beside it, for the owner, and sets `referral_changed`: require_session then
# ends the diagnosis.
#
# Nothing here is written through a link the VM's copy holds: a link outside a session's output
# is dropped as it arrives (one in the output is the release check's to refuse).
bring_back() {
  local made=${1:-$vm_diagnosis} pulled=$work/pulled
  left=""
  referral_changed=""
  if incus exec "$PA_VM" -- runuser -l "$PA_USER" -c "test -d $made" </dev/null; then
    rm -rf "$pulled"
    mkdir -p "$pulled"
    incus file pull -r "$PA_VM$vm_diagnosis" "$pulled/"
    find "$pulled" -type l -not -path '*/output/*' -delete
    find "$pulled" -type d -exec chmod 0700 {} +
    find "$pulled" -type f -exec chmod 0600 {} +
    if [[ -e $pulled/$name/referral.md ]] && ! cmp -s "$asked_referral" "$pulled/$name/referral.md"; then
      mv -T "$pulled/$name/referral.md" "$pulled/$name/referral-from-the-vm.md"
      referral_changed=1
    fi
    cp -- "$asked_referral" "$pulled/$name/referral.md"
    if [[ -e $private ]]; then
      find "$private" -maxdepth 1 -type f -name '*-output.txt' -exec mv -t "$pulled/$name" {} +
      mv -T "$private" "$work/replaced"
    fi
    mv -T "$pulled/$name" "$private"
    rm -rf "$work/replaced"
    left=$private
    echo "left: $private"
  fi
  keep_unshown diagnose
}

# require_session ends the diagnosis, with nothing checked or released, unless the session that
# just ran (run_session, then bring_back) left the chat inbox as it was and ended well.
require_session() {
  # Capture is off in the session, so a diagnosis can never become a correction: an inbox that
  # changed while it ran fails it.
  if [[ $inbox_before != "$inbox_after" ]]; then
    diff <(echo "$inbox_before") <(echo "$inbox_after") >&2 || true
    die "the diagnosis's session changed $PA_CHAT_INBOX: nothing is released"
  fi
  log "the diagnosis's session left $PA_CHAT_INBOX unchanged"
  [[ -n $left ]] ||
    die "the diagnosis $name started no session: pa-eval diagnose exited $session in the VM, and nothing is released"
  # The release check lets findings repeat what the referral holds, so a referral changed in the
  # VM is a way round the check. Nothing is checked: this is a failure, never a refusal.
  [[ -z $referral_changed ]] ||
    die "the referral that came back with the diagnosis $name isn't the referral that was pushed: nothing is checked or released. The devbox's own is kept in $private/referral.md, and what came back in $private/referral-from-the-vm.md"
  ((session)) || return 0
  # A session that failed left no record of itself, so there is nothing to check.
  printf 'error: %s\n' "the diagnosis $name failed in its session (pa-eval diagnose exited $session in the VM), and nothing is released: what it left is in $private" >&2
  exit "$PA_DIAGNOSIS_FAILED"
}

# revise N has a new session revise the diagnosis, whose output the release check just refused:
# revision N. It pushes the refused output and the check's notes into the VM, replacing the last
# revision's, runs the revision there over the same referral and case, and brings back what
# it left. The revision keeps the refused pass in the diagnosis's directory, as refused/N.
#
# This is the one place real content goes back into a session, and it goes into the VM, which
# may hold it: the notes say what the check found, so they travel on stdin, never on a command
# line, and nothing here prints them.
revise() {
  local vm_notes=$PA_VM_EVAL_REFUSED/$PA_DIAGNOSIS_NOTES
  log "the release check refused the diagnosis $name: revising the diagnosis by the check's notes (revision $1 of at most $PA_DIAGNOSIS_REVISIONS)"
  incus exec "$PA_VM" -- runuser -l "$PA_USER" -c \
    "rm -rf $PA_VM_EVAL_REFUSED && mkdir -p $PA_VM_EVAL_REFUSED" </dev/null
  push_tree "$private" "$PA_VM_EVAL_REFUSED" output
  push_file "$private/$PA_DIAGNOSIS_NOTES" "$vm_notes"
  run_session "${asked[@]}" --revise "$PA_VM_EVAL_REFUSED/output" --notes "$vm_notes"
  bring_back "$vm_diagnosis/refused/$1"
  require_session
}

# check_release runs the release check on the devbox, never in the VM, over the devbox's copy of
# the case and of the referral, and as the instance's owner, whose names it looks for. It prints what the check
# reports (its counts are its own to say), and sets `checked` to its exit status: 0 released,
# $PA_DIAGNOSIS_REFUSED refused.
check_release() {
  checked=0
  (
    set -a
    # shellcheck source=/dev/null # owner.env, written by the wizard's owner stage
    source "$PA_OWNER_ENV"
    set +a
    export PA_DATA_DIR=$PA_DATA_ROOT
    exec "$PA_EVAL_BIN" release --name "$name" --referral "$asked_referral" "${devbox_case[@]}"
  ) 2>"$work/stderr" || checked=$?
  : >"$work/unshown"
  own_errors
  keep_unshown release
}

for history in "${histories[@]}"; do
  mapfile -t wiki <<<"$history"
  exported=$(PA_DATA_DIR=$PA_DATA_ROOT "$PA_EVAL_BIN" wiki-history "${wiki[1]}" "${wiki[2]}" \
    --out "$work/history/${wiki[0]}" | sed -n 's/^history: //p')
  log "exported the history of the wiki under ${wiki[0]}: $exported"
done

push_cut "$cut"
# The last diagnosis's case goes, whatever modes its files came with.
incus exec "$PA_VM" -- runuser -l "$PA_USER" -c \
  "{ [ ! -e $PA_VM_EVAL_CASE ] || chmod -R u+rwX $PA_VM_EVAL_CASE; } && rm -rf $PA_VM_EVAL_CASE && mkdir -p $PA_VM_EVAL_CASE" </dev/null
push_file "$asked_referral" "$PA_VM_EVAL_CASE/referral.md"
[[ -z $eval_set ]] || push_file "$eval_set" "$PA_VM_EVAL_CASE/eval-set.json"
for push in "${pushes[@]}"; do
  mapfile -t part <<<"$push"
  push_tree "${part[@]}"
done
log "pushed the referral and the rest of the case to $PA_VM_EVAL_CASE"

log "starting the diagnosis $name"
# What every pass is asked: the referral and the case, as the VM holds them.
asked=(--referral "$PA_VM_EVAL_CASE/referral.md" --name "$name"
  --out "$PA_VM_EVAL_DIAGNOSES" "${vm_case[@]}")
run_session "${asked[@]}"
# What a session left is the owner's to read however the diagnosis ends.
bring_back
require_session
# Every pass is checked by the same check over the same case: a revision is held to every rule
# again, and a diagnosis still refused after the last one ends unreleased.
revisions=0
while :; do
  check_release
  case $checked in
    0) break ;;
    "$PA_DIAGNOSIS_REFUSED")
      if ((revisions == PA_DIAGNOSIS_REVISIONS)); then
        # Every pass and the check's notes on each stay in $private.
        printf 'error: %s\n' "the diagnosis $name is still refused after $revisions revisions, and nothing is released: every pass, and the check's notes on each, are kept in $private" >&2
        exit "$PA_DIAGNOSIS_REFUSED"
      fi
      revisions=$((revisions + 1))
      revise "$revisions"
      ;;
    *) die "the release check couldn't run (pa-eval release exited $checked): nothing is released, and the diagnosis is kept in $private" ;;
  esac
done
