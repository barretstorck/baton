#!/usr/bin/env python3
"""Spike #228 Q4 prototype (throwaway, not for merge).

Runs the GitHub App manifest flow from a local CLI with the standard library only:

1. Serves a page on 127.0.0.1 that auto-submits a pre-filled manifest form to
   https://github.com/settings/apps/new?state=<random> (the user clicks "Create").
2. Catches the redirect with ?code=&state= on the same local server.
3. Calls POST /app-manifests/{code}/conversions (no auth needed) and writes
   the PEM to <outdir>/<slug>.pem with mode 0600 and the rest (no secrets) to
   <outdir>/<slug>.json.
4. Prints the install URL; the manifest's setup_url points back at the local
   server, so the installation_id is caught when the user clicks "Install".

Usage: manifest_flow.py <baton|reviewer> <outdir> [--org ORG]
"""
import html
import http.server
import json
import os
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser

API = "https://api." + "github.com"
WEB = "https://" + "github.com"

# Minimal permission sets proposed by the spike (metadata read is implicit).
PERMS = {
    "baton": {"issues": "write", "pull_requests": "write", "contents": "write"},
    "reviewer": {"pull_requests": "write", "contents": "read"},
    "issues-only": {"issues": "write"},  # Q2 control only
}


def manifest(role, base):
    return {
        "name": f"baton-{role}-{secrets.token_hex(3)}" if role != "baton" else f"baton-{secrets.token_hex(3)}",
        "url": "https://github.com/barretstorck/baton",
        "hook_attributes": {"url": "https://example.invalid/unused", "active": False},
        "redirect_url": base + "/created",
        "setup_url": base + "/installed",
        "public": False,
        "default_permissions": PERMS[role],
        "default_events": [],
    }


def convert(code):
    req = urllib.request.Request(
        f"{API}/app-manifests/{code}/conversions", method="POST",
        headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def main():
    role, outdir = sys.argv[1], sys.argv[2]
    org = sys.argv[4] if len(sys.argv) > 4 and sys.argv[3] == "--org" else None
    state = secrets.token_urlsafe(16)
    result = {}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, body):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = dict(urllib.parse.parse_qsl(u.query))
            if u.path == "/":
                target = (f"{WEB}/organizations/{org}/settings/apps/new" if org
                          else f"{WEB}/settings/apps/new")
                m = html.escape(json.dumps(manifest(role, base)))
                self.reply(f'<form id=f method=post action="{target}?state={state}">'
                           f'<input type=hidden name=manifest value="{m}">'
                           '<button>Create the App on GitHub</button></form>'
                           '<script>document.getElementById("f").submit()</script>')
            elif u.path == "/created":
                if q.get("state") != state:
                    self.reply("state mismatch; ignored")
                    return
                app = convert(q["code"])
                os.makedirs(outdir, mode=0o700, exist_ok=True)
                pem = os.path.join(outdir, app["slug"] + ".pem")
                fd = os.open(pem, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(app["pem"])
                keep = {k: app.get(k) for k in ("id", "slug", "client_id", "owner", "permissions")}
                keep["owner"] = (app.get("owner") or {}).get("login")
                keep["key_path"] = pem
                result.update(keep)
                with open(os.path.join(outdir, app["slug"] + ".json"), "w") as f:
                    json.dump(keep, f, indent=2)
                print("created:", json.dumps(keep), flush=True)
                self.send_response(302)
                self.send_header("Location", f"{WEB}/apps/{app['slug']}/installations/new")
                self.end_headers()
            elif u.path == "/installed":
                result["installation_id"] = int(q.get("installation_id", 0))
                print("installed:", q, flush=True)
                self.reply("Installed. You can close this tab.")

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    base = f"http://127.0.0.1:{srv.server_port}"
    print("open:", base, flush=True)
    webbrowser.open(base)
    while "installation_id" not in result:
        srv.handle_request()
    with open(os.path.join(outdir, result["slug"] + ".json"), "w") as f:
        json.dump(result, f, indent=2)
    print("done:", json.dumps(result))


if __name__ == "__main__":
    main()
