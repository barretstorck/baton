#!/bin/sh
# Spike #236 (from #228) Q1/Q2/Q3 probe (throwaway, not for merge). Run by the
# maintainer after manifest_flow.py created and installed the Apps on a scratch repo.
#
#   probe.sh <owner/scratch-repo> <baton.json> <reviewer.json> [<issues-only.json>]
#   probe.sh --q1-recheck <owner/scratch-repo> <reviewer.json> <pr>
#       (after raising the reviewer App to contents: write and accepting it on the
#        installation: re-approve and re-read reviewDecision)
#
# Each JSON is the file manifest_flow.py wrote (id, key_path, installation_id). If
# <baton.json> has user_token_path (manifest_flow.py --user-token), Q3 runs too.
# The scratch repo needs: main protected with "Require a pull request" and
# "Require approvals: 1"; the maintainer's gh login is admin.
set -eu
MODE=full
[ "${1:-}" = --q1-recheck ] && { MODE=recheck; shift; }
REPO=$1
API=https://api.github.com

b64() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }
jwt() {  # $1 = app json
  id=$(jq -r .id "$1"); key=$(jq -r .key_path "$1"); now=$(date +%s)
  h=$(printf '{"alg":"RS256","typ":"JWT"}' | b64)
  p=$(printf '{"iat":%s,"exp":%s,"iss":"%s"}' $((now-60)) $((now+540)) "$id" | b64)
  s=$(printf '%s.%s' "$h" "$p" | openssl dgst -sha256 -sign "$key" -binary | b64)
  printf '%s.%s.%s' "$h" "$p" "$s"
}
token() {  # $1 = app json
  curl -fsS -X POST -H "Authorization: Bearer $(jwt "$1")" -H "Accept: application/vnd.github+json" \
    "$API/app/installations/$(jq -r .installation_id "$1")/access_tokens" | jq -r .token
}
call() {  # $1 token, $2 method, $3 path, $4 body
  curl -sS -o /dev/stderr -w '%{http_code}\n' -X "$2" -H "Authorization: Bearer $1" \
    -H "Accept: application/vnd.github+json" "$API$3" -d "$4"
}

if [ "$MODE" = recheck ]; then
  R=$2; PR=$3; RT=$(token "$R")
  echo "== Q1 recheck: reviewer App permissions now"
  curl -fsS -H "Authorization: Bearer $(jwt "$R")" "$API/app" | jq .permissions
  call "$RT" POST "/repos/$REPO/pulls/$PR/reviews" '{"event":"APPROVE","body":"Q1 approve (contents: write)"}'
  gh pr view "$PR" -R "$REPO" --json reviewDecision,mergeStateStatus,reviews
  exit 0
fi
B=$2; R=$3; I=${4:-}
BT=$(token "$B"); RT=$(token "$R")

echo "== setup: baton[bot] opens a PR"
call "$BT" POST "/repos/$REPO/git/refs" "{\"ref\":\"refs/heads/spike-228\",\"sha\":\"$(gh api repos/$REPO/commits/main -q .sha)\"}"
call "$BT" PUT "/repos/$REPO/contents/spike-228.txt" '{"message":"spike 228","content":"c3Bpa2UK","branch":"spike-228"}'
PR=$(curl -fsS -X POST -H "Authorization: Bearer $BT" "$API/repos/$REPO/pulls" \
  -d '{"title":"spike 228","head":"spike-228","base":"main"}' | jq .number)
echo "PR #$PR"

echo "== Q2: reviewer (pull_requests write, no issues) comments on the PR"
call "$RT" POST "/repos/$REPO/issues/$PR/comments" '{"body":"Q2 reviewer comment"}'
if [ -n "$I" ]; then
  echo "== Q2: issues-write-only App comments on the PR, then on an issue (control)"
  IT=$(token "$I")
  call "$IT" POST "/repos/$REPO/issues/$PR/comments" '{"body":"Q2 issues-only on PR"}'
  ISS=$(gh issue create -R "$REPO" -t "spike 236 control" -b x | sed 's#.*/##')
  call "$IT" POST "/repos/$REPO/issues/$ISS/comments" '{"body":"Q2 issues-only on issue"}'
fi

echo "== Q1: before approval"
gh pr view "$PR" -R "$REPO" --json reviewDecision,mergeStateStatus
call "$RT" POST "/repos/$REPO/pulls/$PR/reviews" '{"event":"APPROVE","body":"Q1 approve"}'
echo "== Q1: after reviewer[bot] approval (APPROVED + CLEAN means it counts)"
gh pr view "$PR" -R "$REPO" --json reviewDecision,mergeStateStatus,reviews
echo "== Q1 control: baton[bot] approving its own PR (expect 422)"
call "$BT" POST "/repos/$REPO/pulls/$PR/reviews" '{"event":"APPROVE"}'

UTP=$(jq -r '.user_token_path // empty' "$B")
if [ -n "$UTP" ]; then
  echo "== Q3: comment written with the baton App's user access token"
  UT=$(cat "$UTP")
  call "$UT" POST "/repos/$REPO/issues/$PR/comments" '{"body":"Q3 user-token comment"}'
fi

echo "== attribution fields on the comments (Q2/Q3 evidence)"
gh api "repos/$REPO/issues/$PR/comments" -q '.[] | {body: .body, login: .user.login, type: .user.type, via: .performed_via_github_app.slug}'

cat <<'NOTE'
== cleanup (by hand, after recording the output above)
  gh repo delete <scratch-repo> --yes            # needs the delete_repo scope
  delete each App: Settings > Developer settings > GitHub Apps > <app> > Advanced
  rm -rf <outdir>                                 # PEMs and the user token
  for s in <slugs>; do gh api apps/$s >/dev/null 2>&1 && echo "$s still exists" || echo "$s gone (404)"; done
NOTE
