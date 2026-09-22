# SPDX-License-Identifier: GPL-2.0-only
# Run with the matching SDK blinky ELF after target extended-remote.
monitor halt
monitor gdb_breakpoint_override hard
info registers pc sp f0
hbreak blinky_cmsis.c:29
continue
bt 3
step
finish
next
stepi
delete breakpoints
monitor reset halt
maintenance flush register-cache
p/x $pc
if $pc != 0x100000
 quit 1
end
hbreak main
continue
p/x $pc
if $pc != &main
 quit 1
end
delete breakpoints
detach
quit
