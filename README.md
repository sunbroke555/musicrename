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
python3 -m pip install mutagen pymediainfo opencc-python-reimplemented
```

系统还需要安装 MediaInfo，本脚本通过 `pymediainfo` 读取音频信息。

## 用法

```bash
python3 musicrename.py [参数] [目录]
```

示例：

```bash
python3 musicrename.py /path/to/music
python3 musicrename.py --debug /path/to/music
python3 musicrename.py -n /path/to/music
```

如果不传目录，脚本会使用代码里的默认路径。

## 参数说明

`--debug` / `-d`

- 打开调试输出
- 会打印目录扫描、文件重命名、封面补充、目录重命名等过程日志

`--no-process-album` / `-n`

- 不裁剪专辑名里最后一个 `-` 后面的内容
- 默认行为下，如果专辑名里包含 `-`，脚本会只保留最后一个 `-` 前面的部分

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
- `opencc-python-reimplemented`
