# Working habits for Baton agents

Every tool call sends your whole conversation to the model again, so an agent's cost is its number
of calls times the size of its conversation. A developer that read whole files and checked on
long runs grew to 300K-680K tokens in the second photofactory sprint, and calls made above 250K
were most of the cost. These habits keep a conversation small. They are not optional.

## Read what you need

Find the part you need, then read only that part. `cat` is fine for a short file, or when you
really need all of it.

    rg -n "fn detect|struct Fade" src/ | head -20
    sed -n '120,188p' reference/defade.py
    awk '/^def detect/,/^def [a-z]/' reference/defade.py | head -80
    find src -name '*.rs' -newer Cargo.toml | head
    git diff --stat origin/main...HEAD

Compare numbers rather than open images, unless the task is about how something looks.

## Logs go to a file

Send the output of a long test, parity or benchmark run to a log in your working folder, then
read the summary and the failures from it:

    cargo test > "$WORK/test.log" 2>&1; echo "exit $?"; tail -5 "$WORK/test.log"
    rg -n "FAILED|panicked|error\[" "$WORK/test.log" | head -20

## One call for related commands

Chain commands that belong together in one shell call, for example fetch, check out and show
the log, or build, test and print the summary. A separate call for each costs the whole
conversation again each time.

## Wait once

Run a long command in the foreground with a long timeout (up to ten minutes). For anything
longer, start it in the background and make one blocking wait:

    ./run-parity.sh > "$WORK/parity.log" 2>&1 & pid=$!
    wait $pid; echo "exit $?"; tail -5 "$WORK/parity.log"

or wait on a file the run writes when it finishes:

    until [ -f "$WORK/parity.done" ]; do sleep 30; done

Never check on the same run twice.
