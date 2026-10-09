# CN 2026 HW2 pre-judge

The pre-judge runs a subset of the checks used for grading. Passing it does
not guarantee full marks; the official judge tests more cases.

## 1. Get the course image

The course image contains the compilers, FFmpeg, Python, Node and browser
tools used by both the pre-judge and the official judge; grading uses exactly
this image:

```shell
docker pull cnta/cn-hw2:2026
```

## 2. Develop on your computer

Get the homework template from https://github.com/ntucn/cn2026-hw2, keep your
repository on your computer, and edit it with any editor. To build and run
inside the course environment, use its development container (in your
repository root):

```shell
docker compose up -d
docker compose exec dev bash      # opens /home/cnta/hw2 = your ./hw2
make server client
./server 8080                      # browse http://localhost:8080
```

`docker compose down` stops it. Files you create in the container appear in
your `./hw2` folder.

## 3. Run the pre-judge

Clone this repository next to yours, then run it from here:

```shell
git clone https://github.com/ntucn/cn2026-hw2-prejudge.git
cd cn2026-hw2-prejudge
./run-in-docker.sh ../<your-repository> [output-directory]
```

It mounts your repository read-only into a one-shot container without
network access, copies it inside, builds `server` and `client` with
`make server` and `make client` (or plain `make` once if your makefile has no
such targets), and runs the checks. Your files are never
changed, and the container is removed afterwards. Results and logs go to
`./output` (or the directory you give, which must be outside your repository):

- `structure.json` / `structure.log`: repository structure findings
- `make-server.log`, `make-client.log`: compiler output
- `startup-*.json`: whether each server started listening

`run-in-docker.sh` is the recommended way. If you already work in a shell of
the course image that can see both this repository and yours, `./run.sh
../<your-repository>` runs the same checks on a private copy and never stops
your other programs.

## 4. Reading the summary

```
structure-check                          :   pass
build                       (server):   pass
build                       (client):   pass
server-test                 (server-0.py):   pass
...
```

- `pass` / `failed`: the check ran and your program passed or failed it.
- `failed (compile failed)`: `make` did not produce the program; the checks
  that need it are shown as `failed (not run: ...)`.
- `needs_review`: for example `make` reported an error but still produced the
  program, or the structure check found files the specification does not
  allow. There is no automatic deduction; fix them before submitting.
- `incomplete`: the pre-judge itself could not finish a check (for example a
  port was already in use, or a tool failed). This is not a failure of your
  program; read the message, fix the cause, and run again.

The exit code is 0 when everything passed, 1 when something failed or needs
review, and 2 when something could not be checked.

If you have questions, email `ntu.cnta@gmail.com`.
