from pathlib import Path
R=Path.cwd()
p=R/'src/knowledge/structured_facts.py';s=p.read_text()
a=s.index('def _chunk_results(')
s=s[:a]+'''def _declared_instruction_alias_targets(query):
    """Resolve declared natural-language names, not inferred implementation intent.

    The importer already owns these aliases. Preserve their lookup when exact
    facts move out of broad RRF instead of rebuilding a prompt-specific rule.
    Incidental English prose is not an opcode or a free-form title match.
    """
    core, _path, connection, schema, _meta = _runtime()
    aliases = schema.get("instruction_aliases") if schema else None
    instructions = schema.get("instructions") if schema else None
    if not connection or not aliases or not instructions:
        return []
    if not {"alias", "alias_type", "instruction_id"}.issubset(aliases["columns"]) or not {"id", "opcode"}.issubset(instructions["columns"]):
        return []
    rows = connection.execute(
        f"SELECT DISTINCT a.alias,i.opcode FROM {core._quote_identifier(aliases['name'])} a "
        f"JOIN {core._quote_identifier(instructions['name'])} i ON i.id=a.instruction_id "
        "WHERE a.alias_type='zh_alias' ORDER BY a.alias,i.opcode"
    )
    targets = []
    for row in rows:
        alias, opcode = str(row["alias"] or ""), str(row["opcode"] or "").upper()
        if len(alias) < 2 or not core._alias_occurs(query, alias):
            continue
        targets.append({"opcode": opcode, "base_opcode": opcode,
                        "target_resolution": "declared_manual_alias", "requested_alias": alias})
    return targets


'''+s[a:]
a=s.index('    return {',s.index('def structured_fact_targets'))
s=s[:a]+'''    identified = {str(row.get("opcode") or "").upper() for row in instructions}
    for target in _declared_instruction_alias_targets(route.query_text):
        if target["opcode"] not in identified:
            instructions.append(target)
            identified.add(target["opcode"])
'''+s[a:]
a=s.index('def resolve_instruction_records(')
s=s[:a]+'''def _instruction_section_records(opcode, *, plc_model, task_type):
    """Recover official basic-instruction sections absent from the row catalogue.

    Exact leaf headings and the original body must both identify the mnemonic.
    This is metadata lookup, never broad ranking or an alias substring match.
    The section remains verbatim; registry facts retain their own provenance.
    """
    core, _path, connection, schema, _meta = _runtime()
    chunks = schema.get("chunks") if schema is not None else None
    if not connection or not chunks or not {"id", "section", "text", "manual_type"}.issubset(chunks["columns"]):
        return []
    indexed = getattr(core._thread_state, "instruction_sections", None)
    if indexed is None:
        indexed = []
        placeholders = ",".join("?" for _ in _OFFICIAL_INSTRUCTION_TYPES)
        for row in connection.execute(
            f"SELECT * FROM {core._quote_identifier(chunks['name'])} WHERE manual_type IN ({placeholders})",
            tuple(sorted(_OFFICIAL_INSTRUCTION_TYPES)),
        ):
            leaf = str(row["section"] or "").split(" > ")[-1]
            indexed.append((row, core._instruction_heading_terms(leaf)))
        core._thread_state.instruction_sections = indexed
    records = []
    for row, names in indexed:
        if opcode not in names or not core._row_in_scope(row, plc_model, task_type):
            continue
        if not core._alias_occurs(str(row["text"] or ""), opcode):
            continue
        for record in _chunk_results([row["id"]], plc_model=plc_model, task_type=task_type,
                                     fact_kind="instruction", fact_target=opcode):
            record["instruction_opcode"] = opcode
            record["instruction_lookup_basis"] = "official_section_heading"
            records.append(record)
    return records


'''+s[a:]
a=s.index('        if not candidates:',s.index('def resolve_instruction_records'))
s=s[:a]+'''        if not candidates:
            for section in _instruction_section_records(opcode, plc_model=plc_model, task_type=task_type):
                original_text = section["text"]
                candidate = _attach_instruction_contract(
                    _attach_instruction_step_width(section, step_target, plc_model=plc_model),
                    step_target, plc_model=plc_model,
                )
                candidate["manual_text"] = original_text
                if authority:
                    candidate["instruction_source_authority"] = copy.deepcopy(authority)
                    candidate["instruction_source_authority_status"] = (
                        "authoritative" if candidate.get("manual_id") == authority["manual_id"] else "non_authoritative"
                    )
                candidates.append((
                    0 if not authority or candidate.get("manual_id") == authority["manual_id"] else 1,
                    -1, 0, -int(candidate.get("manual_priority") or 0),
                    int(candidate.get("pdf_page") or 0), str(candidate.get("id") or ""), candidate,
                ))
'''+s[a:]
a=s.index('def resolve_device_records(');b=s.index('def resolve_error_records(',a)
s=s[:a]+r'''def _device_definition_ids(connection, schema, device, *, plc_model, task_type):
    """A named device/range in an official leaf heading owns its definition.

    The occurrence index can point at an instruction example. Do not promote
    such a mention over a declaration; use the original section unchanged.
    """
    from plc.device_identity import canonical_device
    core, _path, _connection, _schema, _meta = _runtime()
    chunks = schema.get("chunks")
    if not chunks or not {"id", "section", "manual_type", "text"}.issubset(chunks["columns"]):
        return []
    parsed = re.fullmatch(r"([A-Z]+)([0-9A-F]+)", device)
    if not parsed:
        return []
    prefix, number = parsed.groups()
    ids = []
    for row in connection.execute(
        f"SELECT * FROM {core._quote_identifier(chunks['name'])} "
        "WHERE section LIKE '%[%]%' AND manual_type IN ('programming','positioning','structured_device')"
    ):
        if not core._row_in_scope(row, plc_model, task_type):
            continue
        leaf = str(row["section"] or "").split(" > ")[-1]
        for label in re.findall(r"\[([^\]]+)\]", leaf):
            match = re.fullmatch(r"\s*([A-Z]+)([0-9A-F]+)\s*(?:to|[-–～]|至)\s*([A-Z]+)([0-9A-F]+)\s*", label, re.I)
            matches = canonical_device(label.strip().upper()) == device
            if match:
                low_prefix, low, high_prefix, high = match.groups()
                matches = low_prefix.upper() == high_prefix.upper() == prefix and int(low, 16) <= int(number, 16) <= int(high, 16)
            body = re.split(r"\n\[(?:PAGE|TABLE)\b", str(row["text"] or ""), maxsplit=1)[-1]
            # Continuation chunks can repeat a heading while containing only an
            # adjacent topic. The cited body must still contain this definition.
            body_mentions = core._alias_occurs(body, device) or label.casefold() in body.casefold()
            if matches and body_mentions:
                ids.append(row["id"])
                break
    return ids


def resolve_device_records(devices, *, plc_model="FX3U", task_type="analysis", query=None):
    """Resolve device definitions and explicit property questions, before RRF.

    An original official range table owns time-base questions. This retains the
    established metadata selector without allowing incidental T-device mentions
    in program examples to displace that evidence in the broad rank fusion.
    """
    from plc.device_identity import canonical_device
    core, _path, connection, schema, _meta = _runtime()
    if connection is None or schema is None:
        return []
    table = schema.get("device_records")
    table_available = bool(table and {"device_norm", "device", "chunk_id"}.issubset(table["columns"]))
    requested_devices = list(dict.fromkeys(str(item or "").strip().upper()
                             for item in devices or () if str(item or "").strip()))
    results, seen = [], set()
    chunks = schema.get("chunks")
    if query and core._query_is_timer_preset(str(query)) and chunks and {"id", "manual_type", "section", "text"}.issubset(chunks["columns"]):
        targets = [canonical_device(item) for item in requested_devices if re.fullmatch(r"T[0-9]+", item)] or ["T"]
        for row in connection.execute(
            f"SELECT * FROM {core._quote_identifier(chunks['name'])} "
            "WHERE manual_type IN ('programming','structured_device') AND section LIKE '%Numbers of timers%'"
        ):
            if not core._row_in_scope(row, plc_model, task_type) or not core._timer_range_evidence(row["text"], plc_model):
                continue
            for target in targets:
                if target != "T":
                    ranges = re.findall(r"\bT(\d+)\s*(?:to|[-–～]|至)\s*T(\d+)\b", str(row["text"] or ""), re.I)
                    if not any(int(low) <= int(target[1:]) <= int(high) for low, high in ranges):
                        continue
                for record in _chunk_results([row["id"]], plc_model=plc_model, task_type=task_type,
                                             fact_kind="device", fact_target=target):
                    record["fact_dimensions"] = ["range", "time_base"]
                    record["device_lookup_basis"] = "official_property_section"
                    record["structured_fact_requested_target"] = target
                    seen.add((record["id"], target))
                    results.append(record)
    for requested_device in requested_devices:
        device = canonical_device(requested_device)
        if not isinstance(device, str) or not device:
            continue
        definition_ids = _device_definition_ids(connection, schema, device, plc_model=plc_model, task_type=task_type)
        ids = definition_ids
        if not ids and table_available:
            rows = connection.execute(
                f"SELECT * FROM {core._quote_identifier(table['name'])} WHERE device_norm=? AND chunk_id IS NOT NULL ORDER BY occurrences DESC,id",
                (device.casefold(),),
            ).fetchall()
            ids = [row["chunk_id"] for row in rows if "plc_models" not in row.keys() or core._scope_matches(row["plc_models"], plc_model)]
        candidates = _chunk_results(ids, plc_model=plc_model, task_type=task_type, fact_kind="device", fact_target=device)
        candidates.sort(key=lambda item: (-int(item.get("manual_priority") or 0), int(item.get("pdf_page") or 0), str(item.get("id") or "")))
        # Preserve requested-target order; source priority compares sources for
        # one target, never starves a later target in a multi-device query.
        for record in candidates:
            marker = (record["id"], device)
            if marker in seen:
                continue
            seen.add(marker)
            record["structured_fact_requested_target"] = requested_device
            record["device_lookup_basis"] = "official_definition_heading" if definition_ids else "device_record"
            results.append(record)
    return results


'''+s[b:]
a=s.index('    text = str(value.get("text") or "")',s.index('def compact_structured_fact_record'))
s=s[:a]+'''    if value.get("instruction_lookup_basis") == "official_section_heading":
        value["text"] = value.pop("manual_text", value.get("text", ""))
        return value
'''+s[a:]
p.write_text(s)
