from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union
import xml.etree.ElementTree as ET

from .container import CompoundFile
from .models import GXWFormatError


@dataclass(frozen=True)
class GXWLogicalFile:
    logical_name: str
    stream_name: str


class GXWProjectResolver:
    """Resolve logical GX Works2 project objects to streams in the nested ``_hdb`` CFB."""

    def __init__(self, outer: CompoundFile) -> None:
        self.outer = outer
        try:
            nested_data = outer.read_stream("_hdb")
        except KeyError as exc:
            raise GXWFormatError("GXW project does not contain the _hdb stream") from exc
        self.hdb = CompoundFile(nested_data, source=outer.source)
        self._logical_to_stream = self._load_project_data_map()

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "GXWProjectResolver":
        return cls(CompoundFile.from_file(path))

    def _load_project_data_map(self) -> Dict[str, str]:
        try:
            xml_bytes = self.outer.read_stream("projectdatalist.xml")
        except KeyError as exc:
            raise GXWFormatError("GXW projectdatalist.xml is missing") from exc
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as exc:
            raise GXWFormatError(f"invalid projectdatalist.xml: {exc}") from exc

        mapping: Dict[str, str] = {}
        for row in root.iter():
            fields = {child.tag.split("}")[-1]: (child.text or "") for child in row}
            logical_name = fields.get("szName")
            stream_id = fields.get("iID")
            scrap = fields.get("bScrapFlag", "false").strip().lower() == "true"
            if logical_name and stream_id and not scrap:
                mapping[logical_name] = stream_id
        if not mapping:
            raise GXWFormatError("projectdatalist.xml contained no logical project objects")
        return mapping

    def logical_files(self) -> List[GXWLogicalFile]:
        return [
            GXWLogicalFile(logical_name=name, stream_name=stream)
            for name, stream in sorted(
                self._logical_to_stream.items(),
                key=lambda item: (0, int(item[1]))
                if item[1].isdigit()
                else (1, item[1]),
            )
        ]

    def program_pou_names(self) -> List[str]:
        return sorted(
            name for name in self._logical_to_stream if name.endswith(".Program.pou")
        )

    def read_logical_file(self, logical_name: str) -> bytes:
        try:
            stream_name = self._logical_to_stream[logical_name]
        except KeyError as exc:
            raise KeyError(f"GXW logical project object not found: {logical_name}") from exc
        try:
            return self.hdb.read_stream(stream_name)
        except KeyError as exc:
            raise GXWFormatError(
                f"logical object {logical_name!r} points to missing _hdb stream {stream_name!r}"
            ) from exc

    def choose_program_pou(self, logical_name: Optional[str] = None) -> str:
        if logical_name is not None:
            if logical_name not in self._logical_to_stream:
                raise KeyError(f"GXW logical project object not found: {logical_name}")
            if not logical_name.endswith(".Program.pou"):
                raise GXWFormatError(f"not a Program.pou logical object: {logical_name}")
            return logical_name

        candidates = self.program_pou_names()
        if not candidates:
            raise GXWFormatError("GXW project contains no *.Program.pou object")
        if len(candidates) > 1:
            raise GXWFormatError(
                "GXW project contains multiple Program.pou objects; specify one explicitly: "
                + ", ".join(candidates)
            )
        return candidates[0]
