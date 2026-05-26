import argparse
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import lru_cache
from importlib import import_module
from typing import Optional

THIRD_PARTY_DEPENDENCIES = {
    "opencc": "python -m pip install opencc-python-reimplemented",
    "mutagen": "python -m pip install mutagen",
    "pymediainfo": "python -m pip install pymediainfo",
}


def _raise_missing_dependency(module_name: str, error: Exception):
    install_cmd = THIRD_PARTY_DEPENDENCIES.get(module_name, f"python -m pip install {module_name}")
    raise RuntimeError(
        f"缺少依赖：{module_name}\n"
        f"请先安装：{install_cmd}\n"
        f"原始错误：{error}"
    ) from error


def _require_module(module_name: str):
    try:
        return import_module(module_name)
    except Exception as e:
        _raise_missing_dependency(module_name, e)


mutagen = _require_module("mutagen")
pymediainfo = _require_module("pymediainfo")
FLAC = _require_module("mutagen.flac").FLAC
Picture = _require_module("mutagen.flac").Picture
MediaInfo = pymediainfo.MediaInfo
File = mutagen.File
APIC = _require_module("mutagen.id3").APIC
TPE2 = _require_module("mutagen.id3").TPE2
MP3 = _require_module("mutagen.mp3").MP3
mutagen_mp4 = _require_module("mutagen.mp4")
MP4Cover = mutagen_mp4.MP4Cover
MP4 = mutagen_mp4.MP4

not_process_album = False
DEBUG = False
AUDIO_EXTS = {".m4a", ".flac", ".mp3"}
RENAMABLE_EXTS = AUDIO_EXTS | {".aac", ".alac", ".wav", ".jpg", ".jpeg", ".png", ".cue", ".log"}
COVER_EXTS = {".jpg", ".jpeg", ".png"}
PERFORMER_SPLIT_RE = re.compile(r"\s*(?:/|&|,|，|、|;|；)\s*")
DISC_SUFFIX_RE = re.compile(r"(\(DISC\s*\d+\))", flags=re.I)
CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f]")
TRAILING_DOTS_SPACES_RE = re.compile(r"[ .]+$")
ILLEGAL_FILENAME_TRANSLATION = str.maketrans({
    "\\": "＼",
    "/": "／",
    ":": "：",
    "*": "＊",
    "?": "？",
    '"': "＂",
    "<": "＜",
    ">": "＞",
    "|": "｜",
})


def debug_print(*args, **kwargs):
    if DEBUG:
        print("[DEBUG]", *args, **kwargs)


def _get_t2s_converter():
    """
    Traditional Chinese -> Simplified Chinese converter (OpenCC).
    Uses the pure Python implementation: pip install opencc-python-reimplemented
    """
    opencc = _require_module("opencc")
    for config in ("t2s", "t2s.json"):
        try:
            return opencc.OpenCC(config)
        except FileNotFoundError:
            continue
    return opencc.OpenCC("t2s")


_T2S_LOCAL = threading.local()


def _get_thread_t2s_converter():
    if getattr(_T2S_LOCAL, "conv", None) is None:
        _T2S_LOCAL.conv = _get_t2s_converter()
    return _T2S_LOCAL.conv


@lru_cache(maxsize=8192)
def _to_simplified_cached(text: str) -> str:
    return _get_thread_t2s_converter().convert(text)


def to_simplified(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    if text == "":
        return text
    return _to_simplified_cached(text)


def _iter_dir_files(dir_path: str):
    with os.scandir(dir_path) as entries:
        for entry in entries:
            if entry.is_file():
                yield entry


def _rename_item_if_needed(old_path: str, new_path: str):
    if os.path.abspath(old_path) == os.path.abspath(new_path):
        debug_print(f"文件名未变化，跳过: {old_path}")
        return
    if os.path.exists(new_path):
        # 不覆盖已有文件，但默认提示用户，避免静默漏处理。
        print(f"警告：目标文件已存在，跳过重命名: {old_path} -> {new_path}")
        return
    debug_print(f"重命名文件: {old_path} -> {new_path}")
    os.rename(old_path, new_path)


def _convert_filenames_in_dir(dir_path: str):
    """
    Convert file names (not contents) in a directory from Traditional->Simplified.
    Includes common audio/cover extensions.
    """
    for entry in _iter_dir_files(dir_path):
        _, ext = os.path.splitext(entry.name)
        if ext.lower() not in RENAMABLE_EXTS:
            continue
        new_name = to_simplified(entry.name) or entry.name
        if new_name != entry.name:
            new_path = os.path.join(dir_path, new_name)
            _rename_item_if_needed(entry.path, new_path)


def _split_performers(value: Optional[str]) -> list[str]:
    if not value:
        return []
    normalized = to_simplified(value) or value
    parts = PERFORMER_SPLIT_RE.split(normalized)
    performers = []
    seen = set()
    for part in parts:
        part = part.strip()
        if not part or part in seen:
            continue
        seen.add(part)
        performers.append(part)
    return performers


def _pick_cover_file(dir_path: str) -> Optional[str]:
    for entry in sorted(_iter_dir_files(dir_path), key=lambda item: item.name):
        if os.path.splitext(entry.name)[1].lower() in COVER_EXTS:
            return entry.path
    return None


def _list_audio_files(dir_path: str) -> list[str]:
    return [
        entry.path
        for entry in sorted(_iter_dir_files(dir_path), key=lambda item: item.name)
        if os.path.splitext(entry.name)[1].lower() in AUDIO_EXTS
    ]


def _collect_audio_dirs(root_path: str) -> list[str]:
    audio_dirs = []
    for current_root, _, _ in os.walk(root_path):
        if _list_audio_files(current_root):
            audio_dirs.append(current_root)
    audio_dirs.sort(key=lambda path: path.count(os.sep), reverse=True)
    return audio_dirs


def _detect_cover_format(cover_path: str):
    ext = os.path.splitext(cover_path)[1].lower()
    if ext == ".png":
        return MP4Cover.FORMAT_PNG, "image/png"
    return MP4Cover.FORMAT_JPEG, "image/jpeg"


@dataclass(frozen=True)
class CoverAsset:
    data: bytes
    mime: str
    mp4_format: int


def _load_cover_asset(cover_path: Optional[str]) -> Optional[CoverAsset]:
    if not cover_path:
        return None
    mp4_format, mime = _detect_cover_format(cover_path)
    with open(cover_path, "rb") as f:
        return CoverAsset(data=f.read(), mime=mime, mp4_format=mp4_format)


def _get_media_tracks(filename: str):
    media = MediaInfo.parse(filename)
    general_track = next((track for track in media.tracks if getattr(track, "track_type", "") == "General"), None)
    audio_track = next((track for track in media.tracks if getattr(track, "track_type", "") == "Audio"), None)
    if general_track is None and media.tracks:
        general_track = media.tracks[0]
    if audio_track is None:
        audio_track = general_track
    return general_track, audio_track


def _convert_text_list(values) -> tuple[list, bool]:
    changed = False
    new_values = []
    for value in values:
        if isinstance(value, str):
            new_value = to_simplified(value) or value
            if new_value != value:
                changed = True
            new_values.append(new_value)
        else:
            new_values.append(value)
    return new_values, changed


def _extract_cover_from_audio(audio_path: str, dir_path: str) -> Optional[str]:
    audio = File(audio_path)
    if isinstance(audio, MP4):
        covers = audio.get("covr", [])
        if not covers:
            return None
        cover_data = covers[0]
        imageformat = getattr(cover_data, "imageformat", MP4Cover.FORMAT_JPEG)
        ext = ".png" if imageformat == MP4Cover.FORMAT_PNG else ".jpg"
        cover_path = os.path.join(dir_path, f"cover{ext}")
        with open(cover_path, "wb") as f:
            f.write(bytes(cover_data))
        return cover_path

    if isinstance(audio, MP3):
        if audio.tags is None:
            return None
        pictures = audio.tags.getall("APIC")
        if not pictures:
            return None
        picture = next((p for p in pictures if getattr(p, "type", None) == 3), pictures[0])
        mime = (picture.mime or "").lower()
        ext = ".png" if "png" in mime else ".jpg"
        cover_path = os.path.join(dir_path, f"cover{ext}")
        with open(cover_path, "wb") as f:
            f.write(picture.data)
        return cover_path

    if isinstance(audio, FLAC):
        pictures = [p for p in audio.pictures if p.type == 3] or list(audio.pictures)
        if not pictures:
            return None
        picture = pictures[0]
        mime = (picture.mime or "").lower()
        ext = ".png" if "png" in mime else ".jpg"
        cover_path = os.path.join(dir_path, f"cover{ext}")
        with open(cover_path, "wb") as f:
            f.write(picture.data)
        return cover_path

    return None


def _ensure_cover_file(dir_path: str, audio_files: list[str]) -> Optional[str]:
    cover = _pick_cover_file(dir_path)
    if cover:
        return cover

    for audio_path in audio_files:
        cover = _extract_cover_from_audio(audio_path, dir_path)
        if cover:
            debug_print(f"已从 {audio_path} 提取封面到 {cover}")
            return cover
    return None


def _convert_mp4_tag_value(value):
    """
    Convert MP4 tag value(s) that are text-like.
    MP4 tags are typically str, list[str], or bytes (for freeform atoms).
    Returns (new_value, changed: bool). Non-text values are returned unchanged.
    """
    if isinstance(value, str):
        new = to_simplified(value) or value
        return new, new != value
    if isinstance(value, list):
        changed = False
        new_list = []
        for v in value:
            if isinstance(v, str):
                nv = to_simplified(v) or v
                if nv != v:
                    changed = True
                new_list.append(nv)
            else:
                new_list.append(v)
        return new_list, changed
    if isinstance(value, bytes):
        # Attempt UTF-8 decode; if not decodable, treat as binary and skip.
        try:
            s = value.decode("utf-8")
        except Exception:
            return value, False
        ns = to_simplified(s) or s
        if ns != s:
            return ns.encode("utf-8"), True
        return value, False
    return value, False


def convert_embedded_tags(filename: str) -> bool:
    """
    Convert all embedded text tags (including lyrics) Traditional->Simplified.
    Returns True if any tag was modified.
    """
    audio = File(filename)
    if audio is None:
        return False

    changed = False

    if isinstance(audio, MP4):
        # Skip cover art and other binary-ish fields.
        skip_keys = {"covr"}
        for k in list(audio.keys()):
            if k in skip_keys:
                continue
            v = audio.get(k)
            nv, ch = _convert_mp4_tag_value(v)
            if ch:
                audio[k] = nv
                changed = True
        if changed:
            audio.save()
        return changed

    if isinstance(audio, MP3):
        if audio.tags is None:
            return False
        for frame in audio.tags.values():
            if getattr(frame, "FrameID", "") == "APIC" or not hasattr(frame, "text"):
                continue
            text = frame.text
            if isinstance(text, str):
                new_text = to_simplified(text) or text
                if new_text != text:
                    frame.text = new_text
                    changed = True
                continue
            if isinstance(text, list):
                new_text, local_changed = _convert_text_list(text)
                if local_changed:
                    frame.text = new_text
                    changed = True
        if changed:
            audio.save()
        return changed

    if isinstance(audio, FLAC):
        # FLAC text tags are vorbis comments (list[str] per key).
        if audio.tags:
            for k in list(audio.tags.keys()):
                vals = audio.tags.get(k)
                if not isinstance(vals, list):
                    continue
                new_vals, local_changed = _convert_text_list(vals)
                if local_changed:
                    audio.tags[k] = new_vals
                    changed = True
        if changed:
            audio.save()
        return changed

    # Other formats: best-effort; if tags exist and are str/list[str], convert.
    try:
        tags = audio.tags
    except Exception:
        tags = None
    if tags:
        for k in list(tags.keys()):
            v = tags.get(k)
            if isinstance(v, str):
                nv = to_simplified(v) or v
                if nv != v:
                    tags[k] = nv
                    changed = True
            elif isinstance(v, list):
                new_list, local_changed = _convert_text_list(v)
                if local_changed:
                    tags[k] = new_list
                    changed = True
        if changed:
            audio.save()
    return changed


def do_album_performer(this_path: str) -> str:
    audio_files = _list_audio_files(this_path)
    debug_print(audio_files)
    album_performers = []
    seen_performers = set()
    for audio_file in audio_files:
        general_track, _ = _get_media_tracks(audio_file)
        performers = _split_performers(getattr(general_track, "performer", None))
        debug_print(album_performers, getattr(general_track, "title", None), performers)
        for performer in performers:
            if performer in seen_performers:
                continue
            seen_performers.add(performer)
            album_performers.append(performer)
    if len(album_performers) < 3:
        album_performer = "&".join(album_performers)
    else:
        album_performer = "VA"
    return album_performer


def _normalize_album_name(album_name: Optional[str]) -> str:
    simplified = to_simplified(album_name) or album_name or ""
    cleaned = DISC_SUFFIX_RE.sub("", simplified).strip()
    if not_process_album:
        return cleaned
    if "-" in cleaned:
        return cleaned.rsplit("-", 1)[0].strip()
    return cleaned


@dataclass
class AlbumInfo:
    artist: str
    album: str
    year: str
    bit_depth: Optional[int]
    sample_rate: str
    codec: str
    album_performer: Optional[str] = None

    @classmethod
    def from_file(cls, filename: str, dir_path: str) -> "AlbumInfo":
        general_track, audio_track = _get_media_tracks(filename)
        album_performer = None
        if getattr(general_track, "album_performer", None):
            album_performers = _split_performers(general_track.album_performer)
            artist = "&".join(album_performers) if album_performers else ""
        else:
            artist = do_album_performer(dir_path)
            album_performer = artist

        if len(_split_performers(artist)) >= 3:
            artist = "群星"

        sampling_rate = getattr(audio_track, "sampling_rate", None)
        sample_rate = f"{float(sampling_rate) / 1000:.1f}kHz" if sampling_rate else ""
        codec = (getattr(general_track, "audio_codecs", None) or getattr(audio_track, "format", None) or "").strip()
        year = (getattr(general_track, "recorded_date", None) or "")[:4]

        return cls(
            artist=artist,
            album=_normalize_album_name(getattr(general_track, "album", None)),
            year=year,
            bit_depth=getattr(audio_track, "bit_depth", None),
            sample_rate=sample_rate,
            codec=codec,
            album_performer=album_performer,
        )

    def get_album_performer(self):
        return self.album_performer

    def parse_dir_name_by_mediainfo(self) -> str:
        codec = (self.codec or "").strip()
        if "AAC" in codec.upper() or not self.bit_depth or not self.sample_rate:
            return r"{artist} - {album_name} ({year}) [{audio_codec}]".format(
                artist=self.artist,
                album_name=self.album,
                year=self.year,
                audio_codec=codec,
            )

        return r"{artist} - {album_name} ({year}) [{bit_depth}bit-{sample_rate} {audio_codec}]".format(
            artist=self.artist,
            album_name=self.album,
            year=self.year,
            bit_depth=self.bit_depth,
            sample_rate=self.sample_rate,
            audio_codec=codec,
        )



def sanitize_filename(filename):
    sanitized = filename.translate(ILLEGAL_FILENAME_TRANSLATION)
    sanitized = CONTROL_CHAR_RE.sub("", sanitized)
    sanitized = TRAILING_DOTS_SPACES_RE.sub("", sanitized).strip()
    return sanitized or "未命名"


def _resolve_rename_dst(src_dir: str, dst_dir: str) -> Optional[str]:
    """
    Resolve target directory for renaming:
    - If dst_dir does not exist: rename to dst_dir
    - If dst_dir exists: no-op (skip renaming)
    - If src already equals dst_dir: no-op (return None)

    This makes re-running stable and avoids generating (1)(2)(3)... directories,
    while also avoiding any merge/move behavior.
    """
    src_abs = os.path.abspath(src_dir)
    dst_abs = os.path.abspath(dst_dir)
    if src_abs == dst_abs:
        debug_print(f"目录名未变化，跳过: {src_dir}")
        return None

    if not os.path.exists(dst_abs):
        return dst_abs

    print(f"警告：目标目录已存在，跳过重命名: {src_dir} -> {dst_dir}")
    return None


def insert_cover(filename: str, cover: Optional[CoverAsset], album_performer: Optional[str]):
    audio = File(filename)
    if isinstance(audio, MP4):
        # 先转换所有内嵌文本标签（含歌词）。成功时这里不会重复保存：convert_embedded_tags()
        # 只有当确实有文本变更才会 save()。
        changed = convert_embedded_tags(filename)
        audio = File(filename)
        if cover and "covr" not in audio:
            debug_print(f"正在处理 {filename} 缺少的封面")
            audio["covr"] = [MP4Cover(cover.data, imageformat=cover.mp4_format)]
            debug_print(f"{filename} 封面补充完成")
            changed = True

        if album_performer and "aART" not in audio:
            audio['aART'] = album_performer
            changed = True
        if changed:
            audio.save()

    elif isinstance(audio, MP3):
        changed = convert_embedded_tags(filename)
        audio = MP3(filename)
        if audio.tags is None:
            audio.add_tags()
        if cover and not audio.tags.getall("APIC"):
            debug_print(f"正在处理 {filename} 缺少的封面")
            audio.tags.add(APIC(encoding=3, mime=cover.mime, type=3, desc="Cover", data=cover.data))
            debug_print(f"{filename} 封面补充完成")
            changed = True

        if album_performer and not audio.tags.getall("TPE2"):
            audio.tags.add(TPE2(encoding=3, text=[album_performer]))
            changed = True
        if changed:
            audio.save()

    elif isinstance(audio, FLAC):
        # 先转换所有内嵌文本标签（含歌词）
        changed = convert_embedded_tags(filename)
        audio = File(filename)
        if cover and not any(p.type == 3 for p in audio.pictures):
            debug_print(f"正在处理 {filename} 缺少的封面")
            picture = Picture()
            picture.data = cover.data
            picture.type = 3
            picture.mime = cover.mime
            audio.add_picture(picture)
            debug_print(f"{filename} 封面补充完成")
            changed = True

        if album_performer and "albumartist" not in audio:
            audio["albumartist"] = album_performer
            changed = True
        if changed:
            audio.save()


def dispose_files(file_path: str, src_path: str):
    debug_print(f"开始处理目录: {file_path}")
    # 先把目录内常见文件名（封面/音频/cue等）做繁转简，保证后续处理更一致
    _convert_filenames_in_dir(file_path)

    audio_files = _list_audio_files(file_path)
    debug_print(f"检测到音频文件 {len(audio_files)} 个")
    cover = _load_cover_asset(_ensure_cover_file(file_path, audio_files))
    album_info = AlbumInfo.from_file(audio_files[0], file_path) if audio_files else None
    album_performer = album_info.get_album_performer() if album_info else None

    def _process_one(audio_path: str):
        insert_cover(audio_path, cover, album_performer)

    # 并行处理每首歌的标签转换/补封面（IO + CPU 混合，线程池即可显著提速）
    if audio_files:
        max_workers = min(8, (os.cpu_count() or 4) + 2)
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [ex.submit(_process_one, p) for p in audio_files]
            for fu in as_completed(futures):
                # 触发异常抛出，便于发现个别文件处理失败
                fu.result()

    if album_info is not None:
        dir_name = album_info.parse_dir_name_by_mediainfo()
        debug_print(dir_name)
        dst = os.path.join(src_path, sanitize_filename(dir_name))
        dst = _resolve_rename_dst(file_path, dst)
        if dst is not None:
            debug_print(f"重命名目录: {file_path} -> {dst}")
            os.rename(file_path, dst)


def _parse_args(argv: list[str]) -> str:
    global DEBUG, not_process_album
    parser = argparse.ArgumentParser(description="整理音乐目录并重命名专辑文件夹")
    parser.add_argument("path", nargs="?", default=r"F:\自用", help="要处理的音乐根目录")
    parser.add_argument("-d", "--debug", action="store_true", help="输出调试日志")
    parser.add_argument(
        "-n",
        "--no-process-album",
        action="store_true",
        help="不裁剪专辑名里最后一个 - 后面的内容",
    )
    parsed = parser.parse_args(argv[1:])

    DEBUG = parsed.debug
    not_process_album = parsed.no_process_album
    filepath = parsed.path

    debug_print(f"参数解析结果: filepath={filepath}, not_process_album={not_process_album}")
    debug_print(f"最终处理路径: {filepath}")
    return filepath


def main(argv: Optional[list[str]] = None):
    args = argv or sys.argv
    filepath = _parse_args(args)
    if not os.path.isdir(filepath):
        raise FileNotFoundError(f"目录不存在或不可访问：{filepath}")
    audio_dirs = _collect_audio_dirs(filepath)
    debug_print(f"共发现 {len(audio_dirs)} 个待处理目录")
    for path in audio_dirs:
        dispose_files(path, os.path.dirname(path))


if __name__ == "__main__":
    main()
