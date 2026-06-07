# musicrename

自用音乐重命名脚本。

主要功能：

- 递归扫描目录中的专辑文件夹
- 将文件名和音频标签中的繁体中文转换为简体中文
- 补充缺失的内嵌封面
- 补充缺失的专辑歌手信息
- 按元数据重命名专辑目录

## 依赖

Python 包：

```bash
python3 -m pip install mutagen pymediainfo opencc
```

系统还需要安装 MediaInfo，本脚本通过 `pymediainfo` 读取音频信息。

### 从旧版本迁移（opencc-python-reimplemented → opencc）

本脚本的简繁转换已从纯 Python 的 `opencc-python-reimplemented` 切换到官方的 `opencc`（C++ 内核，更快，零 Python 依赖）。

两个包的导入名都是 `opencc`，不能同时安装，否则会互相覆盖导致行为不确定。升级时务必先卸载旧的，再装新的：

```bash
# 1. 卸载旧的纯 Python 实现
python3 -m pip uninstall -y opencc-python-reimplemented

# 2. 安装官方 opencc
python3 -m pip install opencc
```

验证安装是否成功：

```bash
python3 -c "import opencc; print(opencc.OpenCC('t2s').convert('漢字測試'))"
# 预期输出：汉字测试
```

说明：

- 如果你用的是虚拟环境（venv/conda），请在对应环境里执行上面的命令。
- 官方 `opencc` 在 macOS（Intel/Apple Silicon）、Linux、Windows 的常见 Python 版本上都有预编译 wheel，`pip install opencc` 一般直接装二进制，无需编译。
- 仅当你的平台/Python 版本没有匹配 wheel 时，pip 才会回退到源码构建，此时需要 C++ 编译器（g++ 4.6+ / clang 3.2+）和 CMake。

## 用法

```bash
python3 musicrename.py [参数] [目录]
```

示例：

```bash
python3 musicrename.py /path/to/music
python3 musicrename.py --debug /path/to/music
python3 musicrename.py -n /path/to/music
python3 musicrename.py --converter zhconvert /path/to/music
```

如果不传目录，脚本会使用代码里的默认路径。

## 参数说明

`--debug` / `-d`

- 打开调试输出
- 会打印目录扫描、文件重命名、封面补充、目录重命名等过程日志

`--no-process-album` / `-n`

- 不裁剪专辑名里最后一个 `-` 后面的内容
- 默认行为下，如果专辑名里包含 `-`，脚本会只保留最后一个 `-` 前面的部分

`--converter` / `-c`

- 选择简繁转换后端，可选 `opencc`（默认）或 `zhconvert`
- `opencc`：本地转换，快、离线、确定性，适合批量处理整库
- `zhconvert`：调用繁化姬 API（<https://zhconvert.org>），转换质量更高，但每个待转字符串都要一次网络请求，速度慢很多且依赖联网
- 走 `zhconvert` 时，脚本会强制 `modules={"*":0}`，只做字符级简繁转换，不启用错别字修正、专有名词等模块，保证标签内容可预测
- API 多次失败会自动回退到本地 `opencc`，所以使用此选项时本地仍需装好 `opencc`
- 转换结果带进程内缓存（相同字符串只请求一次）

`--zh-interval`

- 仅在 `--converter zhconvert` 时生效
- 设置两次 API 请求之间的最小间隔秒数，用于限速、避免触发繁化姬的频率限制
- 默认 `0`（不限速）；批量处理大量文件时建议设一个小值，例如 `--zh-interval 0.2`

`[目录]`

- 要处理的音乐根目录
- 脚本会递归查找其中“实际包含音频文件”的目录并处理

## 处理范围

支持的主音频格式：

- `.m4a`
- `.flac`
- `.mp3`

会参与文件名繁转简的常见文件：

- 音频文件
- `.jpg` `.jpeg` `.png`
- `.cue` `.log`
- `.aac` `.alac` `.wav`

## 行为说明

- 只要音频标签被修改，文件的修改时间就会变成当前时间
- 即使歌曲标题是英文，只要别的标签字段里有繁体中文，比如流派、歌词、出版信息，也可能触发写回
- 如果文件本身缺少封面或缺少专辑歌手，脚本也会写回音频文件
- 目录会按从深到浅的顺序处理，避免父目录重命名影响子目录扫描

## 依赖缺失时

脚本现在不会再静默忽略依赖问题。

如果缺少下面这些依赖，会直接报错并提示安装命令：

- `mutagen`
- `pymediainfo`
- `opencc`
