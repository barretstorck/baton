# Local brief

Private to this machine. It lives outside every checkout and is never committed. Agents read it
by its absolute path from any worktree. Nothing from it may appear in anything they publish.

## Hosts

<!-- Names or addresses of machines agents may use, one per line. -->

## Paths

<!-- Working folders on this machine, one `NAME: /absolute/path` per line. -->

## Local data

<!-- One `NAME: /absolute/path` per line: data agents may read in place, read-only, never copy.
     Examples: test footage, reference images. -->

## Sandbox

<!-- Optional. One line `Command: <template>` that runs a command in a sandbox.
     `{checkout}` is the absolute path of a fresh clone of the default branch and `{command}`
     is the command to run; both are substituted already quoted, so write them unquoted.
     The template must contain both. Used by `baton sandbox run` and `baton sandbox check`.
     Isolating the sandbox (network, credentials, file system) is the maintainer's
     responsibility; Baton does not check it.
     Example:
     Command: docker run --rm --network none -v {checkout}:/work -w /work IMAGE sh -c {command} -->

## Credentials

<!-- Agents never read or copy this section. Keep keys and tokens here or in a file the project
     loads itself, never in a worktree. -->
