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

## 4. 检查修改

关闭 GDB、移除全部断点和观察点，让 OpenOCD 保持连接。以下检查暂借任务栈下方的 64 字节 RAM，
保存并恢复 CPU 与 RAM，验证寄存器、缓存、AP1 边界访问及 RAM 软件断点；不会写 Flash。
输出目录必须尚不存在；失败时 CPU 保持暂停，恢复现场保存在该目录。

```sh
python3 contrib/ws63/check_core.py --output artifacts/core-check
```

成功时输出各项 `PASS` 和恢复后的运行状态。
`contrib/ws63/check_decoder.py --objdump` 接受 SDK objdump 路径，可离线核对全部短指令访存解码。
`contrib/ws63/gdb_smoke.gdb` 和 `contrib/ws63/gdb_flash_breakpoints.gdb` 用于匹配 SDK blinky 的 GDB 回归；
后者要求启用 Flash 软件断点，会改写应用扇区。执行前按功能参考保存涉及的整个扇区。
日志、备份、转储及生成 ELF 都放在被忽略的 `artifacts/`，不提交到分支。
