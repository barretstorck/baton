#!/bin/sh
# Spike #228 Q1/Q2/Q3 probe (throwaway, not for merge). Run by the maintainer after
# manifest_flow.py created and installed the Apps on a scratch repository.
#
#   probe.sh <owner/scratch-repo> <baton.json> <reviewer.json> [<issues-only.json>]
#
# Each JSON is the file manifest_flow.py wrote (id, key_path, installation_id).
# The scratch repo needs: main protected with "Require a pull request" and
# "Require approvals: 1"; the maintainer's gh login is admin.
set -eu
REPO=$1; B=$2; R=$3; I=${4:-}
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
  echo "== Q2: issues-write-only App comments on the PR, then on an issue"
  IT=$(token "$I")
  call "$IT" POST "/repos/$REPO/issues/$PR/comments" '{"body":"Q2 issues-only on PR"}'
fi

echo "== Q1: before approval"
gh pr view "$PR" -R "$REPO" --json reviewDecision,mergeStateStatus
call "$RT" POST "/repos/$REPO/pulls/$PR/reviews" '{"event":"APPROVE","body":"Q1 approve"}'
echo "== Q1: after reviewer[bot] approval (APPROVED + CLEAN means it counts)"
gh pr view "$PR" -R "$REPO" --json reviewDecision,mergeStateStatus,reviews
echo "== Q1 control: baton[bot] approving its own PR (expect 422)"
call "$BT" POST "/repos/$REPO/pulls/$PR/reviews" '{"event":"APPROVE"}'

echo "== attribution fields on the comments"
gh api "repos/$REPO/issues/$PR/comments" -q '.[] | {login: .user.login, type: .user.type, via: .performed_via_github_app.slug}'
