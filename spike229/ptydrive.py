"""Drive an interactive claude session in a pty to test a plugin monitor.

Usage: python3 ptydrive.py <spike dir>
Writes the raw terminal output to <spike dir>/logs/pty.out.
"""
import os
import pty
import select
import sys
import time

W = sys.argv[1]
LOG = f"{W}/logs/progress-mon.log"
out = open(f"{W}/logs/pty.out", "wb")

pid, fd = pty.fork()
if pid == 0:
    os.chdir(f"{W}/proj")
    os.environ["TERM"] = "xterm-256color"
    os.environ["COLUMNS"] = "160"
    os.execvp("claude", ["claude", "--model", "haiku", "--plugin-dir", f"{W}/plugin"])


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.5)
        if r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                return
            out.write(data)
            out.flush()


def send(text):
    os.write(fd, text.encode())


pump(15)
send("\r")          # accept a trust dialog if one is shown
pump(8)
send("/spk:sprint")
pump(2)
send("\r")
pump(25)
for i in range(3):
    with open(LOG, "a") as f:
        f.write(f"{time.strftime('%H:%M:%S', time.gmtime())} orchestrator progress line {i}\n")
    pump(15)
pump(20)
send("/exit\r")
pump(10)
try:
    os.kill(pid, 15)
except ProcessLookupError:
    pass
