"""Where TagPup keeps its settings and libraries, and what the settings say.

config.ini was read in 26 places, each with its own idea of where the file is and what
a missing value means. Most looked beside the code; metadata.py looked in whatever
folder the program was started from, and read it as cp1252. A relative data_dir was
resolved against the code in some places and the working directory in others, and
runner.py ignored data_dir altogether. ExifTool had three different fallbacks. Every
read now comes here; tests/test_config_single_owner.py fails the build on one that
does not.

TAGPUP_HOME names the folder that holds config.ini and that relative paths in it are
resolved against. Unset, it is the folder the code is in, which is where config.ini and
data/ have always been. Tests set it to a folder of their own, so nothing they do
reaches the config of the app somebody is using.

The file is read fresh on every call. It is small, and the servers change it while they
run: selecting a library remembers it for next time.
"""
import configparser
import os
import platform
import shutil
import threading

CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: A setting config.ini does not give: the project's settings, as config.example.ini
#: gives them (tests/test_config.py keeps the two in step). config.ini is not in git, so
#: a checkout can lack one, and these must then be the settings its libraries were built
#: with -- the CLI's index clears every embedding when the model's dimensions differ.
DEFAULTS = {
    "paths": {
        "data_dir": "data",
        "default_db": "photo_index.db",
    },
    "model": {
        "name": "ViT-H-14",
        "pretrained": "laion2b_s32b_b79k",
        "preserve_full_frame": "true",
        "max_aspect_ratio": "1.4",
        "force_image_size": "512",
    },
    "candidates": {"tags": "Landscape, Portrait, Nature, Urban, Sunset, Sunrise, Night, Ocean, "
                           "Mountain, Forest, Animal, Cat, Dog, Food, Indoor, Outdoor, Vehicle, "
                           "Flower, Architecture, Party, Wedding, Beach, Sports, Concert"},
    "faces": {
        "min_face_size": "20",
        "confidence_threshold": "0.85",
        "mtcnn_thresholds": "0.6, 0.7, 0.7",
    },
    "renaming": {"format": "{grouping} - {index} - {caption}"},
}

_write_lock = threading.Lock()


def home():
    """The folder holding config.ini, which relative paths in it are resolved against."""
    return os.path.abspath(os.environ.get("TAGPUP_HOME") or CODE_ROOT)


def config_path(folder=None):
    """The config.ini of a TagPup home: this one, or `folder` (a sandbox being built)."""
    return os.path.join(folder or home(), "config.ini")


def load():
    """The settings: config.ini over DEFAULTS, so every setting has a value."""
    settings = configparser.ConfigParser(interpolation=None)
    settings.read_dict(DEFAULTS)
    if os.path.exists(config_path()):
        settings.read(config_path(), encoding="utf-8")
    return settings


def read_file(folder=None):
    """A home's config.ini as written, without DEFAULTS: for changing and writing back."""
    settings = configparser.ConfigParser(interpolation=None)
    if os.path.exists(config_path(folder)):
        settings.read(config_path(folder), encoding="utf-8")
    return settings


def write_file(settings, folder=None):
    """Write a home's config.ini whole, or not at all, with LF line endings.

    `settings` is a ConfigParser or a {section: {key: value}} dict. Written beside the
    target and swapped in, so a reader never sees half a file.
    """
    if isinstance(settings, dict):
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_dict(settings)
        settings = parser
    target = config_path(folder)
    temporary = target + ".writing"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        settings.write(handle)
    os.replace(temporary, target)


def remember_library(db_name):
    """Make this the library to open next time: paths.default_db, and nothing else."""
    with _write_lock:
        settings = read_file()
        if not settings.has_section("paths"):
            settings.add_section("paths")
        settings.set("paths", "default_db", db_name)
        write_file(settings)


def resolve(value):
    """A path setting as an absolute path. Relative means relative to home(), never to
    the folder the program happened to be started from."""
    expanded = os.path.expandvars(os.path.expanduser(value.strip()))
    return expanded if os.path.isabs(expanded) else os.path.join(home(), expanded)


def data_dir(settings=None):
    """The folder the libraries are in."""
    return resolve((settings or load()).get("paths", "data_dir"))


def default_db(settings=None):
    """The file name of the library to open, such as photo_index.db."""
    return (settings or load()).get("paths", "default_db").strip()


def library_path(db_name, settings=None):
    """Where the library with that file name lives."""
    return os.path.join(data_dir(settings), db_name)


def default_exiftool():
    """Where ExifTool's Windows installer puts it, or the one on PATH elsewhere."""
    if platform.system() == "Windows":
        return os.path.join(os.environ.get("USERPROFILE", "C:\\Users\\Username"),
                            r"AppData\Local\Programs\ExifTool\exiftool.exe")
    return shutil.which("exiftool") or "/usr/bin/exiftool"


def exiftool_path(settings=None):
    """The ExifTool to run: the configured one if it exists, else one on PATH.

    If neither exists, the configured one, so that the error names what was asked for.
    """
    configured = (settings or load()).get("paths", "exiftool", fallback="").strip()
    path = os.path.expandvars(configured) if configured else default_exiftool()
    if os.path.exists(path):
        return path
    return shutil.which("exiftool") or path


def embedder_settings(settings=None):
    """The keyword arguments ClipEmbedder takes from the config."""
    settings = settings or load()
    size = settings.get("model", "force_image_size").strip()
    return {
        "model_name": settings.get("model", "name"),
        "pretrained": settings.get("model", "pretrained"),
        "preserve_full_frame": settings.getboolean("model", "preserve_full_frame"),
        "max_aspect_ratio": settings.getfloat("model", "max_aspect_ratio"),
        "force_image_size": int(size) if size else None,
    }


def candidate_tags(settings=None):
    """The zero-shot tags config.ini lists, in order."""
    listed = (settings or load()).get("candidates", "tags")
    return [tag.strip() for tag in listed.split(",") if tag.strip()]


def face_settings(settings=None):
    """Face detection thresholds. A threshold list that does not parse gets the default."""
    settings = settings or load()
    try:
        thresholds = [float(x) for x in settings.get("faces", "mtcnn_thresholds").split(",")]
    except ValueError:
        thresholds = [float(x) for x in DEFAULTS["faces"]["mtcnn_thresholds"].split(",")]
    return {
        "min_face_size": settings.getint("faces", "min_face_size"),
        "confidence_threshold": settings.getfloat("faces", "confidence_threshold"),
        "mtcnn_thresholds": thresholds,
    }


def rename_format(settings=None):
    """The pattern Smart Rename names photos with."""
    return (settings or load()).get("renaming", "format")
