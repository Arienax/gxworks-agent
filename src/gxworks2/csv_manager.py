import csv
import hashlib
import shutil
import codecs
import json
import re
import unicodedata
import tempfile
from datetime import datetime
from pathlib import Path

from .models import CommentCSVValidationResult, CSVValidationResult
from plc.comments import GXWORKS2_DEVICE_COMMENT_MAX_CHARS


GXWORKS2_HEADER = [
    "步号",
    "行间声明",
    "指令",
    "I/O(软元件)",
    "空白栏",
    "PI声明",
    "注解",
]

GXWORKS2_COMMENT_HEADER = ["软元件名", "注释"]
GXWORKS2_STATEMENT_MAX_BYTES = 64


class CSVManager:
    """Validate and stage immutable GX Works2 statement-list CSV files."""

    def validate(self, csv_path):
        path = Path(csv_path).expanduser()
        errors = []
        warnings = []
        if not path.is_file():
            return CSVValidationResult(
                valid=False,
                path=str(path),
                errors=["程序CSV文件不存在"],
            )
        if path.suffix.casefold() != ".csv":
            errors.append("文件扩展名必须是.csv")
        if path.stat().st_size <= 2:
            errors.append("程序CSV文件为空")
        try:
            prefix = path.read_bytes()[:2]
        except OSError as error:
            return CSVValidationResult(
                False,
                str(path),
                errors=[f"无法读取CSV：{error}"],
            )
        if prefix != codecs.BOM_UTF16_LE:
            errors.append("GX Works2程序CSV必须使用UTF-16 Little Endian并包含BOM")

        rows = []
        encoding = ""
        for candidate in ("utf-16",):
            try:
                with path.open("r", encoding=candidate, newline="") as handle:
                    rows = list(csv.reader(handle, delimiter="\t"))
                encoding = candidate
                break
            except (UnicodeError, csv.Error):
                rows = []
        if not encoding:
            errors.append("CSV必须使用UTF-16 Little Endian编码")
            return CSVValidationResult(False, str(path), errors=errors)

        if len(rows) < 4:
            errors.append("CSV缺少GX Works2标题或程序内容")
        normalized_header = rows[2][:7] if len(rows) > 2 else []
        if normalized_header != GXWORKS2_HEADER:
            errors.append("CSV列标题不是GX Works2程序语句表格式")
        raw_bytes = path.read_bytes()
        if raw_bytes and b"\r\x00\n\x00" not in raw_bytes:
            errors.append("GX Works2程序CSV必须使用CRLF换行")

        instruction_rows = []
        previous_step = -1
        end_count = 0
        for line_number, row in enumerate(rows[3:], start=4):
            padded = list(row) + [""] * max(0, 7 - len(row))
            if len(row) > 7:
                errors.append(f"第{line_number}行超过7列")
            step, instruction = padded[0].strip(), padded[2].strip().upper()
            if not instruction:
                continue
            instruction_rows.append((line_number, instruction))
            if instruction == "END":
                end_count += 1
            if step:
                if not step.isdigit():
                    errors.append(f"第{line_number}行步号不是整数")
                elif int(step) < previous_step:
                    errors.append(f"第{line_number}行步号发生倒序")
                else:
                    previous_step = int(step)

        if not instruction_rows:
            errors.append("CSV没有可导入的PLC指令")
        if end_count != 1:
            errors.append("CSV必须且只能包含一条END指令")
        elif instruction_rows and instruction_rows[-1][1] != "END":
            errors.append("END必须是最后一条指令")

        return CSVValidationResult(
            valid=not errors,
            path=str(path.resolve()),
            encoding=encoding,
            row_count=len(rows),
            instruction_count=len(instruction_rows),
            program_name=(rows[0][0].strip() if rows and rows[0] else ""),
            plc_info=(rows[1][1].strip() if len(rows) > 1 and len(rows[1]) > 1 else ""),
            errors=errors,
            warnings=warnings,
        )

    def validate_comments(self, csv_path, *, require_crlf=True):
        path = Path(csv_path).expanduser()
        errors = []
        warnings = []
        if not path.is_file():
            return CommentCSVValidationResult(
                valid=False,
                path=str(path),
                errors=["软元件注释CSV文件不存在"],
            )
        if path.suffix.casefold() != ".csv":
            errors.append("软元件注释文件扩展名必须是.csv")
        if path.stat().st_size <= 2:
            errors.append("软元件注释CSV文件为空")
        try:
            raw_bytes = path.read_bytes()
        except OSError as error:
            return CommentCSVValidationResult(
                False,
                str(path),
                errors=[f"无法读取软元件注释CSV：{error}"],
            )
        if raw_bytes[:2] != codecs.BOM_UTF16_LE:
            errors.append("GX Works2软元件注释CSV必须使用UTF-16 Little Endian并包含BOM")
        if require_crlf and raw_bytes and b"\r\x00\n\x00" not in raw_bytes:
            errors.append("GX Works2软元件注释CSV必须使用CRLF换行")

        try:
            with path.open("r", encoding="utf-16", newline="") as handle:
                rows = list(csv.reader(handle, delimiter="\t"))
        except (UnicodeError, csv.Error) as error:
            return CommentCSVValidationResult(
                False,
                str(path),
                errors=errors + [f"无法解析软元件注释CSV：{error}"],
            )

        if len(rows) < 2:
            errors.append("软元件注释CSV缺少GX Works2标题")
        elif rows[1][:2] != GXWORKS2_COMMENT_HEADER:
            errors.append("CSV列标题不是GX Works2软元件注释格式")
        # Generated files use ``COMMENT - 副本`` here, while GX Works2's own
        # export writes the current project title (for example ``(工程未设置)``).
        # Both are native comment-CSV documents; the second-row column header
        # is the stable format discriminator.
        if rows and (not rows[0] or not rows[0][0].strip()):
            errors.append("软元件注释CSV首行缺少文档标题")

        comment_count = 0
        seen_devices = set()
        for line_number, row in enumerate(rows[2:], start=3):
            if not row or not any(str(value).strip() for value in row):
                continue
            if len(row) > 2:
                errors.append(f"软元件注释CSV第{line_number}行超过2列")
            padded = list(row) + [""] * max(0, 2 - len(row))
            device = padded[0].strip().upper()
            comment = padded[1].strip()
            if not device:
                errors.append(f"软元件注释CSV第{line_number}行缺少软元件名")
                continue
            if not comment:
                warnings.append(f"软元件{device}的注释为空，已忽略")
                continue
            if len(comment) > GXWORKS2_DEVICE_COMMENT_MAX_CHARS:
                errors.append(
                    f"软元件{device}的注释超过"
                    f"{GXWORKS2_DEVICE_COMMENT_MAX_CHARS}字符"
                )
            if device in seen_devices:
                warnings.append(f"软元件{device}存在重复注释，GX Works2将按文件顺序处理")
            seen_devices.add(device)
            comment_count += 1

        if not comment_count and not errors:
            warnings.append("软元件注释CSV没有可导入内容，将保留工程中的现有注释")

        return CommentCSVValidationResult(
            valid=not errors,
            path=str(path.resolve()),
            encoding="utf-16",
            row_count=len(rows),
            comment_count=comment_count,
            errors=errors,
            warnings=warnings,
        )

    @staticmethod
    def file_sha256(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    @staticmethod
    def _semantic_cell(value, *, uppercase=False):
        text = unicodedata.normalize("NFKC", str(value or "")).strip()
        text = re.sub(r"\s+", " ", text)
        return text.upper() if uppercase else text

    @staticmethod
    def truncate_statement(value, max_bytes=GXWORKS2_STATEMENT_MAX_BYTES):
        """Fit an interline statement into GX Works2's legacy byte limit."""

        text = unicodedata.normalize("NFKC", str(value or "")).strip()
        limit = max(0, int(max_bytes))
        if len(text.encode("gb18030")) <= limit:
            return text
        suffix = "…"
        suffix_bytes = len(suffix.encode("gb18030"))
        result = []
        used = 0
        for character in text:
            size = len(character.encode("gb18030"))
            if used + size + suffix_bytes > limit:
                break
            result.append(character)
            used += size
        return "".join(result).rstrip() + suffix

    def prepare_import_program(self, source_path, destination_path):
        """Create a GX-compatible staging CSV without mutating a version."""

        source = Path(source_path).expanduser().resolve()
        destination = Path(destination_path).expanduser().resolve()
        with source.open("r", encoding="utf-16", newline="") as handle:
            rows = list(csv.reader(handle, delimiter="\t"))
        changed = False
        for row in rows[3:]:
            if len(row) > 1 and row[1]:
                normalized = self.truncate_statement(row[1])
                if normalized != row[1]:
                    row[1] = normalized
                    changed = True
        if not changed:
            return source
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-16", newline="") as handle:
            csv.writer(
                handle,
                delimiter="\t",
                quoting=csv.QUOTE_ALL,
                lineterminator="\r\n",
            ).writerows(rows)
        return destination

    def program_semantic_payload(self, csv_path):
        """Return canonical instructions across generated and GX-exported CSVs.

        The application renderer writes all operands in one ``I/O`` cell, but
        GX Works2 writes additional operands on following rows whose opcode is
        blank.  Those rows are not new instructions.  Rejoin them so a native
        export of an unchanged import has exactly the same semantic digest.
        Titles, step offsets and human-readable declarations remain excluded.
        """

        path = Path(csv_path).expanduser().resolve()
        with path.open("r", encoding="utf-16", newline="") as handle:
            rows = list(csv.reader(handle, delimiter="\t"))
        instructions = []
        for row in rows[3:]:
            padded = list(row) + [""] * max(0, 7 - len(row))
            opcode = self._semantic_cell(padded[2], uppercase=True)
            operands = self._semantic_cell(padded[3], uppercase=True)
            if opcode:
                instructions.append([opcode, operands])
            elif operands and instructions:
                instructions[-1][1] = " ".join(
                    value for value in (instructions[-1][1], operands) if value
                )
        return {"schema_version": 1, "instructions": instructions}

    def program_semantic_sha256(self, csv_path):
        payload = self.program_semantic_payload(csv_path)
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def comments_semantic_payload(self, csv_path):
        """Return the effective GX device-comment mapping in stable form.

        GX Works2 exports a project-specific title and may use different line
        endings from files produced by the application.  Neither changes the
        effective global device comments.  Duplicate rows are resolved in
        file order, matching the value visible after an import.
        """

        path = Path(csv_path).expanduser().resolve()
        with path.open("r", encoding="utf-16", newline="") as handle:
            rows = list(csv.reader(handle, delimiter="\t"))
        comments = {}
        for row in rows[2:]:
            padded = list(row) + [""] * max(0, 2 - len(row))
            device = self._semantic_cell(padded[0], uppercase=True)
            comment = self._semantic_cell(padded[1])
            if device:
                if comment:
                    comments[device] = comment
                else:
                    comments.pop(device, None)
        return {
            "schema_version": 1,
            "comments": [
                [device, comments[device]]
                for device in sorted(comments, key=lambda item: item.casefold())
            ],
        }

    def comments_semantic_sha256(self, csv_path):
        payload = self.comments_semantic_payload(csv_path)
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def export(self, source_path, destination_path):
        source = Path(source_path).resolve()
        destination = Path(destination_path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return destination

    def backup(self, source_path, backup_root, project_name="GXWorks2"):
        source = Path(source_path).resolve()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        safe_name = "".join(
            char if char.isalnum() or char in "-_." else "_"
            for char in str(project_name or "GXWorks2")
        ).strip("._") or "GXWorks2"
        folder = Path(backup_root).expanduser().resolve() / safe_name / stamp
        folder.mkdir(parents=True, exist_ok=False)
        backup_path = folder / "program_before_import.csv"
        shutil.copy2(source, backup_path)
        digest = hashlib.sha256(backup_path.read_bytes()).hexdigest()
        (folder / "sha256.txt").write_text(digest + "\n", encoding="ascii")
        return backup_path

    @staticmethod
    def backup_folder(backup_root, project_name="GXWorks2"):
        safe_name = "".join(
            char if char.isalnum() or char in "-_." else "_"
            for char in str(project_name or "GXWorks2")
        ).strip("._") or "GXWorks2"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        parent = Path(backup_root).expanduser().resolve() / safe_name
        parent.mkdir(parents=True, exist_ok=True)
        # Wall-clock resolution is not a uniqueness guarantee on Windows.
        # Atomically allocate a fresh directory even for same-tick operations.
        return Path(tempfile.mkdtemp(prefix=stamp + "-", dir=parent))

    @staticmethod
    def write_checksum(path):
        path = Path(path).resolve()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        (path.parent / "sha256.txt").write_text(digest + "\n", encoding="ascii")
        return digest

    @staticmethod
    def write_checksum_manifest(folder):
        folder = Path(folder).resolve()
        entries = []
        for path in sorted(folder.glob("*.csv"), key=lambda item: item.name.casefold()):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append(f"{digest}  {path.name}")
        (folder / "sha256.txt").write_text("\n".join(entries) + "\n", encoding="ascii")
        return entries
