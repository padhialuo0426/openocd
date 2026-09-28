# 构建并连接 WS63

本页面向已开启 SWD 的 BearPi-Pico H3863 开发板，使用 Linux 和 CMSIS-DAP 探针。
功能范围和约束见 [WS63 分支功能参考](ws63.md)。SDK 的环境与固件编译方法参考
[小熊派开发文档](https://www.bearpi.cn/core_board/bearpi/pico/h3863/)。

## 1. 构建本分支

按仓库 [构建说明](../README.md#compiling-openocd)准备 C 编译器、Autotools、pkg-config、
libusb 和 HIDAPI 等依赖。以下命令均从 OpenOCD 仓库根目录执行，生成文件位于本地 `build/`。

```sh
git submodule update --init --recursive
./bootstrap
mkdir -p build
cd build
../configure --enable-cmsis-dap --enable-cmsis-dap-v2 \
  --disable-jlink --disable-internal-libjaylink \
  --disable-doxygen-html --disable-doxygen-pdf
make -j8
cd ..
```

使用 `build/src/openocd` 和同一份源码的 `tcl/`；仅把 `ws63.cfg` 交给未经修改的上游二进制，
无法获得这里的 AP 映射 RISC-V、Flash 和 LiteOS 实现。

## 2. 连接 CPU

探针接 SWDIO、SWCLK 和 GND，确认调试引脚已由固件开启。关闭占用探针的其他程序，
从仓库根目录启动服务；主机需要对应 USB 设备的访问权限。

```sh
mkdir -p artifacts
build/src/openocd -s tcl -f interface/cmsis-dap.cfg \
  -c 'adapter speed 4000; bindto 127.0.0.1' \
  -c 'set WS63_FLASH_BACKUP_DIR artifacts/flash-backups' \
  -f target/ws63.cfg
```

服务提供 GDB 端口 `3333`、Tcl 端口 `6666` 和 telnet 端口 `4444`。
连接后使用 GDB 的 `monitor halt` 暂停，再执行 `monitor flash probe WS63.flash`；
驱动识别正确时输出 `WS63 GD25Q32: 4 MiB, 4096-byte sectors`。

不需要 Flash 驱动时，在加载目标配置前增加 `-c 'set WS63_FLASH 0'`。
允许 Flash 软件断点时，增加 `-c 'set WS63_FLASH_SWBP 1'`；先阅读功能参考中的 Flash 边界。

## 3. 准备 LiteOS 调试 ELF

安装 Python 3 和 `pyelftools`，使用 SDK 自带的 RISC-V objdump、objcopy 和 GDB。
将路径变量替换为实际 SDK 目录；准备工具会从 SDK 目录或 `PATH` 查找 objdump 和 objcopy。
输出 ELF 与配置文件必须尚不存在。

```sh
export WS63_SDK="填入实际的 bearpi-pico_h3863 SDK 路径"
python3 contrib/ws63/prepare_elf.py prepare \
  "$WS63_SDK/output/ws63/acore/ws63-liteos-app/ws63-liteos-app.elf" \
  artifacts/ws63-debug.elf --rtos-config artifacts/ws63-rtos.cfg
```

工具输出补充的展开条目数和配置文件路径。运行固件必须对应输入 ELF；换固件后重新生成。
先停止第 2 节的 OpenOCD，再带上生成的 RTOS 配置启动：

```sh
build/src/openocd -s tcl -f interface/cmsis-dap.cfg \
  -c 'adapter speed 4000; bindto 127.0.0.1' \
  -c 'set WS63_FLASH_BACKUP_DIR artifacts/flash-backups' \
  -f target/ws63.cfg -f artifacts/ws63-rtos.cfg
```

用 SDK GDB 打开 `artifacts/ws63-debug.elf`，执行：

```gdb
set remotetimeout 15
target extended-remote localhost:3333
monitor halt
info registers pc sp
info threads
thread apply all bt 5
```

线程配置必须在 GDB 首次连接前加载，以便初始化线程标识。调试源码时，另按本机 SDK 路径配置
GDB 的 `set substitute-path`。回溯因 ROM 缺少展开信息而停止时，保留已知帧，不将后续猜测视为有效调用链。

## 4. 复位并停在应用入口

以下命令用于已经连接的目标和匹配的应用 ELF。复位会重新执行启动流程；
先按第 5 节保存固件，随后在 GDB 中删除已有断点和观察点，再设置应用入口硬件断点：

```gdb
monitor halt
delete breakpoints
monitor gdb breakpoint_override hard
monitor reset halt
maintenance flush register-cache
hbreak main
continue
info registers pc sp
bt 3
delete breakpoints
monitor gdb breakpoint_override disable
```

`reset halt` 后 CPU 应暂停在 `0x100000`；继续后应停在 ELF 的 `main`。
`maintenance flush register-cache` 避免 GDB 使用复位前的寄存器缓存决定下一次断点或单步操作。
结束时清除强制硬件断点设置，避免影响同一 OpenOCD 服务中的后续调试连接。
启动过程若使 SWD 短暂断开，等待后端重新检查目标；持续无法恢复时检查 SWD 引脚配置和固件。

进入有源码的应用函数后，使用 `step` 进入调用、`next` 跨过调用、`finish` 返回调用者，
或用 `stepi` 执行一条指令。等待 LiteOS 完成初始化后，再查看任务列表和任务栈。
调试结束时删除断点和观察点，再执行 `continue` 恢复运行。

SDK 会在 ELF 链接后生成 ROM 补丁表和签名。需要更换可启动应用时，使用配套签名 BIN，
保留 ELF 供源码调试；不能将直接 `load` 原 ELF 视为完整的固件安装流程。
烧录边界和保护要求见[功能参考的 Flash 部分](ws63.md#32-flash)。

## 5. 保存与核对固件

改写 Flash 或运行会复位的验证前，删除所有断点和观察点，暂停 CPU，再保存当前板卡的完整 Flash 和 ROM。
确认 `artifacts/` 已存在，以下输出文件尚不存在；在 Tcl 或 telnet 控制台执行：

```tcl
halt
ws63_ap1_dump artifacts/before-flash.bin 0x200000 0x400000
ws63_ap1_dump artifacts/before-rom.bin 0x100000 0x4c000
```

Flash 文件应为 4,194,304 字节，ROM 文件应为 311,296 字节。回到主机终端，记录校验值：

```sh
sha256sum artifacts/before-flash.bin artifacts/before-rom.bin
```

恢复固件后，先让它启动并运行，再暂停 CPU，导出另一组文件：

```tcl
halt
ws63_ap1_dump artifacts/after-flash.bin 0x200000 0x400000
ws63_ap1_dump artifacts/after-rom.bin 0x100000 0x4c000
```

在主机终端逐字节比较：

```sh
cmp artifacts/before-flash.bin artifacts/after-flash.bin
cmp artifacts/before-rom.bin artifacts/after-rom.bin
```

两条命令均无输出且退出码为 0 时，文件一致。若有差异，保持目标暂停，核对并恢复所有受影响扇区，
包括固件启动时可能改写的配置区和升级区；仅恢复应用区不能证明完整固件已恢复。
另行核对 Flash 保护状态，确认没有遗留断点和观察点，再恢复运行。
日志、测试脚本、备份、转储及生成 ELF 保留在被忽略的 `artifacts/`，不加入提交。
