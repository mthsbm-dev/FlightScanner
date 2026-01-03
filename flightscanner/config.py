import configparser
from pathlib import Path


def load_config(path: str | Path = "config.ini") -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read(path)
    return cfg
