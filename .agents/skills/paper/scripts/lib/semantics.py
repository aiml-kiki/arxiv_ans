"""Compile explicitly registered Markdown research objects; never infer support.

The index is disposable. Source responses and review decisions are persistent;
neither this compiler nor impact propagation edits a scientific note.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
import json
from pathlib import Path
import re

from .core import atomic_text, default_root, digest, inside, locked, read_json, safe_id, utc_now, write_json
from .notes import note_paths, read_note, visible_markdown, wiki_parts

FORMAT = "paper-semantic-v1"
OBJECT_TYPES = {"命题": "claim", "关系": "relation", "未解问题": "question", "知识更新": "delta"}
CLAIM_TYPES = {"机制", "经验结果", "理论", "实现", "跨篇选择", "假设"}
VERDICTS = {"支持", "有条件支持", "证据不足", "相反证据", "原文未知"}
EVIDENCE_TYPES = {"支持", "限制", "反驳", "背景"}
RELATIONS = {"导航", "前置", "实例", "证据支持", "依赖", "限制", "反例", "引用", "承继", "替代", "分叉", "组合"}
ATTRIBUTIONS = {"作者明说", "分析者推断", "待核验线索"}
QUESTION_STATES = {"未解", "已解决", "已澄清为不同条件", "暂缓"}
DELTA_TYPES = {"新建", "补充实例", "澄清条件", "修正判断", "拆分", "合并", "留在论文", "待核验", "不变"}
ALLOWED_FIELDS = {
    "claim": {"研究对象", "判断类型", "范围", "提出者", "证据", "依赖", "裁决", "假设", "区分观察"},
    "relation": {"研究对象", "源", "目标", "关系类型", "归属", "范围", "证据"},
    "question": {"研究对象", "问题状态", "范围", "竞争解释", "区分观察", "进展", "参与命题", "证据"},
    "delta": {"研究对象", "增量类型", "旧判断", "新判断", "处理决定", "受影响", "证据"},
}
FIELD = re.compile(r"^\*\*([^*\n：:]+)[：:]\*\*\s*(.*?)\s*$")
READABLE_FIELD = re.compile(r"^\*\*([^*\n：:]+)\*\*[：:]\s*(.*?)\s*$")
RECORD_CALLOUT = re.compile(r"^>\s*\[!note\]-\s*(判断记录|来源与判断记录|关系记录|问题记录|更新记录)\s*$", re.I)
RECORD_TYPES = {"判断记录": "命题", "来源与判断记录": "命题", "关系记录": "关系",
                "问题记录": "未解问题", "更新记录": "知识更新"}
ANCHOR = re.compile(r"(?<!\S)\^([A-Za-z0-9-]+)\s*$")
WIKI = re.compile(r"\[\[([^\]\n]+)\]\]")
PLACEHOLDER = re.compile(r"^(?:<.*>|待填写|TODO|TBD|\.\.\.|…)$", re.I)
INDEX_PATH = ".research/index/semantic.json"
BODY_OBJECT = re.compile(r'^<!-- paper-object (.+) -->$')
TEXT_FIELDS = {"范围", "假设", "区分观察", "竞争解释", "进展", "旧判断", "新判断", "处理决定"}


def _body_bindings(text):
    """Resolve annotations only against unique, visible ordinary paragraphs."""
    raw, visible = text.splitlines(), visible_markdown(text).splitlines()
    blocks = {}
    for index, line in enumerate(visible):
        match = ANCHOR.search(line)
        if not match:
            continue
        if match[1] in blocks:
            raise ValueError(f"正文定位存在重复块 ID：{match[1]}")
        start = index
        while start and visible[start - 1].strip():
            start -= 1
        paragraph = "\n".join(raw[start:index] + [raw[index][:match.start()].rstrip()])
        if not paragraph.strip() or re.match(r'^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|>|\||```|~~~)', paragraph):
            continue
        blocks[match[1]] = paragraph
    return blocks


def _annotation_fields(data, blocks):
    if not isinstance(data, dict) or set(data) - {"kind", "fields", "source", "evidence"}:
        raise ValueError("正文绑定只接受 kind/fields/source/evidence")
    kind = data.get("kind")
    if kind not in OBJECT_TYPES or not isinstance(data.get("fields"), dict):
        raise ValueError("正文绑定须给合法 kind 与 fields")

    def prose(pointer):
        if not isinstance(pointer, dict) or set(pointer) != {"block"} or pointer["block"] not in blocks:
            raise ValueError("解释字段必须指向本页唯一可见普通段落：{\"block\":\"稳定ID\"}")
        return " ".join(blocks[pointer["block"]].splitlines())

    rows = [("研究对象", kind)]
    for name, value in data["fields"].items():
        if name == "研究对象" or name not in ALLOWED_FIELDS[OBJECT_TYPES[kind]] - {"证据"}:
            raise ValueError(f"正文绑定未知字段：{name}")
        values = value if isinstance(value, list) else [value]
        for item in values:
            if name in TEXT_FIELDS:
                item = prose(item)
            elif name == "裁决":
                if not isinstance(item, dict) or set(item) != {"status", "reason"}:
                    raise ValueError("裁决须给 status 与指向正文的 reason")
                item = str(item["status"]) + "｜理由=" + prose(item["reason"])
            elif name in {"依赖", "源", "目标", "参与命题", "受影响"}:
                if not isinstance(item, str) or not re.fullmatch(r'(?:论文|知识|专题)/[^\n\[\]]+#\^[A-Za-z0-9-]+', item):
                    raise ValueError("关系定位须为正式路径#^稳定块ID；阅读解释留在正文")
                item = "[[" + item + "]]"
            elif not isinstance(item, str):
                raise ValueError(f"分类字段必须为字符串：{name}")
            rows.append((name, item))
    defaults = data.get("source", {})
    if not isinstance(defaults, dict) or set(defaults) - {"id", "version", "verification", "sha256"}:
        raise ValueError("source 仅接受 id/version/verification/sha256；不同源独立覆盖")
    evidence = data.get("evidence", [])
    if not isinstance(evidence, list):
        raise ValueError("evidence 必须为列表")
    for entry in evidence:
        if not isinstance(entry, dict) or set(entry) - {"relation", "block", "link", "location", "source"}:
            raise ValueError("证据只保存关系、正文定位、链接序号、原文位置及必要来源覆盖")
        body = prose({"block": entry.get("block")})
        links = re.findall(r'\[\[[^\]\n]+\]\]|(?<!!)\[[^\]\n]*\]\(https://[^\s)]+\)', body)
        number = entry.get("link", 0)
        if type(number) is not int or number < 0 or number >= len(links):
            raise ValueError("证据链接序号须指向该可见正文段落的真实 Wiki/HTTPS 链接（从0起）")
        override = entry.get("source", {})
        if not isinstance(override, dict) or set(override) - {"id", "version", "verification", "sha256"}:
            raise ValueError("证据来源覆盖存在未知字段")
        if any(key in override and override[key] != defaults.get(key) for key in ("id", "version")):
            source = override  # Never inherit another source's verification or hash.
        else:
            source = {**defaults, **override}
        slots = {"来源": source.get("id", ""), "版本": source.get("version", ""),
                 "核验": source.get("verification", ""), "位置": entry.get("location", ""),
                 "链接": links[number], "说明": body}
        if source.get("sha256"):
            slots["哈希"] = source["sha256"]
        value = "｜".join([str(entry.get("relation", ""))] + [f"{key}={value}" for key, value in slots.items()])
        _evidence(value)
        rows.append(("证据", value))
    return rows


def body_presentation_issues(text):
    """New candidates must explain judgments in prose; histories stay readable."""
    issues = []
    for number, line in enumerate(visible_markdown(text).splitlines(), 1):
        field = FIELD.fullmatch(line)
        if RECORD_CALLOUT.fullmatch(line) or (field and field[1] == "研究对象"):
            issues.append(f"第 {number} 行：新候选不输出独立判断/关系/问题/更新记录；先融入正文，再用 paper-object 绑定")
    return issues


def _filled(value):
    return bool(str(value).strip()) and not PLACEHOLDER.fullmatch(str(value).strip())


def _scientific_fingerprint(record):
    values = {key: value for key, value in record.items()
              if key not in {"key", "id", "path", "line", "fields", "fingerprint", "note_sha256"}}
    values["evidence"] = [{key: item.get(key) for key in
                           ("relation", "source_id", "version", "location", "verification", "link",
                            "rationale", "requested_sha256")} for item in record.get("evidence", [])]
    return digest(json.dumps(values, ensure_ascii=False, sort_keys=True))


def _slots(value):
    """Full-width separator avoids splitting Wiki aliases or Markdown tables."""
    parts = [part.strip() for part in value.split("｜")]
    head, fields = parts[0], {}
    for part in parts[1:]:
        if "=" not in part:
            raise ValueError(f"槽位必须为 名称=值：{part}")
        name, text = part.split("=", 1)
        if name.strip() in fields:
            raise ValueError(f"重复槽位：{name.strip()}")
        fields[name.strip()] = text.strip()
    return head, fields


def _one(fields, name, required=True):
    values = fields.get(name, [])
    if len(values) > 1:
        raise ValueError(f"{name}只允许一行；重复证据/依赖另按对应字段处理")
    if required and (not values or not _filled(values[0])):
        raise ValueError(f"缺少有效字段：{name}")
    return values[0] if values else None


def _refs(value, current_path):
    matches = list(WIKI.finditer(value))
    if not matches:
        raise ValueError("语义引用须为 [[库根相对页#^稳定块ID]]")
    refs = []
    for match in matches:
        dest = wiki_parts(match[1])[0].strip()
        if "#^" not in dest:
            raise ValueError(f"语义引用缺少稳定块 ID：{dest}")
        page, block = dest.rsplit("#^", 1)
        if not re.fullmatch(r"[A-Za-z0-9-]+", block):
            raise ValueError(f"块 ID 只能用 ASCII 字母、数字及连字符：{block}")
        if not page:
            page = current_path
        elif not page.endswith(".md"):
            page += ".md"
        if page.split("/", 1)[0] not in {"论文", "知识", "专题"}:
            raise ValueError(f"语义引用需用正式页的库根相对路径：{page}")
        if ".." in Path(page).parts or Path(page).is_absolute():
            raise ValueError(f"非法语义路径：{page}")
        refs.append(f"{page}#^{block}")
    return refs


def _evidence(value):
    kind, slots = _slots(value)
    if kind not in EVIDENCE_TYPES:
        raise ValueError(f"未知证据关系：{kind}")
    required = {"来源", "版本", "位置", "核验", "链接", "说明"}
    missing = [name for name in required if not _filled(slots.get(name, ""))]
    if missing:
        raise ValueError(f"证据缺少有效槽位：{', '.join(sorted(missing))}")
    if set(slots) - required - {"哈希"}:
        raise ValueError("证据存在未知槽位；仅使用协议定义的字段")
    if slots["位置"] in {"全文", "全篇", "paper", "source"}:
        raise ValueError("证据需具体页/节/表图/代码位置，不能只有全文")
    if slots["核验"] not in {"定位已核", "未核"}:
        raise ValueError("证据核验只能为定位已核/未核；不能以此表示独立复现")
    if slots.get("哈希") and not re.fullmatch(r"[0-9a-f]{64}", slots["哈希"]):
        raise ValueError("来源哈希须为完整 SHA-256")
    return {"relation": kind, "source_id": slots["来源"], "version": slots["版本"],
            "location": slots["位置"], "verification": slots["核验"],
            "link": slots["链接"], "rationale": slots["说明"],
            "requested_sha256": slots.get("哈希")}


def _registration_view(text):
    """Resolve body bindings and legacy records, retaining physical line locators.

    Ordinary quotes, fenced examples and unmarked comments never register an object.
    The scientific paragraph and its stable anchor remain outside the callout.
    """
    visible = visible_markdown(text).splitlines()
    raw = text.splitlines()
    lines, original, locations, errors = [], [], [], []
    # Reveal only the explicit marker for fence detection; ordinary comments
    # remain invisible and can never accidentally become research objects.
    probe = visible_markdown(re.sub(r'^<!-- paper-object .+ -->$', 'PAPER_OBJECT_MARKER', text, flags=re.M)).splitlines()
    blocks = None
    index = 0
    while index < len(visible):
        annotation = BODY_OBJECT.fullmatch(raw[index])
        if annotation and probe[index] == 'PAPER_OBJECT_MARKER':
            try:
                if blocks is None:
                    blocks = _body_bindings(text)
                for name, value in _annotation_fields(json.loads(annotation[1]), blocks):
                    field = f"**{name}：** {value}"
                    lines.append(field); original.append(field); locations.append(index)
                lines.append("<!-- record boundary -->"); original.append(""); locations.append(index)
            except (ValueError, TypeError, KeyError) as exc:
                errors.append({"line": index + 1, "message": str(exc)})
            index += 1
            continue
        header = RECORD_CALLOUT.fullmatch(visible[index])
        if not header:
            lines.append(visible[index]); original.append(raw[index]); locations.append(index)
            index += 1
            continue
        start, rows = index, []
        index += 1
        while index < len(visible) and re.match(r"^>($|\s)", visible[index]):
            content = re.sub(r"^> ?", "", raw[index])
            masked = re.sub(r"^> ?", "", visible[index])
            if masked.strip():
                rows.append((index, content))
            index += 1
        try:
            expanded = [(start, "研究对象", RECORD_TYPES[header[1]])]
            defaults, judgement, reason = {}, None, None
            parsed = []
            for location, content in rows:
                match = READABLE_FIELD.fullmatch(content) or FIELD.fullmatch(content)
                if not match:
                    raise ValueError("登记提示块只接受明确字段；解释、依据和重要限制放在可见正文")
                name, value = match[1].strip(), match[2]
                if name == "来源":
                    if defaults:
                        raise ValueError("共同来源只允许一行；不同来源在各条证据中明确填写")
                    source, slots = _slots(value)
                    if set(slots) - {"版本", "核验", "哈希"}:
                        raise ValueError("共同来源只接受版本、核验及必要哈希")
                    defaults = {"来源": source, **slots}
                    if any(not _filled(defaults.get(key, "")) for key in ("来源", "版本", "核验")):
                        raise ValueError("共同来源须明确来源、版本及核验状态")
                    if defaults["核验"] not in {"定位已核", "未核"}:
                        raise ValueError("共同来源核验只能为定位已核/未核")
                    if defaults.get("哈希") and not re.fullmatch(r"[0-9a-f]{64}", defaults["哈希"]):
                        raise ValueError("来源哈希须为完整 SHA-256")
                elif name == "判断":
                    if judgement is not None or header[1] not in {"判断记录", "来源与判断记录"}:
                        raise ValueError("判断简写只允许命题记录中一行")
                    claim_type, slots = _slots(value)
                    if set(slots) != {"提出者", "裁决"}:
                        raise ValueError("判断简写须明确类型｜提出者=作者/分析者｜裁决=实际状态")
                    judgement = (location, claim_type, slots)
                elif name == "理由":
                    if reason is not None:
                        raise ValueError("理由只允许一行")
                    reason = (location, value)
                elif name in EVIDENCE_TYPES:
                    parsed.append((location, "证据", name + "｜" + value))
                else:
                    parsed.append((location, name, value))
            if judgement is not None:
                if reason is None or not _filled(reason[1]):
                    raise ValueError("判断简写必须保留具体理由")
                location, claim_type, slots = judgement
                expanded.extend([(location, "判断类型", claim_type), (location, "提出者", slots["提出者"]),
                                 (reason[0], "裁决", slots["裁决"] + "｜理由=" + reason[1])])
            elif reason is not None:
                raise ValueError("单独理由须配合判断简写；完整裁决使用 裁决｜理由=…")
            for location, name, value in parsed:
                if name == "证据" and defaults:
                    kind, slots = _slots(value)
                    other_source = any(key in slots and slots[key] != defaults.get(key)
                                       for key in ("来源", "版本"))
                    if other_source:
                        if any(not _filled(slots.get(key, "")) for key in ("来源", "版本", "核验")):
                            raise ValueError("不同来源/版本的证据须独立填写来源、版本及核验，不能继承共同核验或哈希")
                    else:
                        slots = {**defaults, **slots}
                    value = "｜".join([kind] + [f"{key}={item}" for key, item in slots.items()])
                expanded.append((location, name, value))
            for location, name, value in expanded:
                field = f"**{name}：** {value}"
                lines.append(field); original.append(field); locations.append(location)
            # End this explicit registration before following prose or callouts.
            lines.append("<!-- record boundary -->"); original.append(""); locations.append(index - 1)
        except ValueError as exc:
            errors.append({"line": start + 1, "message": str(exc)})
            lines.append("<!-- invalid record -->"); original.append(""); locations.append(start)
    return lines, original, locations, errors


def parse_note(path, text, line_offset=0):
    """Return explicit records and diagnostics. Unregistered prose stays prose."""
    lines, original, locations, view_errors = _registration_view(text)
    records, errors, used = [], [{"path": path, "line": item["line"] + line_offset,
                                "message": item["message"]} for item in view_errors], set()
    for index, line in enumerate(lines):
        start = FIELD.fullmatch(line)
        if not start or start[1].strip() != "研究对象":
            continue
        label = f"{path}:{locations[index] + 1 + line_offset}"
        try:
            object_type = OBJECT_TYPES.get(start[2])
            if not object_type:
                raise ValueError(f"研究对象未知：{start[2]}")
            before = index - 1
            while before >= 0 and not lines[before].strip():
                before -= 1
            if before < 0:
                raise ValueError("研究对象字段须紧邻带段尾稳定块 ID 的正文")
            match = ANCHOR.search(lines[before])
            if not match or not lines[before][:match.start()].strip():
                raise ValueError("注册正文须以空格 + ^ASCII块ID 结束；孤立或非法 ID 无效")
            if re.match(r"^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|>|\|)", lines[before]):
                raise ValueError("注册正文必须是普通段落，不能把块 ID 放在标题、表格或 callout 内")
            block = match[1]
            if block in used:
                raise ValueError(f"同页重复研究对象块 ID：{block}")
            used.add(block)
            paragraph_start = before
            while paragraph_start > 0 and lines[paragraph_start - 1].strip():
                if (FIELD.fullmatch(lines[paragraph_start - 1]) or
                        lines[paragraph_start - 1].startswith("<!-- record boundary") or
                        re.match(r"^\s*#", lines[paragraph_start - 1])):
                    break
                paragraph_start -= 1
            # Inline code can name an actual parameter or operator. Preserve it
            # after locating registration outside examples and comments.
            prose = "\n".join(original[paragraph_start:before] + [original[before][:match.start()].rstrip()])
            fields = defaultdict(list)
            cursor = index
            while cursor < len(lines):
                if not lines[cursor].strip():
                    cursor += 1
                    continue
                field = FIELD.fullmatch(lines[cursor])
                if not field or (cursor > index and field[1] == "研究对象"):
                    break
                actual = FIELD.fullmatch(original[cursor])
                fields[field[1].strip()].append(actual[2] if actual else field[2])
                cursor += 1
            unknown = set(fields) - ALLOWED_FIELDS[object_type]
            if unknown:
                raise ValueError(f"注册单元未知字段：{', '.join(sorted(unknown))}；普通解释另写段落")
            record = {"key": f"{path}#^{block}", "id": block, "path": path,
                      "line": locations[paragraph_start] + 1 + line_offset, "kind": object_type, "text": prose,
                      "fields": dict(fields), "evidence": [], "dependencies": []}
            if object_type == "claim":
                record["claim_type"] = _one(fields, "判断类型")
                if record["claim_type"] not in CLAIM_TYPES:
                    raise ValueError(f"未知判断类型：{record['claim_type']}")
                record["scope"] = _one(fields, "范围")
                record["proponent"] = _one(fields, "提出者")
                if record["proponent"] not in {"作者", "分析者"}:
                    raise ValueError("提出者只能为作者/分析者")
                verdict, slots = _slots(_one(fields, "裁决"))
                if verdict not in VERDICTS or not _filled(slots.get("理由", "")) or set(slots) != {"理由"}:
                    raise ValueError("裁决须为合法状态｜理由=具体依据与边界")
                record.update(verdict=verdict, rationale=slots["理由"])
                record["assumptions"] = _one(fields, "假设", required=False)
                record["distinguishing_observation"] = _one(fields, "区分观察", required=False)
                record["evidence"] = [_evidence(value) for value in fields.get("证据", [])]
                if not record["evidence"]:
                    raise ValueError("核心命题至少明确一条固定来源证据；证据不足可以保留未核依据与限制")
                for value in fields.get("依赖", []):
                    record["dependencies"].extend(_refs(value, path))
            elif object_type == "relation":
                sources = _refs(_one(fields, "源"), path)
                targets = _refs(_one(fields, "目标"), path)
                if len(sources) != 1 or len(targets) != 1:
                    raise ValueError("一个关系单元须有一个源和一个目标")
                relation = _one(fields, "关系类型")
                attribution = _one(fields, "归属")
                if relation not in RELATIONS or attribution not in ATTRIBUTIONS:
                    raise ValueError("未知关系类型或归属")
                record.update(source=sources[0], target=targets[0], relation=relation,
                              attribution=attribution, scope=_one(fields, "范围"))
                record["evidence"] = [_evidence(value) for value in fields.get("证据", [])]
                if relation in {"承继", "替代", "分叉", "组合"} and attribution != "待核验线索":
                    if len(record["evidence"]) < 2:
                        raise ValueError("确认的技术关系须明确两端来源；两条字段不能自动证明双端语义")
                if attribution == "待核验线索":
                    record["status"] = "需复核"
            elif object_type == "question":
                record["status"] = _one(fields, "问题状态")
                if record["status"] not in QUESTION_STATES:
                    raise ValueError("未知问题状态")
                record["scope"] = _one(fields, "范围")
                record["competing_explanations"] = _one(fields, "竞争解释")
                record["distinguishing_observation"] = _one(fields, "区分观察")
                record["progress"] = _one(fields, "进展")
                record["participants"] = []
                for value in fields.get("参与命题", []):
                    record["participants"].extend(_refs(value, path))
                if not record["participants"]:
                    raise ValueError("未解问题须关联明确的参与命题")
                record["evidence"] = [_evidence(value) for value in fields.get("证据", [])]
            else:
                delta_type = _one(fields, "增量类型")
                if delta_type not in DELTA_TYPES:
                    raise ValueError("未知知识增量类型")
                record.update(delta_type=delta_type, before=_one(fields, "旧判断"),
                              after=_one(fields, "新判断"), decision=_one(fields, "处理决定"))
                record["affected"] = []
                for value in fields.get("受影响", []):
                    record["affected"].extend(_refs(value, path))
                record["evidence"] = [_evidence(value) for value in fields.get("证据", [])]
                if delta_type != "不变" and not record["evidence"]:
                    raise ValueError("实际知识更新须有固定来源依据")
            # Rendering and slot order are not scientific changes. Fingerprint
            # explicit semantic values, excluding physical/display metadata.
            record["fingerprint"] = _scientific_fingerprint(record)
            records.append(record)
        except ValueError as exc:
            errors.append({"path": path, "line": locations[index] + 1 + line_offset, "message": f"{label} {exc}"})
    return records, errors


def bind_registrations(text, bindings=None, path="draft.md"):
    """Remove old cards only with explicit, reviewed body bindings.

    No semantic deduplication by heuristics. Categories and fixed evidence
    identities are checked; changed body explanations require human review.
    """
    before, errors = parse_note(path, text)
    if errors:
        raise ValueError(f"先处理登记错误：{errors}")
    raw, visible = text.splitlines(), visible_markdown(text).splitlines()
    edits, index = [], 0
    while index < len(raw):
        header = RECORD_CALLOUT.fullmatch(visible[index])
        field = FIELD.fullmatch(visible[index])
        if not header and not (field and field[1] == "研究对象"):
            index += 1
            continue
        begin = index
        previous = index - 1
        while previous >= 0 and not visible[previous].strip():
            previous -= 1
        anchor = ANCHOR.search(visible[previous]) if previous >= 0 else None
        if not anchor or not bindings or anchor[1] not in bindings:
            raise ValueError("旧登记迁移必须提供每项 --bindings 正文定位；先把仅在记录中的重要信息补入正文")
        if header:
            index += 1
            while index < len(raw) and re.match(r"^>($|\s)", visible[index]):
                index += 1
            end = index
        else:
            end = index
            while index < len(raw):
                if not visible[index].strip():
                    index += 1
                    continue
                field = FIELD.fullmatch(visible[index])
                if not field or (index > begin and field[1] == "研究对象"):
                    break
                index += 1; end = index
        annotation = "<!-- paper-object " + json.dumps(bindings[anchor[1]], ensure_ascii=False, separators=(",", ":")) + " -->"
        edits.append((begin, end, [annotation]))
    for begin, end, rows in reversed(edits):
        raw[begin:end] = rows
    formatted = "\n".join(raw) + ("\n" if text.endswith("\n") else "")
    after, errors = parse_note(path, formatted)
    if errors or {r["id"] for r in before} != {r["id"] for r in after}:
        raise ValueError(f"正文绑定丢失登记或定位失败：{errors}")
    identity_keys = {"kind", "claim_type", "proponent", "verdict", "source", "target", "relation", "attribution",
                     "status", "delta_type", "dependencies", "participants", "affected"}
    def identity(record):
        value = {key: record.get(key) for key in identity_keys}
        value["evidence"] = [{key: item.get(key) for key in
                              ("relation", "source_id", "version", "location", "verification", "requested_sha256")}
                             for item in record["evidence"]]
        return value
    old = {r["id"]: identity(r) for r in before}
    if old != {r["id"]: identity(r) for r in after}:
        raise ValueError("迁移改变了类别、裁决、关系或固定证据身份；请另走实质科学修订")
    return formatted, len(edits)


def _source_catalog(root):
    catalog, manifest_hashes, errors = [], {}, []
    for path in sorted(inside(root, ".research/sources").rglob("*.json")):
        relative = str(path.relative_to(root))
        try:
            data = read_json(path)
            if not isinstance(data, dict) or not data.get("source_path") or not data.get("source_sha256"):
                continue  # coverage and other sidecars are not source manifests
            source = inside(root, data["source_path"])
            manifest_hashes[relative] = digest(path.read_bytes())
            if not source.is_file() or digest(source.read_bytes()) != data["source_sha256"]:
                errors.append({"path": relative, "message": "固定来源缺失或全文哈希不一致"})
                continue
            catalog.append({"identities": sorted({str(value) for value in (data.get("source_id"), data.get("paper_id")) if value}),
                            "version": str(data.get("version", "")), "source_path": data["source_path"],
                            "source_sha256": data["source_sha256"], "manifest": relative,
                            "manifest_sha256": manifest_hashes[relative]})
        except (ValueError, OSError) as exc:
            errors.append({"path": relative, "message": str(exc)})
    return catalog, manifest_hashes, errors


def _bind_evidence(evidence, catalog):
    candidates = [entry for entry in catalog if evidence["source_id"] in entry["identities"]
                  and evidence["version"] == entry["version"]]
    if evidence.get("requested_sha256"):
        candidates = [entry for entry in candidates if entry["source_sha256"] == evidence["requested_sha256"]]
    hashes = {entry["source_sha256"] for entry in candidates}
    if len(hashes) != 1:
        raise ValueError("证据来源未唯一固定到全文哈希：" + evidence["source_id"] + " / " + evidence["version"])
    chosen = sorted(candidates, key=lambda entry: entry["manifest"])[0]
    links = list(WIKI.finditer(evidence["link"]))
    if len(links) == 1:
        link_path = wiki_parts(links[0][1])[0].split("#", 1)[0].strip()
        if link_path != chosen["source_path"]:
            raise ValueError(f"证据链接与固定来源不一致：{link_path} != {chosen['source_path']}")
    elif not evidence["link"].startswith("https://"):
        raise ValueError("证据链接须为固定附件 Wiki 或来源官方 HTTPS 链接")
    evidence.update(source_sha256=chosen["source_sha256"], source_path=chosen["source_path"],
                    manifests=[entry["manifest"] for entry in candidates], binding_status="fixed")


def compile_index(root, changes=None):
    """Compile the whole formal set, optionally with read-only proposed texts.

    ``changes`` maps formal vault-relative ``*.md`` targets to complete text or
    None (proposed deletion). It never writes overlays into the real vault.
    """
    root = Path(root).resolve()
    catalog, manifests, errors = _source_catalog(root)
    notes, objects, unregistered = {}, {}, []
    paths = {str(path.relative_to(root)): path for path in note_paths(root)}
    overlays = {}
    for value, text in (changes or {}).items():
        path = inside(root, value)
        relative = str(path.relative_to(root))
        if path.suffix != ".md" or relative.split("/", 1)[0] not in {"论文", "知识", "专题"}:
            raise ValueError(f"候选只可覆盖正式三个目录的 Markdown：{value}")
        if text is not None and not isinstance(text, str):
            raise ValueError("候选覆盖必须为完整 Markdown 字符串或 null 删除")
        if relative in overlays:
            raise ValueError(f"同一候选目标有多个路径别名：{relative}")
        overlays[relative] = text
        if text is None:
            paths.pop(relative, None)
        else:
            paths[relative] = path
    for relative, path in sorted(paths.items()):
        if relative in overlays:
            full_text = overlays[relative]
            raw = full_text.encode("utf-8")
            frontmatter = re.match(r"\A---\r?\n.*?\r?\n---(?:\r?\n|$)", full_text, re.S)
            body = full_text[frontmatter.end():] if frontmatter else full_text
        else:
            raw = path.read_bytes()
            full_text = raw.decode("utf-8")
            _, body = read_note(path)
        line_offset = full_text[:len(full_text) - len(body)].count("\n")
        records, issues = parse_note(relative, body, line_offset=line_offset)
        errors.extend(issues)
        notes[relative] = {"sha256": digest(raw), "registered_objects": len(records)}
        if not records and not issues:
            unregistered.append(relative)
        for record in records:
            record["note_sha256"] = digest(raw)
            for evidence in record["evidence"]:
                try:
                    _bind_evidence(evidence, catalog)
                except ValueError as exc:
                    evidence["binding_status"] = "unresolved"
                    errors.append({"path": relative, "line": record["line"], "message": str(exc)})
            objects[record["key"]] = record
    dependencies, relations = [], []
    for key, record in objects.items():
        refs = list(record.get("dependencies", []))
        if record["kind"] == "relation":
            refs += [record["source"], record["target"]]
            relations.append({"key": key, "source": record["source"], "target": record["target"],
                              "type": record["relation"], "attribution": record["attribution"]})
            # A depends-on edge points from the dependent judgment to its premise.
            if record["relation"] == "依赖" and record["attribution"] != "待核验线索":
                dependencies.append({"dependent": record["source"], "premise": record["target"], "via": key})
            elif record["relation"] == "证据支持" and record["attribution"] != "待核验线索":
                dependencies.append({"dependent": record["target"], "premise": record["source"], "via": key})
        refs += record.get("participants", []) + record.get("affected", [])
        for ref in refs:
            if ref not in objects:
                errors.append({"path": record["path"], "line": record["line"],
                               "message": f"显式语义引用未指向注册对象：{ref}；旧正文需人工选择是否注册"})
        for ref in record.get("dependencies", []):
            if ref in objects and objects[ref]["kind"] != "claim":
                errors.append({"path": record["path"], "line": record["line"], "message": f"论证依赖目标须为核心命题：{ref}"})
            dependencies.append({"dependent": key, "premise": ref, "via": key})
    source_versions = defaultdict(set)
    for entry in catalog:
        for identity in entry["identities"]:
            source_versions[f"{identity} / {entry['version']}"].add(entry["source_sha256"])
    source_versions = {key: sorted(value) for key, value in sorted(source_versions.items())}
    snapshot = {"notes": notes, "manifests": manifests, "sources": source_versions}
    return {"format": FORMAT, "derived_at": utc_now(), "root": str(root), "snapshot": snapshot,
            "snapshot_sha256": digest(json.dumps(snapshot, ensure_ascii=False, sort_keys=True)),
            "objects": objects, "dependencies": dependencies, "relations": relations,
            "unregistered_notes": unregistered, "errors": errors, "valid": not errors,
            "candidate_projection": bool(changes),
            "limits": ["只编译显式字段；未注册正文不表示缺乏科学支持。", "定位已核不表示语义支持或独立实验复现。"]}


def stale_status(root, index):
    actual_notes = {str(path.relative_to(root)): digest(path.read_bytes()) for path in note_paths(root)}
    recorded = {key: value["sha256"] for key, value in index["snapshot"]["notes"].items()}
    changed = sorted(key for key in actual_notes.keys() | recorded.keys() if actual_notes.get(key) != recorded.get(key))
    _, manifests, source_errors = _source_catalog(root)
    old_manifests = index["snapshot"]["manifests"]
    changed_manifests = sorted(key for key in manifests.keys() | old_manifests.keys()
                               if manifests.get(key) != old_manifests.get(key))
    return {"stale": bool(changed or changed_manifests or source_errors), "changed_notes": changed,
            "changed_manifests": changed_manifests, "source_errors": source_errors}


def changed_objects(previous, current):
    old = previous.get("objects", {}) if previous else {}
    new = current.get("objects", {})
    return {key: {"before": old.get(key, {}).get("fingerprint"), "after": new.get(key, {}).get("fingerprint")}
            for key in old.keys() | new.keys()
            if key not in old or key not in new or
            _scientific_fingerprint(old[key]) != _scientific_fingerprint(new[key])}


def affected_candidates(index, changed, sources=()):
    """Cycle-safe candidates only. Actual semantic change remains a human decision."""
    reverse = defaultdict(set)
    for edge in index["dependencies"]:
        reverse[edge["premise"]].add(edge["dependent"])
    reasons = defaultdict(list)
    initial = set(changed)
    for key in changed:
        reasons[key].append("注册正文或字段实际变更")
    for key, record in index["objects"].items():
        if any(evidence["source_id"] in sources for evidence in record.get("evidence", [])):
            initial.add(key)
            reasons[key].append("固定来源出现版本/内容变化：需复核定位和判断")
    queue, visited = deque(sorted(initial)), set()
    while queue:
        key = queue.popleft()
        if key in visited:
            continue
        visited.add(key)
        for dependent in sorted(reverse[key]):
            reasons[dependent].append(f"依赖变更对象：{key}")
            if dependent not in visited:
                queue.append(dependent)
    return [{"key": key, "status": "待处理", "reasons": list(dict.fromkeys(reasons[key])),
             "note_sha256": index["objects"].get(key, {}).get("note_sha256")}
            for key in sorted(visited)]


def build(root):
    target = inside(root, INDEX_PATH)
    with locked(root, "semantic-index"):
        previous = read_json(target) if target.exists() else None
        current = compile_index(root)
        changes = changed_objects(previous, current) if previous else {}
        old_sources = previous.get("snapshot", {}).get("sources", {}) if previous else {}
        new_sources = current["snapshot"]["sources"]
        changed_source_keys = sorted(key for key in old_sources.keys() | new_sources.keys()
                                     if old_sources.get(key) != new_sources.get(key)) if previous else []
        changed_sources = sorted({key.rsplit(" / ", 1)[0] for key in changed_source_keys})
        # Preserve previously explicit dependencies when an object is removed.
        impact_index = dict(current)
        if previous:
            impact_index["dependencies"] = current["dependencies"] + previous.get("dependencies", [])
        candidates = affected_candidates(impact_index, changes, changed_sources)
        change = {"format": "paper-semantic-impact-v1", "created_at": utc_now(),
                  "previous_snapshot": previous.get("snapshot_sha256") if previous else None,
                  "current_snapshot": current["snapshot_sha256"], "changed_objects": changes,
                  "candidates": candidates, "source_changes": changed_sources,
                  "source_version_changes": changed_source_keys,
                  "note": "文本差异只生成复核候选，未自动更改科学裁决。"}
        if changes or changed_sources:
            event_id = digest(json.dumps({"before": change["previous_snapshot"], "after": change["current_snapshot"]}, sort_keys=True))[:24]
            event = inside(root, f".research/updates/semantic-impact/{event_id}.json")
            if not event.exists():
                write_json(event, change)
            change["event_path"] = str(event.relative_to(root))
        write_json(target, current)
    return {"index": str(target.relative_to(root)), "valid": current["valid"],
            "objects": len(current["objects"]), "unregistered_notes": len(current["unregistered_notes"]),
            "errors": current["errors"], "change": change}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="research.py derive", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "status", "impact", "resolve", "format"):
        command = sub.add_parser(name)
        command.add_argument("--root", type=Path, default=default_root())
        if name == "format":
            command.add_argument("--input", required=True, help="已核 Markdown 的库根相对路径")
            command.add_argument("--output", required=True, help="新草稿路径；仅限 .research/drafts 或 work")
            command.add_argument("--bindings", help="旧登记块ID→正文绑定JSON；旧记录迁移必需")
        if name == "impact":
            command.add_argument("--since", help="旧派生索引的库根相对路径；比较实际对象变化")
            command.add_argument("--source", action="append", default=[], help="出现新版本的固定来源 ID；只标需复核")
            command.add_argument("--event", required=True, type=safe_id)
        if name == "resolve":
            command.add_argument("--event", required=True, type=safe_id)
            command.add_argument("--target", required=True)
            command.add_argument("--decision", required=True, choices=("已核", "需修订", "不变", "待处理"))
            command.add_argument("--reason", required=True)
            command.add_argument("--evidence", action="append", default=[])
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.command == "format":
        source, target = inside(root, args.input), inside(root, args.output)
        relative = target.relative_to(root)
        if relative.parts[:2] not in {(".research", "drafts"), (".research", "work")} or target.suffix != ".md":
            raise ValueError("排版仅输出 .research/drafts 或 .research/work 中的 Markdown；正式页走审阅提交")
        if source == target:
            raise ValueError("排版输出须为新草稿，不覆盖输入")
        original = source.read_text(encoding="utf-8")
        bindings = read_json(inside(root, args.bindings)) if args.bindings else None
        if bindings is not None and not isinstance(bindings, dict):
            raise ValueError("bindings须为 块ID→正文绑定 的映射")
        formatted, count = bind_registrations(original, bindings, str(source.relative_to(root)))
        with locked(root, f"semantic-format:{relative}"):
            if source.read_text(encoding="utf-8") != original:
                raise ValueError("输入在排版时改变，停止输出")
            if target.exists() and target.read_text(encoding="utf-8") != formatted:
                raise ValueError("输出已有不同内容，保留它并选择新草稿路径")
            atomic_text(target, formatted)
        result = {"output": str(relative), "sha256": digest(formatted.encode("utf-8")),
                  "formatted_records": count, "identity_preserved": True,
                  "review_required": bool(count),
                  "limitation": "类别、裁决、关系及固定证据身份保持；正文说明重新绑定须实际审阅，不宣称语义等价"}
    elif args.command == "build":
        result = build(root)
    elif args.command == "status":
        target = inside(root, INDEX_PATH)
        if not target.exists():
            result = {"exists": False, "stale": True, "next": "derive build"}
        else:
            index = read_json(target)
            result = {"exists": True, "valid": index["valid"], **stale_status(root, index)}
    elif args.command == "impact":
        target = inside(root, INDEX_PATH)
        index = read_json(target)
        if stale_status(root, index)["stale"] or not index["valid"]:
            raise ValueError("索引已失效或有注册错误；先 derive build 并处理定位错误")
        previous = read_json(inside(root, args.since)) if args.since else None
        changes = changed_objects(previous, index) if previous else {}
        if not changes and not args.source:
            raise ValueError("未给出实际对象变化或新版本来源，不创建空更新事件")
        impact_index = dict(index)
        if previous:
            impact_index["dependencies"] = index["dependencies"] + previous.get("dependencies", [])
        event = {"format": "paper-semantic-impact-v1", "created_at": utc_now(), "event_id": args.event,
                 "previous_snapshot": previous.get("snapshot_sha256") if previous else None,
                 "current_snapshot": index["snapshot_sha256"], "changed_objects": changes,
                 "source_changes": args.source, "candidates": affected_candidates(impact_index, changes, args.source),
                 "note": "候选状态仅描述处理进度；不能自动覆盖 Markdown 或宣布旧命题为假。"}
        path = inside(root, f".research/updates/semantic-impact/{args.event}.json")
        with locked(root, f"semantic-impact:{args.event}"):
            if path.exists():
                existing = read_json(path)
                if existing["current_snapshot"] != event["current_snapshot"] or existing["source_changes"] != event["source_changes"]:
                    raise ValueError("同事件 ID 已绑定其他变化；使用新 ID")
                event = existing
            else:
                write_json(path, event)
        result = {"event": str(path.relative_to(root)), **event}
    else:
        path = inside(root, f".research/updates/semantic-impact/{args.event}.json")
        with locked(root, f"semantic-impact:{args.event}"):
            event = read_json(path)
            candidate = next((item for item in event["candidates"] if item["key"] == args.target), None)
            if not candidate:
                raise ValueError("目标不在此事件的真实影响候选中")
            note = inside(root, args.target.split("#^", 1)[0])
            current_hash = digest(note.read_bytes()) if note.exists() else None
            if candidate.get("note_sha256") != current_hash:
                raise ValueError("候选正文已改变；重建索引并为实际新版本建立事件后复核")
            decision = {"at": utc_now(), "decision": args.decision, "reason": args.reason,
                        "evidence": args.evidence, "note_sha256": current_hash}
            candidate.setdefault("history", []).append(decision)
            candidate.update(status=args.decision, resolution=decision)
            write_json(path, event)
        result = {"event": str(path.relative_to(root)), "candidate": candidate,
                  "next": "科学判断需要按正常审阅更新 Markdown；处理记录不会代替正文。"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("valid") is False else 0
