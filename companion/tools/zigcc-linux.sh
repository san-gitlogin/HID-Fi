#!/bin/sh
# C compiler for checking the Linux build from a Mac (cargo check --target
# x86_64-unknown-linux-gnu): Zig ships the libc headers a Mac does not have.
# A real Linux build uses the system's gcc and needs none of this.
# cc-rs adds a Rust-style --target=, which zig does not read: ours replaces it.
for a in "$@"; do
  shift
  case "$a" in --target=*) ;; *) set -- "$@" "$a" ;; esac
done
exec zig cc -target x86_64-linux-gnu "$@"
