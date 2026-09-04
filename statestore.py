"""Persistent state: open structures and the halt flag. Atomic writes so a
crash mid-save never corrupts state; the loop can be killed and restarted at
any time."""
import os

import config as cfg
from models import OpenStructure
from utils import atomic_write_json, read_json


class State:
    def __init__(self, base_dir="."):
        self.dir = os.path.join(base_dir, cfg.STATE_DIR)
        os.makedirs(self.dir, exist_ok=True)
        self.spath = os.path.join(self.dir, "structures.json")
        self.hpath = os.path.join(self.dir, "halt.json")

    def structures(self) -> list[OpenStructure]:
        return [OpenStructure.model_validate(x) for x in read_json(self.spath, [])]

    def save_structures(self, structs):
        atomic_write_json(self.spath, [s.model_dump() for s in structs])

    def halted(self) -> dict:
        return read_json(self.hpath, {"halted": False})

    def set_halt(self, reason: str):
        atomic_write_json(self.hpath, {"halted": True, "reason": reason})
