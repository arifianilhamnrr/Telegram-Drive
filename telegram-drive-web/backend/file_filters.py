"""Kategori file untuk filter daftar (foto / video / dokumen)."""
from __future__ import annotations

PHOTO_EXTENSIONS = frozenset({
    "jpg", "jpeg", "jpe", "jfif", "png", "gif", "webp", "bmp", "dib",
    "tiff", "tif", "heic", "heif", "hif", "avif", "svg", "svgz", "ico",
    "cur", "ani", "apng", "jxl", "jp2", "j2k", "jpf", "jpm", "jpx",
    "psd", "psb", "ai", "eps", "raw", "cr2", "cr3", "nef", "nrw", "arw",
    "srf", "sr2", "orf", "rw2", "pef", "ptx", "raf", "dng", "x3f", "kdc",
    "dcr", "mrw", "mos", "erf", "3fr", "mef", "iiq", "rwl", "srw",
})

VIDEO_EXTENSIONS = frozenset({
    "mp4", "m4v", "mov", "qt", "avi", "mkv", "mk3d", "mka", "webm",
    "3gp", "3g2", "3gpp", "3gpp2", "flv", "f4v", "wmv", "asf", "mpg",
    "mpeg", "mpe", "mpv", "m2v", "ts", "mts", "m2ts", "vob", "ogv",
    "ogm", "divx", "xvid", "rm", "rmvb", "amv", "mxf", "mod", "tod",
    "dat", "swf", "insv", "lrv",
})

DOCUMENT_EXTENSIONS = frozenset({
    "pdf", "doc", "docx", "dot", "dotx", "rtf", "odt", "ott",
    "xls", "xlsx", "xlsm", "xlsb", "xltx", "xltm", "xlt", "ods", "ots",
    "csv", "tsv", "ppt", "pptx", "pptm", "potx", "pot", "pps", "ppsx",
    "odp", "otp", "txt", "text", "md", "markdown", "rst", "tex", "latex",
    "epub", "mobi", "azw", "azw3", "fb2", "djvu", "djv", "chm",
    "pages", "numbers", "key", "keynote", "one", "onenote",
    "html", "htm", "xhtml", "xml", "json", "yaml", "yml", "toml",
    "ini", "cfg", "conf", "log", "rtfd", "wpd", "wps", "abw", "zabw",
    "sxw", "stw", "sxc", "stc", "sxi", "sti", "odg", "otg",
    "pub", "vsd", "vsdx", "vss", "vst", "vdx", "vtx", "vssx", "vstx",
    "msg", "eml", "mbox", "ics", "vcf", "fdf", "xfdf",
    "dwg", "dxf", "dwt", "dws", "dwf", "dwfx",
    "odf", "odb", "odc", "odf", "odg", "odi", "odm", "odp", "ods", "odt",
    "pages-tef", "numbers-tef", "key-tef",
})

FILTER_TYPES = frozenset({"all", "photo", "video", "document"})

LIST_SCAN_LIMIT = 8000
DEFAULT_PER_PAGE = 24
MAX_PER_PAGE = 100


def normalize_filter_type(value: str | None) -> str:
    t = (value or "all").strip().lower()
    if t in ("foto", "gambar", "image", "images"):
        return "photo"
    if t in ("vid", "videos"):
        return "video"
    if t in ("doc", "docs", "dokumen", "documents"):
        return "document"
    if t not in FILTER_TYPES:
        return "all"
    return t


def file_category(kind: str, mime: str, ext: str) -> str:
    """Kategori utama: photo | video | document | audio | other."""
    k = (kind or "").lower()
    m = (mime or "").lower()
    e = (ext or "").lower().lstrip(".")

    if k == "image" or e in PHOTO_EXTENSIONS or m.startswith("image/"):
        return "photo"
    if k == "video" or e in VIDEO_EXTENSIONS or m.startswith("video/"):
        return "video"
    if k == "audio" or m.startswith("audio/") or e in (
        "mp3", "wav", "flac", "ogg", "oga", "m4a", "aac", "wma", "opus", "aiff", "aif", "amr", "mid", "midi",
    ):
        return "audio"
    if e in DOCUMENT_EXTENSIONS or m in (
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.presentation",
        "text/plain",
        "text/csv",
        "text/html",
        "text/markdown",
    ) or m.startswith(("text/", "application/rtf")):
        return "document"
    if k == "file" and e and e not in PHOTO_EXTENSIONS and e not in VIDEO_EXTENSIONS:
        return "document"
    return "other"


def matches_filter(category: str, filter_type: str) -> bool:
    ft = normalize_filter_type(filter_type)
    if ft == "all":
        return True
    return category == ft


def matches_search(name: str, query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    return q in (name or "").lower()