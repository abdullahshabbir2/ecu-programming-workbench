"""Filesystem A/B bank model. A verified image is staged before pointer commit."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from .image import validate_image


class PowerLoss(RuntimeError):
    pass


def atomic_write(path, data):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".pending-",dir=path.parent)
    try:
        with os.fdopen(fd,"wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class BankStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True,exist_ok=True)
        self.pointer = self.root/"active.json"
        self.pending = None
        if self.pointer.exists():
            self.read_active()  # Fail closed on corrupt metadata; never silently factory-reset.

    def _metadata(self):
        if not self.pointer.exists():
            return None
        record = json.loads(self.pointer.read_text())
        if set(record) != {"bank","sha256","version"} or record["bank"] not in ("A","B"):
            raise ValueError("invalid active bank metadata")
        return record

    def read_active(self):
        record = self._metadata()
        if record is None:
            return None
        data = (self.root/(record["bank"]+".bin")).read_bytes()
        version = validate_image(data)
        if version != record["version"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError("active image does not match committed metadata")
        return data

    @property
    def version(self):
        image = self.read_active()
        return validate_image(image) if image else 0

    def stage(self, image, *, failure=None):
        version = validate_image(image)
        if version <= self.version:
            raise ValueError("image version must increase")
        self.pending = None
        active = self._metadata()
        bank = "B" if active and active["bank"] == "A" else "A"
        atomic_write(self.root/(bank+".bin"),image)
        if failure == "after_bank_write":
            raise PowerLoss(failure)
        # Read-back verification before allowing activation.
        if (self.root/(bank+".bin")).read_bytes() != image:
            raise OSError("bank verification failed")
        self.pending = {"bank":bank,"version":version,"sha256":hashlib.sha256(image).hexdigest()}

    def activate(self, *, failure=None):
        if self.pending is None:
            raise ValueError("no verified staged bank")
        data = (self.root/(self.pending["bank"]+".bin")).read_bytes()
        if validate_image(data) != self.pending["version"] or hashlib.sha256(data).hexdigest() != self.pending["sha256"]:
            raise ValueError("staged bank changed before activation")
        if failure == "before_pointer_commit":
            raise PowerLoss(failure)
        atomic_write(self.pointer,json.dumps(self.pending,sort_keys=True).encode())
        self.pending = None
        if failure == "after_pointer_commit":
            raise PowerLoss(failure)

    def abort(self):
        self.pending = None
