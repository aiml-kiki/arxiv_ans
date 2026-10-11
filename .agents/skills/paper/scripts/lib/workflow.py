"""Unit-owned checkpoints and actual host calls; never calls an LLM.

Run through ``uv run .../research.py --root VAULT workflow ...``. Fields and
hashes are checked here; semantic checks remain attributed to their real reviewer.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
import tempfile
import re
from pathlib import Path
from typing import Any

from .core import atomic_text, default_root, digest, inside, locked, read_json, safe_id, utc_now, write_json

PACKAGE = Path(__file__).resolve().parents[2]
CATALOG_PATH = PACKAGE / "assets/schemas/task-catalog.json"
STAGES = {
    "read": [("R1", ["B1"], "orientation.md"),
             ("R2a", ["B2", "B3"], "mechanism.md"),
             ("R2b", ["B4", "B5"], None),
             ("R2c", ["B6", "B7", "B8"], "evidence.md"),
             ("R3a", ["B9", "B10"], "reconstruction.md"),
             ("R3b", ["B11", "B12"], "reconstruction.md"),
             ("R4", ["B13"], "composition.md")],
    "compare": [("M1", ["C1", "C2"], "alignment.md"),
                ("M2", ["C3", "C4", "C5"], "evolution.md"),
                ("M3", ["C6", "C7", "C8"], "evidence.md"),
                ("M4", ["C9"], "composition.md")],
    "trace": [("T0", ["D8"], "incremental.md"),
              ("T1", ["D1"], "scope.md"),
              ("T2", ["D2", "D3"], "search-screen.md"),
              ("T3", ["D4", "D3"], "snowball.md"),
              ("T4", ["D5", "D6"], "lineage.md"),
              ("T5", ["D7"], "coverage.md"),
              ("T6", ["C9"], "composition.md")],
    "knowledge": [("K1", ["E1", "E2"], "candidates.md"),
                  ("K2", ["E4", "E5", "E6"], "impact.md"),
                  ("K3", ["E3"], "explanation.md"),
                  ("K4", ["E7"], "composition.md")],
}
FINAL = {kind: stages[-1][0] for kind, stages in STAGES.items()}
LIVE = {"executing", "awaiting_review"}
COMPLETE = {"accepted", "skipped"}


def text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}须是具体非空文字")
    return value.strip()


def catalog() -> dict:
    value = read_json(CATALOG_PATH)
    tasks = value.get("tasks", {})
    if not isinstance(tasks, dict) or not tasks:
        raise ValueError("叶任务目录须有实际任务")
    valid_rounds = {stage for stages in STAGES.values() for stage, _, _ in stages} | {
        "every_round", "every_dispatch", "every_return", "candidate_group", "authorized_publish", "boundaries"}
    for key, item in tasks.items():
        if not re.fullmatch(r"[A-F][1-9][0-9]*", key) or not isinstance(item, dict):
            raise ValueError("任务ID/定义无效")
        for field in ("name", "owner", "gate"):
            text(item.get(field), f"任务{key}.{field}")
        rounds = item.get("rounds")
        if not isinstance(rounds, list) or any(stage not in valid_rounds for stage in rounds):
            raise ValueError(f"任务阶段/范围引用无效: {key}")
        if item.get("conditional") and not item.get("applicability"):
            raise ValueError(f"条件任务缺少适用判据: {key}")
    for kind, stages in STAGES.items():
        for stage, leaves, _ in stages:
            for key in leaves:
                if key not in tasks or stage not in tasks[key].get("rounds", []):
                    raise ValueError(f"阶段任务引用无效: {kind}:{stage}:{key}")
    return tasks


def state_path(root: Path, run: str) -> Path:
    return inside(root, f".research/work/{safe_id(run)}/workflow.json")


def archived_state_path(root: Path, run: str) -> Path:
    return inside(root, f".research/records/runs/{safe_id(run)}/workflow.json")


def snapshot(root: Path, value: str) -> dict:
    path = inside(root, value)
    if not path.is_file() or not path.read_bytes().strip():
        raise ValueError(f"固定材料/产物不存在或为空: {value}")
    return {"path": str(path.relative_to(root)), "sha256": digest(path.read_bytes())}


def verify(root: Path, files: list[dict]) -> None:
    for item in files:
        now = snapshot(root, item["path"])
        if now["sha256"] != item["sha256"]:
            raise ValueError(f"已固定文件变动: {item['path']}；请定向重开或刷新来源")


def preserved_version(root: Path, run: str, ident: str, stage: str, file: dict, label: str) -> dict:
    """A revision may overwrite a work path; accepted scientific versions survive."""
    original = inside(root, file["path"])
    path = inside(root, f".research/work/{run}/units/{ident}/versions/{stage}/{label}-{file['sha256']}{original.suffix or '.bin'}")
    raw = original.read_bytes()
    if digest(raw) != file["sha256"]:
        raise ValueError("保存版本时工作文件已变动，停止提交")
    if path.exists():
        if digest(path.read_bytes()) != file["sha256"]:
            raise ValueError("内容寻址的历史版本损坏，不覆盖")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".version-")
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return snapshot(root, str(path.relative_to(root)))


def freeze_inputs(root: Path, inputs: list[str], kind: str) -> tuple[list[dict], list[dict]]:
    if not isinstance(inputs, list):
        raise ValueError("inputs须为库根相对路径列表")
    files, manifests = {}, []
    for value in inputs:
        item = snapshot(root, value)
        files[item["path"]] = item
        path = inside(root, item["path"])
        if path.suffix != ".json":
            continue
        data = read_json(path)
        if not isinstance(data, dict):
            continue
        if not path.is_relative_to(inside(root, ".research/sources")):
            continue  # A provenance/query JSON may also use source_path; it is not a parser manifest.
        if "source_path" not in data:
            raise ValueError("来源目录内的manifest缺少source_path，不能用普通JSON充当解析缓存")
        source_kind = data.get("source_kind", "pdf")
        if source_kind not in {"pdf", "html", "text"}:
            raise ValueError("来源manifest的source_kind须为实际pdf/html/text")
        if not isinstance(data.get("page_count"), int) or isinstance(data["page_count"], bool):
            raise ValueError("来源manifest的page_count须为整数")
        if source_kind == "pdf" and data["page_count"] < 1:
            raise ValueError("PDF来源须有实际正整数page_count")
        if source_kind != "pdf" and (data["page_count"] != 0 or
                not isinstance(data.get("section_count"), int) or isinstance(data["section_count"], bool) or data["section_count"] < 1):
            raise ValueError("非PDF来源须page_count:0及实际正整数section_count，不能将解析段当PDF页")
        if not isinstance(data.get("blocks"), list):
            raise ValueError("来源manifest须有真实blocks列表，不能猜测解析覆盖")
        original = snapshot(root, data["source_path"])
        if original["sha256"] != data.get("source_sha256"):
            raise ValueError(f"manifest的原始来源哈希不符: {item['path']}")
        files[original["path"]] = original
        block_ids = set()
        for block in data.get("blocks", []):
            if block.get("id") in block_ids or not isinstance(block.get("id"), str) or not block["id"].strip():
                raise ValueError("manifest块须有独立非空ID")
            block_ids.add(block["id"])
            if digest(block.get("text", "")) != block.get("text_sha256"):
                raise ValueError(f"manifest块内容哈希不符: {block.get('id')}")
            field, count = ("page", data["page_count"]) if source_kind == "pdf" else ("section", data["section_count"])
            position = block.get(field)
            if not isinstance(position, int) or isinstance(position, bool) or not 1 <= position <= count:
                raise ValueError(f"manifest块{field}不是实际来源位置: {block['id']}")
            if source_kind != "pdf" and block.get("page") is not None:
                raise ValueError("非PDF解析块不可附造PDF页码")
        manifests.append({"path": item["path"], "sha256": item["sha256"],
                          "source_path": original["path"], "source_sha256": original["sha256"],
                          "paper_id": data.get("paper_id"), "version": data.get("version"),
                          "source_kind": source_kind, "page_count": data.get("page_count"),
                          "section_count": data.get("section_count"), "parser": data.get("parser")})
    if kind == "read" and not manifests:
        raise ValueError("完整read须给出固定来源manifest，先用source能力取得/解析材料")
    return list(files.values()), manifests


def task_decision(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("status") not in {"selected", "not_applicable", "pending"}:
        raise ValueError("任务决定须为status:selected|not_applicable|pending及具体reason")
    return {"status": value["status"], "reason": text(value.get("reason"), "任务范围理由")}


def find_unit(state: dict, ident: str) -> dict:
    if ident not in state["units"]:
        raise ValueError(f"单元不存在: {ident}")
    return state["units"][ident]


def find_round(unit: dict, ident: str) -> dict:
    for item in unit["rounds"]:
        if item["id"] == ident:
            return item
    raise ValueError(f"轮次不属于本单元: {ident}")


def current_round(unit: dict) -> dict | None:
    return next((item for item in unit["rounds"] if item["status"] not in COMPLETE), None)


def complete(unit: dict) -> bool:
    return current_round(unit) is None


def skip_inapplicable(unit: dict) -> None:
    """Only explicit not-applicable decisions can close a round without work."""
    for item in unit["rounds"]:
        if item["status"] in {"ready", "revision_required", "interrupted"}:
            decisions = [unit["tasks"][key] for key in item["tasks"]]
            if all(value["status"] == "not_applicable" for value in decisions):
                item["status"] = "skipped"
                item["skip_reasons"] = {key: unit["tasks"][key]["reason"] for key in item["tasks"]}


def initialize(root: Path, run: str, plan: dict, existing: dict | None = None) -> dict:
    if archived_state_path(root, run).exists():
        raise ValueError("该运行已有归档状态；仅status/recovery可只读查看。继续研究须使用新run引用已有材料，不能重建或覆盖旧运行")
    if existing is None and state_path(root, run).exists():
        raise ValueError("已有运行；用status/next/recovery接续，禁止重新初始化覆盖")
    goal = text(plan.get("goal"), "具体研究目标")
    if not isinstance(plan.get("constraints", []), list):
        raise ValueError("constraints须为用户约束记录列表")
    autonomy = plan.get("autonomy", "unit")
    if autonomy not in {"unit", "interactive"}:
        raise ValueError("autonomy须为unit或interactive")
    specs = plan.get("units")
    if not isinstance(specs, list) or not specs:
        raise ValueError("plan.units须是非空列表")
    tasks = catalog()
    units = deepcopy(existing["units"]) if existing else {}
    outputs = {inside(root, unit["draft"]) for unit in units.values()}
    target_writers = deepcopy(existing.get("candidate_writers", {})) if existing else {}
    limit = plan.get("max_workers", 2)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("max_workers须为正整数，默认2；提高须有实际资源/用户依据")
    if limit > 2:
        text(plan.get("worker_limit_reason"), "提高并发上限理由")
    for spec in specs:
        ident, kind = safe_id(spec["id"]), spec.get("kind")
        if ident in units or kind not in STAGES:
            raise ValueError("单元ID重复或kind不是read/compare/trace/knowledge")
        unit_autonomy = spec.get("autonomy", autonomy)
        if unit_autonomy not in {"unit", "interactive"}:
            raise ValueError("单元autonomy须为unit或interactive")
        files, manifests = freeze_inputs(root, spec.get("inputs", []), kind)
        output_dir = inside(root, f".research/work/{run}/units/{ident}/results")
        draft_dir = inside(root, f".research/drafts/{run}/units/{ident}")
        if output_dir.relative_to(root).parts[0] != ".research" or draft_dir.relative_to(root).parts[0] != ".research":
            raise ValueError("工作/候选目录不能经软链接指向正式笔记目录")
        draft = inside(root, spec.get("draft", str(draft_dir.relative_to(root) / "candidate.md")))
        if draft.suffix != ".md" or not draft.is_relative_to(draft_dir) or draft in outputs:
            raise ValueError("候选须是本单元独占draft_dir内Markdown，不能指向正式笔记")
        outputs.add(draft)
        target = spec.get("target")
        if target is not None:
            target = validate_target(root, target)
            if target in target_writers:
                raise ValueError("每个最终目标只能声明一个候选写者: " + target)
            target_writers[target] = ident
        applicable = {key for _, leaves, _ in STAGES[kind] for key in leaves}
        decisions = {key: {"status": "selected", "reason": f"当前{kind}目标需要此叶任务"} for key in applicable}
        for key in applicable:
            if tasks[key].get("conditional"):
                decisions[key] = {"status": "pending", "reason": tasks[key]["applicability"]}
        if kind == "trace" and not spec.get("incremental", False):
            decisions["D8"] = {"status": "not_applicable", "reason": "本单元是新范围研究，没有上次研究集合供增量复核"}
        if kind == "trace" and not spec.get("compose_topic", True):
            decisions["C9"] = {"status": "not_applicable", "reason": text(spec.get("no_topic_reason"), "不生成专题理由")}
        for key, value in spec.get("task_decisions", {}).items():
            if key not in applicable:
                raise ValueError(f"{key}不是{kind}轮次任务")
            decisions[key] = task_decision(value)
        rounds = [{"id": stage, "base": stage, "tasks": leaves, "guide": guide,
                   "status": "ready", "dispatches": [], "pending": None, "accepted": None,
                   "history": []} for stage, leaves, guide in STAGES[kind]]
        dependencies = []
        for value in spec.get("depends_on", []):
            dep = {"unit": value, "stage": None} if isinstance(value, str) else dict(value)
            dependencies.append({"unit": safe_id(dep["unit"]), "stage": dep.get("stage")})
        units[ident] = {"id": ident, "kind": kind, "goal": spec.get("goal", goal), "inputs": files,
                        "sources": manifests, "depends_on": dependencies, "tasks": decisions,
                        "rounds": rounds, "owner": None, "owner_history": [], "handover": [],
                        "autonomy": unit_autonomy, "owner_active": False,
                        "output_dir": str(output_dir.relative_to(root)), "draft_dir": str(draft_dir.relative_to(root)),
                        "draft": str(draft.relative_to(root)), "target": target,
                        "declared_input_paths": list(dict.fromkeys(spec.get("inputs", []))),
                        "source_windows": spec.get("source_windows", {}), "question": spec.get("question"),
                        "recovery_notes": [], "requested_profile": spec.get("requested_profile"),
                        "science_draft": str(output_dir.parent.relative_to(root) / "science.md"),
                        "science_state": None, "transactions": {}, "resource_reads": {},
                        "input_history": [], "bootstrap_instances": {},
                        "candidate_kind": {"read": "paper", "compare": "comparison", "trace": "topic", "knowledge": "knowledge"}[kind]}
        skip_inapplicable(units[ident])
    validate_dag(units)
    for unit in units.values():
        if unit["kind"] == "compare" and len({dep["unit"] for dep in unit["depends_on"]}) + len(unit["declared_input_paths"]) < 2:
            raise ValueError("compare至少需要两个独立论文单元或已有精读输入")
    chosen = {key for unit in units.values() for key in unit["tasks"]}
    selection = {key: {"status": "selected" if key in chosen or key.startswith(("A", "F")) else "out_of_scope",
                       "reason": "本运行所选能力或横向保障" if key in chosen or key.startswith(("A", "F")) else f"当前目标所选能力为{','.join(sorted({u['kind'] for u in units.values()}))}"}
                 for key in tasks}
    return {"schema_version": 2, "run_id": run, "goal": goal, "created": utc_now(), "updated": utc_now(),
            "max_workers": limit, "autonomy": autonomy, "units": units, "task_scope": selection,
            "constraints": plan.get("constraints", []), "constraint_history": [], "events": [],
            "authorization": plan.get("authorization", {}), "integration": None, "candidate_writers": target_writers,
            "semantic_verification": "attributed_human_or_agent_checks_not_performed_by_script"}


def validate_dag(units: dict) -> None:
    for ident, unit in units.items():
        identities = set()
        for dep in unit["depends_on"]:
            other = dep["unit"]
            if other not in units or other == ident or (other, dep["stage"]) in identities:
                raise ValueError("依赖不存在、自引用或重复")
            identities.add((other, dep["stage"]))
            if dep["stage"] is not None:
                find_round(units[other], dep["stage"])
    visited, visiting = set(), set()
    def visit(ident: str) -> None:
        if ident in visiting:
            raise ValueError("执行单元依赖有环；科学语义图可有环，但任务DAG不能有环")
        if ident in visited:
            return
        visiting.add(ident)
        for dep in units[ident]["depends_on"]:
            visit(dep["unit"])
        visiting.remove(ident)
        visited.add(ident)
    for ident in units:
        visit(ident)


def prerequisite_records(root: Path, state: dict, unit: dict) -> list[dict]:
    verify(root, unit["inputs"])
    records = []
    for dep in unit["depends_on"]:
        other = find_unit(state, dep["unit"])
        needed = find_round(other, dep["stage"]) if dep["stage"] else other["rounds"][-1]
        if needed["status"] not in COMPLETE or (dep["stage"] is None and not complete(other)):
            raise ValueError(f"依赖尚未接受: {dep['unit']}:{needed['id']}")
        for stage in other["rounds"]:
            if stage["accepted"]:
                verify(root, stage["accepted"]["files"])
                verify(root, stage["accepted"].get("evidence_files", []))
                verify(root, [{"path": item["draft"], "sha256": item["sha256"]}
                              for item in stage["accepted"].get("candidates", [])])
                records.append(stage["accepted"])
            if stage["id"] == needed["id"]:
                break
    for stage in unit["rounds"]:
        if stage["accepted"]:
            verify(root, stage["accepted"]["files"])
            verify(root, stage["accepted"].get("evidence_files", []))
            verify(root, [{"path": item["draft"], "sha256": item["sha256"]}
                          for item in stage["accepted"].get("candidates", [])])
            records.append(stage["accepted"])
    return records


def validate_target(root: Path, value: str) -> str:
    path = inside(root, value)
    if path.suffix != ".md" or path.relative_to(root).parts[0] not in {"论文", "知识", "专题"}:
        raise ValueError("candidate target须为论文/知识/专题内正式Markdown路径")
    return str(path.relative_to(root))


def selected_tasks(unit: dict, stage: dict) -> list[str]:
    unresolved = [key for key in stage["tasks"] if unit["tasks"][key]["status"] == "pending"]
    if unresolved:
        raise ValueError(f"本轮条件任务尚未判范围: {','.join(unresolved)}；用tasks给出selected或not_applicable与具体依据")
    return [key for key in stage["tasks"] if unit["tasks"][key]["status"] == "selected"]


def guide_refs(unit: dict, stage: dict, active: list[str]) -> list[dict]:
    capability = PACKAGE.parent / f"paper-{unit['kind']}"
    names = [stage["guide"]] if stage["guide"] else [name for key, name in (("B4", "learning.md"), ("B5", "proof.md")) if key in active]
    section = {"R3a": "替代与作者辩护", "R3b": "区分验证与细节保留"}.get(stage["base"])
    refs = []
    for name in names:
        path = capability / "references" / name
        if not path.is_file():
            raise ValueError(f"当前指南缺失: {path}")
        refs.append({"path": str(path), "section": section, "sha256": digest(path.read_bytes())})
    return refs


def autonomy_mode(unit: dict) -> str:
    """Missing fields are legacy interactive semantics, never an implicit migration."""
    return unit.get("autonomy", "interactive")


def owner_running(unit: dict) -> bool:
    stage = current_round(unit)
    if not unit["owner"] or unit["owner"]["mode"] != "agent":
        return False
    if autonomy_mode(unit) == "unit":
        return unit.get("owner_active", False)
    return bool(stage and stage["status"] == "executing")


def record_reference(root: Path, state: dict, unit: dict, stage: dict, field: str) -> dict:
    path = archived_state_path(root, state["run_id"])
    if not path.is_file():
        path = state_path(root, state["run_id"])
    index = unit["rounds"].index(stage)
    value = stage[field]
    return {"path": str(path.relative_to(root)),
            "json_pointer": f"/units/{unit['id']}/rounds/{index}/{field}",
            "record_sha256": digest(json.dumps(value, ensure_ascii=False, sort_keys=True))}


def short_record(record: dict, detail: str = "compact", reference: dict | None = None) -> dict:
    if detail == "full":
        return {key: record.get(key) for key in ("unit", "stage", "files", "answers", "evidence", "coverage",
                                                "judgment_changes", "critical_details", "gaps", "finding", "delta",
                                                "science_state_ref", "input_versions", "input_binding", "sources", "transaction_id", "candidates", "no_change_reason")}
    result = {key: record.get(key) for key in ("unit", "stage")}
    result["science_state_ref"] = record.get("science_state_ref")
    result["counts"] = {key: len(record.get(key, [])) for key in
                        ("answers", "evidence", "coverage", "judgment_changes", "critical_details", "gaps")}
    result["review_kind"] = record.get("finding", {}).get("review_kind", "legacy_coordinator_review")
    if reference:
        result["record_ref"] = reference
        if record.get("science_state_ref"):
            result["interface_ref"] = {**record["science_state_ref"], "json_pointer": "/critical_details"}
        else:
            result["interface_ref"] = {**reference, "json_pointer": reference["json_pointer"] + "/critical_details",
                                       "record_sha256": digest(json.dumps(record.get("critical_details", []), ensure_ascii=False, sort_keys=True))}
    return result


def short_revision(value: dict, detail: str = "compact") -> dict:
    if detail == "full":
        result = {key: value[key] for key in ("decision", "reason", "finding", "at") if key in value}
    else:
        result = {key: value[key] for key in ("decision", "at") if key in value}
        if value.get("reason"):
            result["reason"] = value["reason"][:400]
        finding = value.get("finding", {})
        if finding:
            changes = finding.get("required_changes", [])
            result.update(review_kind=finding.get("review_kind", "legacy_coordinator_review"),
                          summary=finding.get("summary", "")[:400],
                          required_changes=[str(item)[:400] for item in changes[:8]],
                          required_change_count=len(changes))
    if value.get("previous_result"):
        if detail == "full":
            result["previous_files"] = value["previous_result"]["files"]
        else:
            result["previous_version_count"] = len(value["previous_result"]["files"])
    return result


def archive_view(root: Path, run: str, value: dict, command: str) -> dict:
    """Describe archival reality separately from scientific acceptance."""
    index = inside(root, f".research/records/runs/{run}/archive.json")
    info = read_json(index) if index.is_file() else {}
    if info and info.get("run") != run:
        raise ValueError("归档索引与运行ID不符；保留档案，不能猜测清理终态")
    value["lifecycle"] = "archived_read_only"
    value["archive"] = {"state_path": str(archived_state_path(root, run).relative_to(root)),
                        "index_path": str(index.relative_to(root)) if index.is_file() else None,
                        "cleanup_status": info.get("status", "unknown"), "read_only": True}
    value["next"] = "此运行仅可查看归档；需要继续研究时使用新run引用固定来源和已接受结果，不能续派或写回旧状态"
    if command == "recovery":
        for unit in value["units"].values():
            unit["next"] = value["next"]
    return value


def relevant_records(root: Path, state: dict, unit: dict, stage: dict, records: list[dict], detail: str = "compact") -> tuple[list[dict], list[dict]]:
    """Select semantic inputs before packaging; other accepted versions stay addressable."""
    own_needs = {
        "R1": set(), "R2a": {"R1"}, "R2b": {"R1", "R2a"}, "R2c": {"R1", "R2a", "R2b"},
        "R3a": {"R2a", "R2b", "R2c"}, "R3b": {"R2a", "R2b", "R2c", "R3a"},
        "M1": set(), "M2": {"M1"}, "M3": {"M1", "M2"},
        "T0": set(), "T1": {"T0"}, "T2": {"T0", "T1"}, "T3": {"T1", "T2", "T3"},
        "T4": {"T1", "T2", "T3"}, "T5": {"T1", "T4"},
        "K1": set(), "K2": {"K1"}, "K3": {"K1", "K2"},
    }
    dependency_records = {}
    for record in records:
        if record["unit"] != unit["id"]:
            dependency_records.setdefault(record["unit"], []).append(record)
    # Only choose within the declared dependency boundary. A later final record
    # outside that boundary is not an input to this stage-specific dependency.
    dependency_primary = {}
    for ident, available in dependency_records.items():
        final = FINAL[find_unit(state, ident)["kind"]]
        chosen = next((record for record in reversed(available)
                       if record["stage"].split("-", 1)[0] == final), available[-1])
        dependency_primary[ident] = chosen["stage"]
    composition = stage["base"] == FINAL[unit["kind"]]
    primary, addressable = [], []
    for record in records:
        base = record["stage"].split("-", 1)[0]
        own = record["unit"] == unit["id"]
        keep = (own and (composition or base in own_needs.get(stage["base"], set()))) or (
            not own and record["stage"] == dependency_primary.get(record["unit"]))
        if keep:
            source_unit = find_unit(state, record["unit"])
            source_stage = find_round(source_unit, record["stage"])
            primary.append(short_record(record, detail, record_reference(root, state, source_unit, source_stage, "accepted")))
        else:
            addressable.append({key: record[key] for key in ("unit", "stage", "files")})
    return primary, addressable


def protocol_refs(unit: dict, stage: dict, composition: bool) -> list[dict]:
    source_sections = ["实际覆盖与图像核验"]
    if stage["base"] in {"R1", "T0", "T1"}:
        source_sections.insert(0, "身份与固定版本")
    refs = [{"path": str(PACKAGE / "references/protocol-source.md"), "sections": source_sections}]
    claim_sections = ["身份与固定证据"]
    if stage["base"] in {"R2c", "R3a", "R3b", "M2", "M3", "T4", "T5", "K2", "K3"} or composition:
        claim_sections = ["命题字段"]
    if stage["base"] in {"M2", "M3", "T4", "K2"} or composition:
        claim_sections.append("关系字段")
    refs.append({"path": str(PACKAGE / "references/protocol-claim-evidence.md"), "sections": claim_sections})
    if unit["kind"] == "knowledge" and stage["base"] in {"K2", "K4"}:
        refs.append({"path": str(PACKAGE / "references/protocol-update.md"),
                     "sections": {"K2": ["未解问题的稳定单元", "认知更新单元", "影响候选与依赖循环"],
                                  "K4": ["认知更新单元", "持久边界"]}[stage["base"]]})
    if unit["kind"] == "trace" and stage["base"] in {"T0", "T1", "T2", "T3", "T5"}:
        refs.append({"path": str(PACKAGE / "references/protocol-update.md"),
                     "sections": ["可操作的检索留痕"]})
    if composition:
        refs.append({"path": str(PACKAGE / "references/obsidian.md"),
                     "sections": ["可读正文", "链接与锚点", "原生视图和讨论"]})
    return refs


def audit_next_packet(root: Path, state: dict, ident: str, detail: str = "full") -> dict:
    unit = find_unit(state, ident)
    stage = current_round(unit)
    autonomous = autonomy_mode(unit) == "unit"
    if stage is None:
        prerequisite_records(root, state, unit)
        return {"unit": ident, "status": "all_rounds_accepted", "candidate_kind": unit["candidate_kind"],
                "draft": str(inside(root, unit["draft"])), "action": "report_final_result" if autonomous else "group_review",
                "autonomy": autonomy_mode(unit), "publication": "requires_group_review_and_actual_authorization"}
    records = prerequisite_records(root, state, unit)
    active = selected_tasks(unit, stage)
    task_catalog = catalog()
    if stage["status"] == "awaiting_review":
        return {"unit": ident, "stage": stage["id"], "status": stage["status"],
                "autonomy": autonomy_mode(unit), "owner": unit["owner"],
                "action": "owner_local_review" if autonomous else "coordinator_review",
                "pending": short_record(stage["pending"], detail, record_reference(root, state, unit, stage, "pending")),
                "stop": "先记录本轮实际自查或独立审查并accept/revise，再开始下轮" if autonomous else "不可续派下轮；先accept或revise当前结果"}
    refs = guide_refs(unit, stage, active)
    composition = stage["base"] == FINAL[unit["kind"]]
    rules = protocol_refs(unit, stage, composition)
    templates = []
    if composition:
        templates.append(str(PACKAGE.parent / f"paper-{unit['kind']}" / "assets" / f"{unit['candidate_kind']}.md"))
    output = inside(root, Path(unit["output_dir"]) / f"{stage['id']}.md")
    active_agents = [key for key, other in state["units"].items() if owner_running(other)]
    primary, addressable = relevant_records(root, state, unit, stage, records, detail)
    capability_entry = str(PACKAGE.parent / f"paper-{unit['kind']}" / "SKILL.md")
    action = "start_owner_once" if autonomous else "choose_actual_host_route"
    if stage["status"] == "executing":
        action = "owner_execute_current_checkpoint" if autonomous else "await_actual_round_result"
    elif unit["owner"]:
        action = "owner_begin_checkpoint" if autonomous else (
            "followup_same_agent" if unit["owner"]["mode"] == "agent" else "continue_main_current_round")
    for ref in rules:
        ref["sha256"] = digest(Path(ref["path"]).read_bytes())
    return {"unit": ident, "stage": stage["id"], "status": stage["status"],
            "action": action, "autonomy": autonomy_mode(unit), "detail": detail,
            "state_ref": str(state_path(root, state["run_id"]).relative_to(root)),
            "owner": unit["owner"], "goal": stage.get("goal", unit["goal"]), "question": unit["question"],
            "routing_preference": unit["requested_profile"] or {"read": "deep_read", "compare": "deep_compare", "trace": "trace", "knowledge": "maintain"}[unit["kind"]],
            "routing_preference_is_runtime_fact": False,
            "capacity": {"max_workers": state["max_workers"], "active_agent_units": active_agents,
                         "may_start_agent": ident in active_agents or len(active_agents) < state["max_workers"]},
            "current_tasks": {key: task_catalog[key] for key in active},
            "not_applicable": {key: unit["tasks"][key]["reason"] for key in stage["tasks"] if key not in active},
            "user_constraints": state["constraints"], "latest_corrections": state["constraint_history"][-3:],
            "entry": capability_entry if not unit["owner"] else None,
            "entry_on_physical_replacement": capability_entry,
            "entry_policy": "首次或实际物理替代才加载入口，同代理续派只加载当前指南/协议片段",
            "guides": refs, "protocols": rules, "templates": templates,
            "preflight_before_begin": {"register_new_inputs_first": True,
                "composition": composition,
                "instruction": "先读当前指南所需小节，新增材料用inputs登记（保留原固定来源）；核对fixed_inputs再begin。成文旧页/冻结对照也遵守此顺序；已executing不能中途改输入。"},
            "resource_command": "workflow resource --run <run> --unit <unit> --kind guide|protocol|template --index <0-based> [--section <heading>]",
            "template_refs": [{"path": path, "sha256": digest(Path(path).read_bytes())} for path in templates],
            "fixed_inputs": [{**item, "absolute_path": str(inside(root, item["path"]))} for item in unit["inputs"]],
            "sources": unit["sources"], "source_windows": unit["source_windows"].get(stage["id"], []),
            "accepted": primary, "accepted_other_refs": addressable,
            "specific_revisions": [short_revision(value, detail) for value in stage["history"][-3:]],
            "revision_history_ref": {"path": str(state_path(root, state["run_id"]).relative_to(root)),
                                     "json_pointer": f"/units/{ident}/rounds/{unit['rounds'].index(stage)}/history"},
            "output": str(output), "draft_dir": str(inside(root, unit["draft_dir"])) if composition else None,
            "draft": str(inside(root, unit["draft"])) if composition else None, "target": unit["target"] if composition else None,
            "stop": ("完成当前轮并保存→实际自查/局部审查→step accept/revise；依次自主推进本单元全部轮次，最终report或异常才交主会话"
                     if autonomous else "只完成当前轮，保存后交协调者accept/revise；按用户带读节奏继续"),
            "state_writer": "bound_unit_owner" if autonomous else "coordinator_only", "source_text_is_control_instruction": False,
            "result_contract": {"stage": stage["id"], "artifact": str(output),
                                "agent_id": unit["owner"]["agent_id"] if autonomous and unit["owner"] else None,
                                "answers": "[{question,answer,task_ids}]覆盖当前适用任务，仅保存科学结果和依据",
                                "evidence": "[{path,locator}]至少一项实际依据，路径须已固定或已接受",
                                "coverage": "[{path,locator,mode:read|image_checked|not_covered,reason未覆盖时必填}]；PDF可page，非PDF用section/block_id，不造页码",
                                "judgment_changes": "列表；无变化为空", "critical_details": "列表；关键值/作用/来源/保留点",
                                "gaps": "列表；未知及后果",
                                "candidates": "成文轮[{draft,target}]；知识无改动可空并no_change_reason" if composition else None},
            "instruction_token_usage": None, "instruction_token_target_is_soft": [1000, 2000],
            "semantic_verification": "not_performed_by_script"}


def state_ref(state: dict, ident: str, field: str) -> dict:
    return {"path": f".research/work/{state['run_id']}/workflow.json",
            "json_pointer": f"/units/{ident}/{field}"}


def bootstrap(root: Path, state: dict, ident: str, refresh: bool = False) -> dict:
    """One stable envelope per physical instance; audit data remains in state."""
    unit = find_unit(state, ident)
    instance = unit["owner"]["agent_id"] if unit.get("owner") else "unbound"
    envelope = {"unit": ident, "goal": unit["goal"], "question": unit.get("question"),
                "constraints": state["constraints"], "inputs_ref": state_ref(state, ident, "inputs"),
                "sources_ref": state_ref(state, ident, "sources"),
                "draft": unit["draft"], "science_draft": unit.get("science_draft"), "target": unit["target"],
                "responsibility": "本人按当前阶段准备、执行、checkpoint；仅交最终报告或异常；助手不写本人草稿",
                "runtime_ref": {"path": state_ref(state, ident, "inputs")["path"], "json_pointer": "/runtime"},
                "entry": str(PACKAGE.parent / f"paper-{unit['kind']}" / "SKILL.md"),
                "resource_policy": "当前指南按resource读取；同实例相同hash/section默认unchanged，补证显式refresh"}
    binding = digest(json.dumps(envelope, ensure_ascii=False, sort_keys=True))
    loaded = unit.setdefault("bootstrap_instances", {})
    previous = loaded.get(instance)
    previous_sha = previous.get("sha256") if isinstance(previous, dict) else previous
    if previous_sha == binding and not refresh:
        return {"unit": ident, "status": "unchanged", "bootstrap_sha256": binding,
                "ref": state_ref(state, ident, "bootstrap_instances")}
    loaded[instance] = {"sha256": binding, "at": utc_now()}
    if previous and not refresh:
        previous_at = previous.get("at", state["created"]) if isinstance(previous, dict) else state["created"]
        return {"unit": ident, "status": "changes", "goal": unit["goal"],
                "changed_constraints": [item for item in state["constraint_history"] if item["at"] > previous_at],
                "bootstrap_sha256": binding, "ref": state_ref(state, ident, "bootstrap_instances")}
    return {**envelope, "bootstrap_sha256": binding, "instance": instance,
            "measurement": {"characters": len(json.dumps(envelope, ensure_ascii=False)), "tokens": None,
                            "kind": "character_proxy_not_actual_usage"}}


def next_packet(root: Path, state: dict, ident: str, detail: str = "compact") -> dict:
    if detail == "full":
        return audit_next_packet(root, state, ident, detail)
    unit = find_unit(state, ident)
    stage = current_round(unit)
    prerequisite_records(root, state, unit)
    if not stage:
        return {"unit": ident, "status": "all_rounds_accepted", "action": "report_final_result",
                "draft": unit["draft"], "science_state_ref": state_ref(state, ident, "science_state")}
    pending = [key for key in stage["tasks"] if unit["tasks"][key]["status"] == "pending"]
    active = [key for key in stage["tasks"] if unit["tasks"][key]["status"] == "selected"]
    tasks = catalog()
    refs = guide_refs(unit, stage, active)
    composition = stage["base"] == FINAL[unit["kind"]]
    resources = {"guide": refs, "protocol": protocol_refs(unit, stage, composition),
                 "template": [{"path": str(PACKAGE.parent / f"paper-{unit['kind']}" / "assets" / f"{unit['candidate_kind']}.md")}] if composition else []}
    action = ("start_owner_once" if not unit["owner"] else
              "resolve_input_gap" if stage["status"] == "awaiting_input" else
              "record_task_decisions" if pending else
              "local_review" if stage["status"] == "awaiting_review" else
              "read_and_update" if stage["status"] == "executing" else "prepare_and_begin")
    result = {"unit": ident, "stage": stage["id"], "status": stage["status"], "action": action,
              "question": stage.get("goal") or unit.get("question") or unit["goal"],
              "task_refs": active, "pending_tasks": {key: tasks[key]["applicability"] for key in pending},
              "resource_refs": [f"{kind}-{index}" for kind, items in resources.items() for index, ref in enumerate(items)],
              "preflight_before_begin": {"composition": composition},
              "science_refs": {"draft": unit.get("science_draft"), "state": state_ref(state, ident, "science_state")},
              "changed_constraints": [item for item in state["constraint_history"] if item["at"] > unit.get("constraint_seen_at", state["created"])],
              "unresolved": (unit.get("science_state") or {}).get("unresolved", []),
              "source_windows": unit.get("source_windows", {}).get(stage["id"], []),
              "revision": short_revision(stage["history"][-1]) if stage["history"] else None}
    if stage["history"]:
        result["revision_ref"] = {**state_ref(state, ident, "rounds"),
                                  "json_pointer": f"/units/{ident}/rounds/{unit['rounds'].index(stage)}/history"}
    if stage["status"] == "awaiting_input":
        result["input_gap"] = stage.get("input_gap")
    result["measurement"] = {"characters": len(json.dumps(result, ensure_ascii=False)), "tokens": None,
                             "kind": "character_proxy_not_actual_usage"}
    return result


def dispatch(root: Path, state: dict, ident: str, event: dict, owner_start: bool = False) -> dict:
    unit = find_unit(state, ident)
    stage = current_round(unit)
    if autonomy_mode(unit) == "unit" and not unit["owner"] and not owner_start:
        raise ValueError("unit自主单元首次真实spawn用start-owner登记；dispatch仅供旧模式或真实恢复/替代宿主调用")
    resume_review = bool(stage and autonomy_mode(unit) == "unit" and
                         stage["status"] == "awaiting_review" and event.get("resume_review") is True and
                         not unit.get("owner_active", False))
    if not stage or (stage["status"] in LIVE and not resume_review):
        raise ValueError("本轮已执行/待复核或单元已完成，不可重复派发")
    prerequisite_records(root, state, unit)
    if not owner_start:
        selected_tasks(unit, stage)
    if event.get("observed") is not True or event.get("mode") not in {"spawn", "followup", "main"}:
        raise ValueError("dispatch仅登记已发生的宿主执行事件，须observed:true及mode:spawn/followup/main")
    agent, tool = text(event.get("agent_id"), "实际执行者ID"), text(event.get("tool"), "实际宿主工具")
    call_id = event.get("call_id")
    if call_id is not None:
        call_id = text(call_id, "宿主call_id")
        if any(item.get("call_id") == call_id and item.get("tool") == tool for item in state["events"]):
            raise ValueError("该宿主调用ID已登记，不能重复用旧调用证明新轮实际执行")
    actual_record = event.get("actual_record")
    if call_id is None:
        actual_record = text(actual_record, "宿主未暴露call_id时的可核对实际记录")
    if event["mode"] == "main":
        text(event.get("fallback_reason"), "主会话承担本轮的具体理由")
    old = unit["owner"]
    identity = {"agent_id": agent, "mode": "main" if event["mode"] == "main" else "agent"}
    replacement = old is not None and old != identity
    if replacement:
        reason = text(event.get("replacement_reason"), "物理执行者更换理由")
        handover = text(event.get("handover_record"), "已接受产物/覆盖/约束交接记录")
        unit["handover"].append({"from": old, "to": identity, "reason": reason, "record": handover, "at": utc_now()})
    elif old and old["mode"] == "agent" and event["mode"] != "followup":
        raise ValueError("已有代理必须登记真实同代理followup；更换实例需明确replacement_reason和handover_record")
    elif not old and event["mode"] == "followup":
        raise ValueError("首次事件没有已有代理，不能假称followup")
    capacity_violation = False
    if identity["mode"] == "agent":
        active = [u for key, u in state["units"].items() if key != ident and owner_running(u)]
        capacity_violation = len(active) >= state["max_workers"]
        if any(u["owner"] and u["owner"]["agent_id"] == agent and (not complete(u) or owner_running(u))
               for key, u in state["units"].items() if key != ident):
            raise ValueError("同一物理代理不能同时拥有两个尚未完成独立单元")
    record = {**event, "at": utc_now(), "unit": ident, "stage": stage["id"],
              "agent_id": agent, "tool": tool, "call_id": call_id, "actual_record": actual_record,
              "actual_model": event.get("actual_model"), "actual_effort": event.get("actual_effort")}
    if capacity_violation:
        record["capacity_violation"] = "实际调用超过上限；协调者须中断超额宿主执行并登记interrupt，不能删除真实事件"
    unit["owner"] = identity
    if autonomy_mode(unit) == "unit":
        unit["owner_active"] = True
        unit["owner_interrupted"] = False
    unit["owner_history"].append(identity | {"at": record["at"]})
    stage["dispatches"].append(record)
    if not resume_review:
        stage["status"] = "preparing" if autonomy_mode(unit) == "unit" else "executing"
        if stage["status"] == "executing":
            stage["input_binding"] = deepcopy(unit["inputs"])
    state["events"].append({**record, "event": "actual_dispatch", "event_type": "actual_host_call"})
    state["integration"] = None
    return {"unit": ident, "stage": stage["id"], "status": stage["status"], "record": record,
            "requires_actual_host_interrupt": capacity_violation,
            "limitation": "这是协调者提供的实际记录；脚本未调用宿主，不能独立证明其真实性"}


def start_owner(root: Path, state: dict, ident: str, event: dict) -> dict:
    unit = find_unit(state, ident)
    if autonomy_mode(unit) != "unit" or unit["owner"]:
        raise ValueError("start-owner只登记unit自主单元首次真实spawn；已有owner须显式接管/恢复")
    if event.get("mode") != "spawn":
        raise ValueError("start-owner必须登记真实spawn，不能把内部分轮记录成宿主调用")
    result = dispatch(root, state, ident, event, owner_start=True)
    return result | {"autonomy": "unit", "next": "绑定后preparing；先读当前指南、登记固定输入和范围，再step begin；执行后checkpoint；最终report"}


def adopt_owner(root: Path, state: dict, ident: str, event: dict) -> dict:
    unit = find_unit(state, ident)
    owner, stage = unit["owner"], current_round(unit)
    if autonomy_mode(unit) == "unit":
        raise ValueError("已是unit自主单元，不重复登记接管")
    if not owner or owner["mode"] != "agent" or event.get("agent_id") != owner["agent_id"]:
        raise ValueError("接管必须绑定既有同一真实代理；替代物理实例先用dispatch如实登记交接")
    if event.get("observed") is not True:
        raise ValueError("接管须observed:true表示协调者已核对既有owner身份")
    if stage and stage["status"] == "executing":
        raise ValueError("先真实中断并登记interrupt，再接管现有执行轮，不改变仍在运行的控制约定")
    prerequisite_records(root, state, unit)
    record = {"event": "owner_adoption", "event_type": "control_change", "unit": ident,
              "agent_id": owner["agent_id"], "from_autonomy": autonomy_mode(unit), "to_autonomy": "unit",
              "reason": text(event.get("reason"), "显式接管理由"),
              "adoption_record": text(event.get("adoption_record"), "真实owner身份及授权/恢复范围依据"), "at": utc_now()}
    unit["autonomy"], unit["owner_active"] = "unit", False
    unit.setdefault("autonomy_history", []).append(record)
    state["events"].append(record)
    state["integration"] = None
    return {"unit": ident, "owner": owner, "autonomy": "unit", "record": record,
            "next": "真实恢复同代理后登记dispatch mode:followup；此接管不代表宿主调用，旧事件原样保留"}


def bound_owner(unit: dict, agent_id: str, require_active: bool = True) -> None:
    if autonomy_mode(unit) != "unit" or not unit["owner"] or unit["owner"]["mode"] not in {"agent", "main"}:
        raise ValueError("step仅用于有真实绑定执行者的unit自主单元；主会话降级须先登记实际main dispatch")
    if text(agent_id, "执行owner ID") != unit["owner"]["agent_id"]:
        raise ValueError("内部检查点必须绑定本单元真实owner，不能换人或冒充宿主followup")
    if require_active and not unit.get("owner_active", False):
        raise ValueError("owner已中断/未真实恢复；先登记实际恢复宿主调用，不能以内部step冒充恢复")


def owner_step(root: Path, state: dict, ident: str, agent_id: str, operation: str, payload: dict | None) -> dict:
    unit = find_unit(state, ident)
    bound_owner(unit, agent_id)
    stage = current_round(unit)
    if not stage:
        raise ValueError("单元所有轮次已接受，使用report交最终摘要")
    checkpoint = {"event": "internal_checkpoint", "event_type": "internal_checkpoint", "unit": ident,
                  "stage": stage["id"], "agent_id": agent_id, "operation": operation, "at": utc_now(),
                  "actual_host_call": False}
    if operation == "begin":
        if payload is not None:
            raise ValueError("step begin不接受科学结果payload，提交结果用step submit")
        if stage["status"] not in {"ready", "preparing", "revision_required", "interrupted"}:
            raise ValueError("当前检查点已执行/待审，不重复begin")
        prerequisite_records(root, state, unit)
        selected_tasks(unit, stage)
        if sum(owner_running(other) for other in state["units"].values()) > state["max_workers"]:
            raise ValueError("实际活跃owner超过容量，须真实中断超额实例并登记interrupt")
        stage.setdefault("executions", []).append(checkpoint)
        stage["status"] = "executing"
        stage["constraint_seen_at"] = utc_now()
        unit["constraint_seen_at"] = stage["constraint_seen_at"]
        stage["input_binding"] = deepcopy(unit["inputs"])
        value = {"unit": ident, "stage": stage["id"], "status": "executing"}
    elif operation == "awaiting-input":
        if stage["status"] not in {"preparing", "executing", "revision_required"}:
            raise ValueError("当前阶段不能暂停等待材料")
        payload = payload or {}
        stage["input_gap"] = {"reason": text(payload.get("reason"), "缺材料及科学后果"), "at": utc_now()}
        stage["status"] = "awaiting_input"
        value = {"unit": ident, "stage": stage["id"], "status": "awaiting_input", "physical_owner_restart": False}
    elif operation == "submit":
        if not isinstance(payload, dict):
            raise ValueError("step submit须--payload实际结果JSON")
        if payload.get("agent_id", agent_id) != agent_id:
            raise ValueError("结果agent_id与本单元owner不一致")
        value = submit(root, state, ident, {**payload, "agent_id": agent_id})
    else:
        if operation not in {"accept", "revise"} or not isinstance(payload, dict):
            raise ValueError("step accept/revise须--payload实际局部检查JSON")
        if payload.get("review_kind") not in {"self_check", "independent_review"}:
            raise ValueError("owner阶段验收须如实标记self_check或实际independent_review；最终外部复核用report --finding")
        value = review(root, state, ident, payload, operation == "accept")
    state["events"].append(checkpoint)
    state["integration"] = None
    return value | {"execution_record": checkpoint, "next": "本owner按next当前小包自主继续；只交最终report或异常"}


def checkpoint(root: Path, state: dict, ident: str, agent_id: str, payload: dict) -> dict:
    """Validate and accept one current scientific increment as one idempotent transaction."""
    if not isinstance(payload, dict):
        raise ValueError("checkpoint payload须为JSON对象")
    unit = find_unit(state, ident)
    transaction = safe_id(payload.get("transaction_id"))
    request_sha = digest(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    previous = unit.get("transactions", {}).get(transaction)
    if previous:
        if previous["request_sha256"] != request_sha or previous["agent_id"] != agent_id:
            raise ValueError("事务ID已绑定不同输入或执行者")
        recorded = find_round(unit, previous["receipt"]["stage"])
        accepted = recorded.get("accepted") or {}
        valid = accepted.get("transaction_id") == transaction
        return {**previous["receipt"], "idempotent_replay": True,
                "accepted_current": valid, "current_status": recorded["status"]}
    working = deepcopy(state)
    target = find_unit(working, ident)
    bound_owner(target, agent_id)
    stage = current_round(target)
    if not stage or stage["status"] != "executing" or payload.get("stage") != stage["id"]:
        raise ValueError("checkpoint仅接受当前已begin轮；不能预写三遍再回填")
    validate_finding(target, payload.get("finding", {}))
    decision = payload.get("decision", "accept")
    if decision not in {"accept", "revise"}:
        raise ValueError("checkpoint decision须accept/revise")
    if decision == "revise" and not payload["finding"].get("required_changes"):
        raise ValueError("返工须具体required_changes")
    draft = snapshot(root, target["science_draft"])
    if payload.get("draft_sha256") != draft["sha256"]:
        raise ValueError("checkpoint须draft_sha256绑定本次实际科学草稿")
    previous_science = [old["accepted"] for old in target["rounds"] if old.get("accepted")]
    if previous_science and draft["sha256"] == previous_science[-1].get("science_draft_sha256"):
        raise ValueError("科学草稿没有新增或修正；不接受无变化的后续checkpoint")
    delta = payload.get("delta")
    if not isinstance(delta, list) or not delta:
        raise ValueError("checkpoint须具体科学增量delta，不重复阶段报告")
    raw_science = inside(root, draft["path"]).read_text(encoding="utf-8")
    for change in delta:
        if text(change.get("locator"), "科学增量定位") not in raw_science:
            raise ValueError("delta.locator须是科学草稿中实际标题、块ID或原文片段")
        if len(text(change.get("summary"), "科学增量与判断变化")) > 800:
            raise ValueError("delta只交≤800字符变更摘要；完整科学解释保存science.md")
    science = payload.get("science_state")
    if not isinstance(science, dict):
        raise ValueError("须当前短科学状态science_state")
    for field in ("question", "accepted_boundary", "next_action"):
        text(science.get(field), "science_state." + field)
    for field in ("interfaces", "critical_details", "unresolved"):
        if not isinstance(science.get(field), list):
            raise ValueError("science_state." + field + "须为定位/未知列表")
    if len(json.dumps(science, ensure_ascii=False)) > 6000:
        raise ValueError("短科学状态须≤6000字符；完整解释保留science.md，不截断科学正文")
    short_exit = payload.get("no_change_reason")
    if short_exit and (target["kind"] != "knowledge" or stage["base"] != "K1"):
        raise ValueError("短无增量退出只用于knowledge入口K1")
    if short_exit:
        text(short_exit, "无独立价值/无受影响判断理由")
        if payload.get("candidates") or not payload.get("relation_checks") or payload.get("judgment_changes"):
            raise ValueError("短退出须必要关系/影响检查、无候选且无判断变化")
        checked_inputs = allowed_evidence(root, working, target)
        for check in payload["relation_checks"]:
            check_target = str(inside(root, text(check.get("target"), "关系检查目标")).relative_to(root))
            if check_target not in checked_inputs:
                raise ValueError("短退出关系检查须绑定实际已固定或已接受目标快照")
            text(check.get("reason"), "关系/影响不变依据")
    result = {**payload, "artifact": target["science_draft"], "agent_id": agent_id,
              "answers": [{"question": change["locator"], "answer": change["summary"],
                           "task_ids": change.get("task_ids", [])} for change in delta],
              "critical_details": [], "judgment_changes": payload.get("judgment_changes", []), "gaps": payload.get("gaps", [])}
    submit(root, working, ident, result)
    stage["pending"]["answers"] = []
    stage["pending"]["critical_details"] = []
    stage["pending"]["science_draft_sha256"] = draft["sha256"]
    receipt = review(root, working, ident, payload["finding"], decision == "accept")
    if decision == "accept":
        target["science_state"] = deepcopy(science)
        if short_exit:
            target["no_change"] = {"reason": short_exit, "relation_checks": payload["relation_checks"],
                                   "source_binding": deepcopy(target["inputs"]), "at": utc_now()}
            for following in target["rounds"]:
                if following["status"] not in COMPLETE:
                    following["status"] = "skipped"
                    following["skip_reasons"] = {key: "K1实际机会/影响检查无增量: " + short_exit for key in following["tasks"]}
            receipt["next_stage"] = None
            receipt["no_change"] = True
        record = find_round(target, payload["stage"])["accepted"]
        record["transaction_id"] = transaction
        record["relation_checks"] = payload.get("relation_checks", [])
        record["science_draft_sha256"] = draft["sha256"]
        # The increment and scientific draft carry the science; discard adapter duplicates.
        record["answers"] = []
        record["critical_details"] = []
        state_raw = json.dumps(science, ensure_ascii=False, sort_keys=True)
        state_version = f".research/work/{state['run_id']}/units/{ident}/versions/{stage['id']}/science-state-{digest(state_raw)}.json"
        existing = inside(root, state_version)
        if existing.exists() and existing.read_text(encoding="utf-8") != state_raw:
            raise ValueError("内容寻址的科学状态版本损坏")
        if not existing.exists():
            atomic_text(existing, state_raw)
        record["science_state_ref"] = snapshot(root, state_version)
        record["files"].append(record["science_state_ref"])
        record.pop("science_state", None)
    event = {"event": "scientific_checkpoint", "event_type": "internal_checkpoint", "unit": ident,
             "stage": payload["stage"], "transaction_id": transaction, "agent_id": agent_id,
             "draft_sha256": draft["sha256"], "actual_host_call": False, "at": utc_now()}
    working["events"].append(event)
    receipt.update(transaction_id=transaction, draft_sha256=draft["sha256"], semantic_verification_by_script=False)
    target.setdefault("transactions", {})[transaction] = {"request_sha256": request_sha, "agent_id": agent_id,
                                                         "draft": draft, "receipt": receipt}
    state.clear()
    state.update(working)
    return receipt


def validate_finding(unit: dict, finding: dict, final: bool = False) -> None:
    reviewer = text(finding.get("reviewer"), "实际复核者")
    text(finding.get("summary"), "具体接受/返工原因")
    kind = finding.get("review_kind")
    if autonomy_mode(unit) == "unit" or kind is not None or final:
        if kind not in {"self_check", "independent_review", "final_review"}:
            raise ValueError("须标记实际review_kind:self_check|independent_review|final_review")
        if final != (kind == "final_review"):
            raise ValueError("final_review只审最终候选；阶段验收用self_check或independent_review")
        owner_id = unit["owner"]["agent_id"] if unit["owner"] else None
        if kind == "self_check" and reviewer != owner_id:
            raise ValueError("self_check的reviewer必须是本单元真实owner")
        if kind in {"independent_review", "final_review"}:
            if reviewer == owner_id:
                raise ValueError("owner自查不能标为独立或最终外部复核")
            text(finding.get("actual_review_record"), "实际外部审查记录/工具结果定位")
    checks = finding.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("finding.checks须列具体核验问题、发现和产物/来源定位")
    for check in checks:
        text(check.get("question"), "核验问题")
        text(check.get("finding"), "核验发现")
        text(check.get("locator"), "核验产物/来源定位")


def accepted_binding(unit: dict) -> str:
    return digest(json.dumps({"rounds": [{"id": item["id"], "status": item["status"], "accepted": item["accepted"]}
                                         for item in unit["rounds"]]}, ensure_ascii=False, sort_keys=True))


def final_report(root: Path, state: dict, ident: str, report: dict | None = None, finding: dict | None = None) -> dict:
    unit = find_unit(state, ident)
    if not complete(unit):
        raise ValueError("report仅交全部适用轮次已接受的最终候选；阶段问题按异常报告，不抄阶段正文")
    prerequisite_records(root, state, unit)
    binding = accepted_binding(unit)
    accepted = [item["accepted"] for item in unit["rounds"] if item["accepted"]]
    mapping = [item for record in accepted for item in record.get("candidates", [])]
    verify(root, [{"path": item["draft"], "sha256": item["sha256"]} for item in mapping])
    result = {"unit": ident, "status": "final_candidate", "owner": unit["owner"],
              "autonomy": autonomy_mode(unit), "candidates": mapping, "sources": unit["sources"],
              "accepted_binding_sha256": binding,
              "checkpoint_counts": {"accepted": len(accepted),
                                    "self_check": sum(item.get("finding", {}).get("review_kind") == "self_check" for item in accepted),
                                    "independent_review": sum(item.get("finding", {}).get("review_kind") == "independent_review" for item in accepted)},
              "checks": {"file_integrity": "fixed_files_match",
                         "integrity_scope": "unit fixed inputs, all accepted artifact/candidate versions and evidence files",
                         "semantic_verification_by_script": False,
                         "final_review": "not_recorded"}, "publication_authorized_by_script": False}
    if report is not None:
        if any(helper.get("unit") == ident and helper.get("status") in {"reserved", "bound"}
               for helper in state.get("runtime", {}).get("helpers", {}).values()):
            raise ValueError("本单元助手仍有预留/运行，先真实结束并核证结果再交最终report")
        if not isinstance(report, dict):
            raise ValueError("report须为实际最终摘要JSON")
        if autonomy_mode(unit) == "unit":
            bound_owner(unit, report.get("agent_id"), require_active=False)
        conclusions = report.get("conclusions")
        if not isinstance(conclusions, list) or not conclusions:
            raise ValueError("最终report须非空conclusions实际结论摘要，不能复制阶段answers")
        for conclusion in conclusions:
            if len(text(conclusion, "最终实质结论")) > 1000:
                raise ValueError("每项最终结论须≤1000字，完整依据保留在候选及科学记录")
        if len(conclusions) > 12:
            raise ValueError("最终交接限12项实质结论，详细内容留候选文件")
        for field in ("unresolved", "checks"):
            if not isinstance(report.get(field), list):
                raise ValueError(f"report.{field}须为列表，无未决可明确空列表")
        summaries = {field: report.get(field, []) for field in
                     ("conclusions", "unresolved", "checks", "knowledge_leads", "coverage_scope")}
        for field, items in summaries.items():
            if not isinstance(items, list) or len(items) > 20 or any(
                    not isinstance(item, str) or not item.strip() or len(item) > 1000 for item in items):
                raise ValueError(f"report.{field}限20项、每项≤1000字的具体摘要")
        if len(json.dumps(summaries, ensure_ascii=False)) > 6000:
            raise ValueError("最终report全部摘要字段合计限6000字符（含JSON格式）；完整科学内容保留在候选与记录")
        saved = {"unit": ident, "agent_id": report.get("agent_id"), "conclusions": conclusions,
                 "unresolved": report["unresolved"], "owner_checks": report["checks"],
                 "knowledge_leads": summaries["knowledge_leads"], "coverage_scope": summaries["coverage_scope"],
                 "accepted_binding_sha256": binding, "at": utc_now()}
        path = f".research/records/runs/{state['run_id']}/reports/{ident}-{digest(json.dumps(saved, ensure_ascii=False, sort_keys=True))}.json"
        write_json(inside(root, path), saved)
        unit["final_report"] = snapshot(root, path)
        unit["owner_active"] = False  # Scientific delivery ended; this does not close a host thread.
        state["events"].append({"event": "final_report", "event_type": "unit_delivery", "unit": ident,
                                "report": unit["final_report"], "at": saved["at"]})
    if unit.get("final_report"):
        verify(root, [unit["final_report"]])
        saved = read_json(inside(root, unit["final_report"]["path"]))
        if saved.get("accepted_binding_sha256") != binding:
            raise ValueError("最终摘要绑定的接受记录已改变，须由owner重新report，不复用旧摘要")
        result.update({key: saved[key] for key in ("conclusions", "unresolved", "owner_checks")})
        result.update({key: saved.get(key, []) for key in ("knowledge_leads", "coverage_scope")})
        result["report_ref"] = unit["final_report"]
    else:
        result["report_status"] = "owner_summary_not_recorded"
    if finding is not None:
        validate_finding(unit, finding, final=True)
        if finding.get("accepted_binding_sha256") is not None and finding["accepted_binding_sha256"] != binding:
            raise ValueError("最终复核声明的接受绑定已过期；核当前候选/接受轮次后更新真实审查记录")
        if "candidate_path" in finding or "candidate_sha256" in finding:
            if not any(item["draft"] == finding.get("candidate_path") and
                       item["sha256"] == finding.get("candidate_sha256") for item in mapping):
                raise ValueError("最终复核声明的候选路径/哈希不匹配当前接受产物")
        if finding.get("decision") not in {"accept", "revise"}:
            raise ValueError("最终finding须有decision:accept|revise；revise之后定向reopen")
        if finding["decision"] == "revise" and (not isinstance(finding.get("required_changes"), list) or not finding["required_changes"]):
            raise ValueError("最终返工须具体required_changes")
        unit["final_review"] = {"finding": finding, "accepted_binding_sha256": binding, "at": utc_now()}
        state["events"].append({"event": "final_review", "event_type": "review", "unit": ident,
                                "reviewer": finding["reviewer"], "decision": finding["decision"],
                                "accepted_binding_sha256": binding, "at": unit["final_review"]["at"]})
    final = unit.get("final_review")
    if final and final.get("accepted_binding_sha256") == binding:
        result["checks"]["final_review"] = {"decision": final["finding"]["decision"],
                                           "reviewer": final["finding"]["reviewer"], "at": final["at"],
                                           "json_pointer": f"/units/{ident}/final_review"}
    return result


def markdown_sections(raw: str, sections: list[str]) -> str:
    """Return exact heading subtrees; avoid matching headings inside fenced examples."""
    lines, headings, fenced = raw.splitlines(keepends=True), [], None
    for index, line in enumerate(lines):
        fence = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if fence:
            marker = fence.group(1)
            if fenced is None:
                fenced = marker[0]
            elif fenced == marker[0]:
                fenced = None
            continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line.rstrip("\r\n")) if fenced is None else None
        if heading:
            headings.append((index, len(heading.group(1)), heading.group(2)))
    ranges = []
    for section in sections:
        matches = [(index, level) for index, level, title in headings if title == section]
        if len(matches) != 1:
            raise ValueError(f"当前资源标题须唯一且确实存在: {section}")
        index, level = matches[0]
        end = next((position for position, depth, _ in headings if position > index and depth <= level), len(lines))
        ranges.append((index, end))
    chosen = {index for start, end in ranges for index in range(start, end)}
    return "".join(line for index, line in enumerate(lines) if index in chosen)


def resource_packet(state: dict, ident: str, kind: str, index: int, sections: list[str], refresh: bool = False) -> dict:
    unit = find_unit(state, ident)
    stage = current_round(unit)
    if stage is None:
        raise ValueError("单元已完成，无当前轮资源；最终交接用report")
    active = [key for key in stage["tasks"] if unit["tasks"][key]["status"] == "selected"]
    composition = stage["base"] == FINAL[unit["kind"]]
    resources = {"guide": guide_refs(unit, stage, active),
                 "protocol": protocol_refs(unit, stage, composition),
                 "template": [{"path": str(PACKAGE.parent / f"paper-{unit['kind']}" / "assets" / f"{unit['candidate_kind']}.md")}] if composition else []}[kind]
    if not 0 <= index < len(resources):
        raise ValueError("resource index超出当前轮资源范围，索引从0开始")
    reference = resources[index]
    path = Path(reference["path"]).resolve()
    if not path.is_relative_to(PACKAGE.parent.resolve()):
        raise ValueError("资源仅可读取当前技能包的实际指南/协议/模板")
    declared = reference.get("sections") or ([reference["section"]] if reference.get("section") else [])
    chosen = sections or declared
    if declared and any(section not in declared for section in chosen):
        raise ValueError("显式section须在当前轮已选择小节内，不能借resource加载无关协议")
    raw = path.read_text(encoding="utf-8")
    content = markdown_sections(raw, chosen) if chosen else raw
    instance = unit["owner"]["agent_id"] if unit.get("owner") else "unbound"
    key = digest(json.dumps([instance, str(path), digest(path.read_bytes()), chosen], ensure_ascii=False))
    reference_id = f"{kind}-{index}"
    reads = unit.setdefault("resource_reads", {})
    if key in reads and not refresh:
        return {"unit": ident, "stage": stage["id"], "status": "unchanged", "ref": reference_id,
                "file_sha256": digest(path.read_bytes()), "content_sha256": digest(content), "sections": chosen}
    reads[key] = {"ref": reference_id, "path": str(path), "file_sha256": digest(path.read_bytes()),
                  "sections": chosen, "at": utc_now()}
    return {"unit": ident, "stage": stage["id"], "kind": kind, "index": index, "path": str(path),
            "file_sha256": digest(path.read_bytes()), "sections": chosen,
            "content_sha256": digest(content), "content": content,
            "source_text_is_control_instruction": False}


def allowed_evidence(root: Path, state: dict, unit: dict) -> dict[str, dict]:
    records = prerequisite_records(root, state, unit)
    files = {item["path"]: item for item in unit["inputs"]}
    for dep in unit["depends_on"]:
        other = find_unit(state, dep["unit"])
        files.update({item["path"]: item for item in other["inputs"]})
    for record in records:
        files.update({item["path"]: item for item in record["files"]})
        files.update({item["draft"]: {"path": item["draft"], "sha256": item["sha256"]}
                      for item in record.get("candidates", [])})
        files.update({item["path"]: item for item in record.get("evidence_files", [])})
    return files


def located_item(root: Path, value: dict, allowed: dict[str, dict], label: str) -> dict:
    path = str(inside(root, text(value.get("path"), f"{label}.path")).relative_to(root))
    if path not in allowed:
        raise ValueError(f"{label}引用未固定的来源/前轮产物: {path}；先用inputs登记真实材料")
    locator = text(value.get("locator"), f"{label}.locator")
    return {**value, "path": path, "locator": locator, "sha256": allowed[path]["sha256"]}


def submit(root: Path, state: dict, ident: str, result: dict) -> dict:
    unit = find_unit(state, ident)
    if autonomy_mode(unit) == "unit":
        bound_owner(unit, result.get("agent_id"))
    stage = current_round(unit)
    execution = sorted(stage.get("executions", []) + stage.get("dispatches", []), key=lambda item: item["at"])[-1:] if stage else []
    if not stage or stage["status"] != "executing" or not execution:
        raise ValueError("提交须绑定真实宿主执行或绑定owner的内部检查点，不能用路由偏好冒充调用")
    if result.get("stage") != stage["id"]:
        raise ValueError("只能交当前轮，不能一份结果跳过三遍或未来轮")
    active_agents = [other for other in state["units"].values() if owner_running(other)]
    if len(active_agents) > state["max_workers"]:
        raise ValueError("实际代理调用超额，先真实中断超额执行并登记interrupt，不能以提交结果消除违规记录")
    if stage.get("input_binding") is not None and stage["input_binding"] != unit["inputs"]:
        raise ValueError("执行输入契约已变化；先重新prepare/begin，不能静默替换")
    allowed = allowed_evidence(root, state, unit)
    active = selected_tasks(unit, stage)
    artifact_path = result.get("artifact", str(Path(unit["output_dir"]) / f"{stage['id']}.md"))
    artifact = snapshot(root, artifact_path)
    science_path = unit.get("science_draft")
    if artifact["path"] != science_path and inside(root, artifact["path"]).parent != inside(root, unit["output_dir"]):
        raise ValueError("阶段结果须实际位于本单元output_dir，不能用别处笔记软链接充当")
    answers = result.get("answers")
    if not isinstance(answers, list) or not answers:
        raise ValueError("本轮结果须有具体answers，不以状态标签代替科学答案")
    covered_tasks = set()
    for answer in answers:
        text(answer.get("question"), "answer.question")
        text(answer.get("answer"), "answer.answer")
        ids = answer.get("task_ids", [])
        if not ids or any(key not in active for key in ids):
            raise ValueError("每项答案须对应当前轮已选择的task_ids，不能混入未来科学任务")
        covered_tasks.update(ids)
    if covered_tasks != set(active):
        raise ValueError(f"当前叶任务缺答案: {','.join(sorted(set(active) - covered_tasks))}")
    evidence = [located_item(root, item, allowed, "evidence") for item in result.get("evidence", [])]
    coverage = []
    all_sources = unit["sources"] + [source for dep in unit["depends_on"]
                                     for source in find_unit(state, dep["unit"])["sources"]]
    for item in result.get("coverage", []):
        value = located_item(root, item, allowed, "coverage")
        if value.get("mode") not in {"read", "image_checked", "not_covered"}:
            raise ValueError("coverage.mode只可为read/image_checked/not_covered")
        if value["mode"] == "not_covered":
            text(value.get("reason"), "未覆盖的具体限制及后果")
        for source in all_sources:
            if value["path"] in {source["path"], source["source_path"]}:
                is_pdf = source.get("source_kind", "pdf") == "pdf"
                if not is_pdf and value.get("page") is not None:
                    raise ValueError("非PDF coverage不能附造页码，使用实际section/block_id或原文locator")
                if is_pdf and value.get("page") is not None and (not isinstance(value["page"], int) or isinstance(value["page"], bool) or
                        not 1 <= value["page"] <= source["page_count"]):
                    raise ValueError("coverage.page超出固定PDF来源页数")
                if not is_pdf and value.get("section") is not None and (not isinstance(value["section"], int) or
                        isinstance(value["section"], bool) or not 1 <= value["section"] <= source["section_count"]):
                    raise ValueError("coverage.section超出非PDF实际解析段范围；原文节名写locator")
                if value.get("block_id"):
                    data = read_json(inside(root, source["path"]))
                    blocks = {block["id"]: block for block in data["blocks"]}
                    if value["block_id"] not in blocks:
                        raise ValueError("coverage.block_id不在固定manifest中")
                    if is_pdf and value.get("page") is not None and blocks[value["block_id"]].get("page") != value["page"]:
                        raise ValueError("coverage.block_id实际页与所记page不一致")
                    if not is_pdf and value.get("section") is not None and blocks[value["block_id"]].get("section") != value["section"]:
                        raise ValueError("coverage.block_id实际解析段与所记section不一致")
        coverage.append(value)
    if not coverage:
        raise ValueError("必须记录实际访问或明确未覆盖的coverage；不可从解析文本存在推定已读")
    if not evidence:
        raise ValueError("须给出本轮实际依据；无新增科学内容时指向接受的旧结果/当前知识快照")
    for key in ("judgment_changes", "critical_details", "gaps"):
        if not isinstance(result.get(key), list):
            raise ValueError(f"result.{key}须为列表，无变化/无缺口可明确为空")
    files, version_requests = [], [(artifact, "science" if artifact["path"] == science_path else "artifact")]
    candidates = []
    if stage["base"] == FINAL[unit["kind"]]:
        raw = result.get("candidates")
        if raw is None and unit["target"]:
            raw = [{"draft": unit["draft"], "target": unit["target"]}]
        if not raw and not (unit["kind"] == "knowledge" and result.get("no_change_reason")) and not (
                unit["kind"] == "trace" and stage["status"] == "skipped"):
            raise ValueError("成文轮须有draft/target候选映射；知识无改动须具体no_change_reason")
        if not raw:
            text(result.get("no_change_reason"), "没有知识更新候选的具体理由")
        for value in raw or []:
            draft = snapshot(root, value["draft"])
            path = inside(root, draft["path"])
            if path.suffix != ".md" or not path.is_relative_to(inside(root, unit["draft_dir"])):
                raise ValueError("成文产物须在本单位独占draft_dir内，不能由代理写正式页")
            target = validate_target(root, value["target"])
            writer = state.setdefault("candidate_writers", {}).get(target)
            if writer and writer != ident:
                raise ValueError("此目标已绑定其他唯一候选写者: " + target)
            for other in state["units"].values():
                if other["id"] != ident and any(target == candidate["target"] for old in other["rounds"]
                        for candidate in (old.get("accepted") or old.get("pending") or {}).get("candidates", [])):
                    raise ValueError("同一目标已有其他单元候选；须由唯一写者整合")
            from .notes import validate_note_frontmatter, block_anchors, table_render_issues
            from .semantics import parse_note, body_presentation_issues
            content = path.read_text(encoding="utf-8")
            meta = validate_note_frontmatter(content, target)
            expected_kinds = ({"paper"} if target.startswith("论文/") else
                              {"knowledge"} if target.startswith("知识/") else {"comparison", "topic"})
            if meta.get("kind") not in expected_kinds:
                raise ValueError("候选kind与正式目标/本单元类型不一致: " + target)
            if meta.get("review_status") or meta.get("analysis") in {"draft", "pending", "待审", "草稿"}:
                raise ValueError("正式候选正文不能携带待审投影状态: " + target)
            front = re.match(r"\A---\r?\n.*?\r?\n---(?:\r?\n|$)", content, re.S)
            body = content[front.end():]
            anchors, layout_errors = block_anchors(body)
            _, contract_errors = parse_note(target, body, line_offset=content[:front.end()].count("\n"))
            if len(anchors) != len(set(anchors)):
                layout_errors.append("块ID重复")
            layout_errors += table_render_issues(body)
            layout_errors += body_presentation_issues(body)
            if layout_errors or contract_errors:
                raise ValueError("候选本地字段/排版契约未通过（不等于科学核验）: " + json.dumps(
                    {"target": target, "layout": layout_errors, "fields": contract_errors}, ensure_ascii=False))
            formal = inside(root, target)
            candidates.append({"draft": draft["path"], "target": target, "sha256": draft["sha256"],
                               "target_sha256": digest(formal.read_bytes()) if formal.is_file() else None})
            version_requests.append((draft, f"candidate-{len(candidates)}"))
        if len({item["target"] for item in candidates}) != len(candidates):
            raise ValueError("同一单元不能对同一正式页提交多份候选")
    files = [preserved_version(root, state["run_id"], ident, stage["id"], file, label)
             for file, label in version_requests] + files
    for index, candidate in enumerate(candidates, 1):
        candidate["version_ref"] = files[index]
        state.setdefault("candidate_writers", {})[candidate["target"]] = ident
    original_evidence = {item["path"]: {"path": item["path"], "sha256": item["sha256"]}
                         for item in evidence + coverage}
    immutable_evidence = {path: preserved_version(root, state["run_id"], ident, "sources", file, "evidence")
                          for path, file in original_evidence.items()}
    evidence_files = list(immutable_evidence.values())
    input_versions = [preserved_version(root, state["run_id"], ident, "sources", item, "input")
                      for item in unit["inputs"]]
    files = list({item["path"]: item for item in files + input_versions}.values())
    for item in evidence + coverage:
        item["version_ref"] = immutable_evidence[item["path"]]
    pending = {"unit": ident, "stage": stage["id"], "files": files, "answers": answers,
               "evidence": evidence, "evidence_files": evidence_files, "coverage": coverage,
               "judgment_changes": result["judgment_changes"], "critical_details": result["critical_details"],
               "gaps": result["gaps"], "delta": result.get("delta", []), "science_state": result.get("science_state"),
               "input_binding": deepcopy(unit["inputs"]), "input_versions": input_versions, "sources": deepcopy(unit["sources"]), "candidates": candidates, "no_change_reason": result.get("no_change_reason"),
               "dispatch": deepcopy(stage["dispatches"][-1]) if stage["dispatches"] else None,
               "execution": deepcopy(execution[0]), "at": utc_now()}
    stage["pending"], stage["status"] = pending, "awaiting_review"
    state["integration"] = None
    return {"unit": ident, "stage": stage["id"],
            "status": "awaiting_owner_local_review" if autonomy_mode(unit) == "unit" else "awaiting_coordinator_review", "files": files,
            "semantic_verification": "not_performed_by_script"}


def review(root: Path, state: dict, ident: str, finding: dict, accept: bool) -> dict:
    unit = find_unit(state, ident)
    stage = current_round(unit)
    if not stage or stage["status"] != "awaiting_review" or not stage["pending"]:
        raise ValueError("没有待复核的当前轮结果")
    validate_finding(unit, finding)
    if not accept and (not isinstance(finding.get("required_changes"), list) or not finding["required_changes"]):
        raise ValueError("返工须有required_changes具体缺项，不只写不通过")
    prerequisite_records(root, state, unit)
    verify(root, stage["pending"]["files"])
    verify(root, stage["pending"]["evidence_files"])
    record = deepcopy(stage["pending"]) | {"finding": deepcopy(finding), "reviewed_at": utc_now()}
    stage["history"].append({"decision": "accept" if accept else "revise", "finding": finding, "at": utc_now()})
    if accept:
        stage["accepted"], stage["status"] = record, "accepted"
    else:
        if not isinstance(finding.get("required_changes"), list) or not finding["required_changes"]:
            raise ValueError("返工须有required_changes具体缺项，不只写不通过")
        stage["status"] = "revision_required"
        stage["history"][-1]["previous_result"] = record
    stage["pending"] = None
    state["integration"] = None
    following = current_round(unit)
    return {"unit": ident, "stage": stage["id"], "decision": "accepted" if accept else "revision_required",
            "next_stage": following["id"] if following else None,
            "review_kind": finding.get("review_kind", "legacy_coordinator_review"),
            "scientific_judgment": "reviewer_recorded_not_independently_verified_by_script"}


def clear_from(unit: dict, stage: str, reason: str) -> set[str]:
    start = unit["rounds"].index(find_round(unit, stage))
    changed = set()
    for item in unit["rounds"][start:]:
        if item["status"] != "skipped":
            changed.add(item["id"])
            item["history"].append({"decision": "reopen", "reason": reason, "at": utc_now(),
                                    "previous_result": item["accepted"] or item["pending"]})
            item["accepted"], item["pending"], item["status"] = None, None, "revision_required"
    unit.pop("final_report", None)
    unit.pop("final_review", None)
    if unit.get("science_state"):
        remaining = [item for item in unit["rounds"] if item.get("accepted")]
        unit["science_state"] = {**unit["science_state"],
                                 "accepted_boundary": remaining[-1]["id"] if remaining else "none",
                                 "unresolved": unit["science_state"].get("unresolved", []) + ["重开受影响判断: " + reason],
                                 "next_action": "重新准备 " + stage}
    return changed


def reopen(state: dict, ident: str, stage: str, reason: str) -> dict:
    reason = text(reason, "重开具体原因")
    unit = find_unit(state, ident)
    requested = find_round(unit, stage)
    current = current_round(unit)
    if current and unit["rounds"].index(requested) > unit["rounds"].index(current):
        raise ValueError("不能重开尚未到达的未来轮")
    if requested["status"] == "skipped":
        raise ValueError("该轮不适用；先用tasks改变实际范围决定")
    affected = {ident: clear_from(unit, stage, reason)}
    queue = [ident]
    while queue:
        changed_unit = queue.pop(0)
        for key, downstream in state["units"].items():
            if key in affected:
                continue
            impacted = [dep for dep in downstream["depends_on"] if dep["unit"] == changed_unit and
                        (dep["stage"] is None or dep["stage"] in affected[changed_unit])]
            if impacted:
                first = next((item["id"] for item in downstream["rounds"] if item["status"] != "skipped"), None)
                if first:
                    affected[key] = clear_from(downstream, first, f"依赖{changed_unit}实质重开: {reason}")
                    queue.append(key)
    state["integration"] = None
    state["events"].append({"event": "targeted_reopen", "unit": ident, "stage": stage,
                            "reason": reason, "affected": {key: sorted(value) for key, value in affected.items()}, "at": utc_now()})
    return {"status": "reopened", "affected": {key: sorted(value) for key, value in affected.items()}}


def decide_tasks(state: dict, ident: str, decisions: dict) -> dict:
    unit = find_unit(state, ident)
    if not isinstance(decisions, dict) or not decisions:
        raise ValueError("decisions须有本单元叶任务范围决定")
    reopen_at = []
    for key, value in decisions.items():
        if key not in unit["tasks"]:
            raise ValueError(f"叶任务不属于当前单元: {key}")
        new = task_decision(value)
        for stage in unit["rounds"]:
            if key in stage["tasks"] and stage["status"] in LIVE:
                raise ValueError("实际执行/待复核轮不能改任务范围；先中断或返工")
            if key in stage["tasks"] and stage["status"] in COMPLETE and new != unit["tasks"][key]:
                reopen_at.append(stage["id"])
        unit["tasks"][key] = new
    if reopen_at:
        first = min(reopen_at, key=lambda key: unit["rounds"].index(find_round(unit, key)))
        for item in unit["rounds"]:
            if item["id"] in reopen_at and item["status"] == "skipped":
                item["status"] = "ready"
        reopen(state, ident, first, "实际适用范围改变：" + "; ".join(value["reason"] for value in decisions.values()))
    skip_inapplicable(unit)
    state["integration"] = None
    return {"unit": ident, "task_decisions": decisions, "stage": current_round(unit)["id"] if current_round(unit) else None}


def repeat_round(state: dict, ident: str, stage_id: str, reason: str) -> dict:
    unit = find_unit(state, ident)
    stage = find_round(unit, stage_id)
    reason = text(reason, "重复当前科学轮的具体目标/范围")
    if stage["base"] == FINAL[unit["kind"]]:
        raise ValueError("成文版本修订用reopen/revise，不增加第二个最终成文轮")
    if stage["status"] != "accepted":
        raise ValueError("只有已接受轮才能增加定向补批次；返工使用revise/reopen")
    index = unit["rounds"].index(stage)
    if any(item["status"] in LIVE or item["status"] == "accepted" for item in unit["rounds"][index + 1:]):
        raise ValueError("下游已执行时先定向重开；不能在已接受结果中插入未审依赖")
    count = sum(item["base"] == stage["base"] for item in unit["rounds"]) + 1
    ident_stage = f"{stage['base']}-{count}"
    new = {"id": ident_stage, "base": stage["base"], "tasks": stage["tasks"], "guide": stage["guide"],
           "goal": reason, "status": "ready", "dispatches": [], "pending": None, "accepted": None, "history": []}
    unit["rounds"].insert(index + 1, new)
    state["integration"] = None
    return {"unit": ident, "added_round": ident_stage, "goal": reason}


def refresh_inputs(root: Path, state: dict, ident: str, values: list[str], reason: str,
                   stage: str | None = None, mode: str = "append") -> dict:
    unit = find_unit(state, ident)
    reason = text(reason, "固定材料变化理由")
    if mode not in {"append", "replace", "changed-premise"}:
        raise ValueError("输入操作须append/replace/changed-premise")
    if mode != "append" and not stage:
        raise ValueError("replace/changed-premise须明确最早受影响stage")
    current = current_round(unit)
    if current and current["status"] == "awaiting_review":
        raise ValueError("待审结果先accept/revise，再登记输入变化")
    if stage:
        requested = find_round(unit, stage)
        if current and unit["rounds"].index(requested) > unit["rounds"].index(current):
            raise ValueError("不能用未来阶段回填输入")
    if mode == "append":
        verify(root, unit["inputs"])
        paths = list(dict.fromkeys(unit.get("declared_input_paths", []) + values))
    else:
        paths = values
    files, sources = freeze_inputs(root, paths, unit["kind"])
    old = {item["path"]: item["sha256"] for item in unit["inputs"]}
    new = {item["path"]: item["sha256"] for item in files}
    if mode == "append" and any(new.get(path) != sha for path, sha in old.items()):
        raise ValueError("append不能静默替换已固定材料")
    if mode == "append" and not stage and old == new:
        return {"unit": ident, "status": "unchanged", "physical_owner_restart": False}
    result = reopen(state, ident, stage, reason) if stage else {"affected": {}}
    unit["inputs"], unit["sources"], unit["declared_input_paths"] = files, sources, paths
    current = current_round(unit)
    # Current input contract must be prepared again, without a fictitious host restart.
    if current and (stage or current["status"] in {"executing", "awaiting_input", "preparing"}):
        current["status"] = "preparing"
        current.pop("input_gap", None)
    event = {"event": "input_update", "unit": ident, "mode": mode, "stage": stage,
             "reason": reason, "previous": old, "current": new, "at": utc_now()}
    unit.setdefault("input_history", []).append(event)
    state["events"].append(event)
    state["integration"] = None
    return {**result, "unit": ident, "status": current["status"] if current else "all_rounds_accepted",
            "fixed_inputs": files, "physical_owner_restart": False}


def update_constraints(state: dict, update: dict, affected: list[str]) -> dict:
    if update.get("kind") not in {"constraint", "correction", "goal", "authorization"}:
        raise ValueError("更新kind为constraint/correction/goal/authorization")
    message = text(update.get("text"), "用户实际输入")
    record = {**update, "text": message, "at": utc_now(), "affected": affected}
    state["constraint_history"].append(record)
    if update["kind"] == "goal":
        state["goal"] = message
        for unit in state["units"].values():
            unit["goal"] = message
    elif update["kind"] == "authorization":
        state["authorization"] = {"text": message, "evidence": update.get("evidence"), "at": record["at"]}
    else:
        state["constraints"].append(record)
    results = []
    for pointer in affected:
        ident, separator, stage = pointer.partition(":")
        if not separator:
            raise ValueError("--affected必须为unit:stage，避免全量无差别失效")
        results.append(reopen(state, ident, stage, f"用户{update['kind']}: {message}"))
    state["integration"] = None
    return {"update": record, "reopened": results, "limitation": "哪些科学依赖受影响由协调者判断，脚本不猜语义"}


def locate_windows(root: Path, state: dict, ident: str, stage_id: str, windows: list[dict]) -> dict:
    unit = find_unit(state, ident)
    stage = find_round(unit, stage_id)
    if stage["status"] in COMPLETE or stage["status"] in LIVE:
        raise ValueError("不能更改已接受/实际执行轮的窗口；定向重开后再定位")
    if not isinstance(windows, list) or not windows:
        raise ValueError("windows须为非空当前科学问题/来源定位列表")
    allowed = allowed_evidence(root, state, unit)
    located = [located_item(root, value, allowed, "source_window") for value in windows]
    unit["source_windows"][stage_id] = located
    return {"unit": ident, "stage": stage_id, "source_windows": located}


def interrupt(state: dict, ident: str, reason: str) -> dict:
    unit = find_unit(state, ident)
    stage = current_round(unit)
    if not stage:
        raise ValueError("单元已完成，无待恢复轮")
    autonomous = autonomy_mode(unit) == "unit"
    awaiting_review = stage["status"] == "awaiting_review"
    if awaiting_review and not autonomous:
        raise ValueError("已交结果应复核或revise，无需将可核对结果标为执行中断")
    record = {"event": "interrupted", "stage": stage["id"], "reason": text(reason, "实际中断原因"),
              "pending_preserved": awaiting_review, "at": utc_now()}
    stage["history"].append(record)
    if not awaiting_review:
        stage["status"] = "interrupted"
    if autonomous:
        unit["owner_active"] = False
        unit["owner_interrupted"] = True
    state["events"].append(record | {"unit": ident})
    return record


def status(root: Path, state: dict, detail: str = "compact") -> dict:
    summaries = {}
    for ident, unit in state["units"].items():
        stage = current_round(unit)
        errors = []
        try:
            prerequisite_records(root, state, unit)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            errors.append(str(exc))
        summaries[ident] = {"kind": unit["kind"], "stage": stage["id"] if stage else None,
                            "status": stage["status"] if stage else "all_rounds_accepted", "owner": unit["owner"],
                            "autonomy": autonomy_mode(unit), "owner_active": owner_running(unit),
                            "report_ref": unit.get("final_report"),
                            "blocked_by": errors, "pending_task_decisions": [key for key, value in unit["tasks"].items() if value["status"] == "pending"],
                            "accepted_stages": [item["id"] for item in unit["rounds"] if item["status"] == "accepted"],
                            "skipped": {item["id"]: item.get("skip_reasons") for item in unit["rounds"] if item["status"] == "skipped"}}
    return {"run_id": state["run_id"], "goal": state["goal"], "max_workers": state["max_workers"], "units": summaries,
            "state_ref": str((archived_state_path(root, state["run_id"]) if archived_state_path(root, state["run_id"]).exists()
                              else state_path(root, state["run_id"])).relative_to(root)),
            "integration": state["integration"] if detail == "full" else (
                {"status": state["integration"].get("status"), "at": state["integration"].get("at")} if state["integration"] else None),
            "semantic_verification": state["semantic_verification"]}


def recovery(root: Path, state: dict, ident: str | None, detail: str = "compact") -> dict:
    if detail != "full":
        return compact_recovery(root, state, ident)
    units = {ident: find_unit(state, ident)} if ident else state["units"]
    result = {"run_id": state["run_id"], "goal": state["goal"], "user_constraints": state["constraints"],
              "constraint_history": state["constraint_history"] if detail == "full" else state["constraint_history"][-3:],
              "constraint_history_count": len(state["constraint_history"]),
              "state_ref": str((archived_state_path(root, state["run_id"]) if archived_state_path(root, state["run_id"]).exists()
                                else state_path(root, state["run_id"])).relative_to(root)),
              "authorization": state["authorization"], "units": {}}
    for key, unit in units.items():
        accepted = [short_record(item["accepted"], detail, record_reference(root, state, unit, item, "accepted"))
                    for item in unit["rounds"] if item["accepted"]]
        try:
            prerequisite_records(root, state, unit)
            integrity = "fixed_files_match"
        except (ValueError, OSError, KeyError, TypeError) as exc:
            integrity = str(exc)
        current = current_round(unit)
        result["units"][key] = {"owner": unit["owner"], "autonomy": autonomy_mode(unit),
                              "owner_active": owner_running(unit), "sources": unit["sources"], "accepted": accepted,
                              "report_ref": unit.get("final_report"),
                              "integrity": integrity, "current_stage": current["id"] if current else None,
                              "current_status": current["status"] if current else "all_rounds_accepted",
                              "pending": short_record(current["pending"], detail, record_reference(root, state, unit, current, "pending")) if current and current["pending"] else None,
                              "latest_revision": [short_revision(value, detail) for value in current["history"][-1:]] if current else [],
                              "handover": unit["handover"] if detail == "full" else {"count": len(unit["handover"]), "json_pointer": f"/units/{key}/handover"},
                              "next": ("绑定owner自主检查/继续当前轮；实际中断需要宿主真实恢复调用后登记dispatch，不重复已接受轮"
                                       if autonomy_mode(unit) == "unit" else "先复核待审结果；否则真实续派原代理；可显式adopt-owner接管，不改历史事件")}
    return result


def add_unit(root: Path, state: dict, payload: dict) -> dict:
    """Append a genuinely new scientific unit; existing acceptance is untouched."""
    reason = text(payload.get("reason"), "新增科学单元必要性/局部补证不足理由")
    budget = payload.get("budget", {})
    limit = budget.get("token_limit")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("追加单元须正整数真实授权token_limit")
    text(budget.get("basis"), "新增单元预算依据")
    spec = payload.get("unit")
    if not isinstance(spec, dict):
        raise ValueError("add-unit须一个实际unit计划")
    ident = safe_id(spec.get("id"))
    if ident in state["units"]:
        raise ValueError("单元ID已存在，不覆盖旧单元")
    proposed = initialize(root, state["run_id"], {"goal": state["goal"], "units": [spec],
                           "max_workers": state["max_workers"], "worker_limit_reason": "沿用当前已授权容量",
                           "autonomy": state.get("autonomy", "unit"), "constraints": state["constraints"]}, existing=state)
    # initialize validates the combined graph, fixed inputs, scope and exclusive target paths.
    state["units"][ident] = proposed["units"][ident]
    state["units"][ident]["budget"] = budget
    state["candidate_writers"] = proposed["candidate_writers"]
    for key in state["units"][ident]["tasks"]:
        state["task_scope"][key] = {"status": "selected", "reason": "计划追加单元: " + reason}
    state["events"].append({"event": "plan_add_unit", "unit": ident, "reason": reason, "budget": budget, "at": utc_now()})
    state["integration"] = None
    return {"status": "unit_added", "unit": ident, "next": "绑定新owner；旧科学边界不回填"}


def integrate(root: Path, state: dict, ident: str, agent_id: str, payload: dict) -> dict:
    """The bound writer receives source-backed exact fragments into its one candidate."""
    unit = find_unit(state, ident)
    bound_owner(unit, agent_id, require_active=False)
    transaction = safe_id(payload.get("transaction_id"))
    request_sha = digest(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    old = unit.get("integrations", {}).get(transaction)
    if old:
        if old["request_sha256"] != request_sha or old["agent_id"] != agent_id:
            raise ValueError("整合事务ID已绑定不同输入/写者")
        return {**old["receipt"], "idempotent_replay": True}
    if not complete(unit):
        raise ValueError("整合须基于已接受的唯一候选；未成文先由科学owner合入草稿")
    if unit.get("owner_interrupted"):
        raise ValueError("物理owner已中断，先如实登记宿主恢复；整合不冒充恢复")
    if payload.get("owner_observed") is not True:
        raise ValueError("整合须owner_observed:true及实际本人接收记录")
    text(payload.get("actual_owner_record"), "实际科学owner接收/确认记录")
    target = validate_target(root, payload.get("target"))
    if state.get("candidate_writers", {}).get(target) != ident:
        raise ValueError("整合只能由此最终目标唯一科学写者执行")
    stage = unit["rounds"][-1]
    candidate = next((item for item in (stage.get("accepted") or {}).get("candidates", []) if item["target"] == target), None)
    if not candidate or payload.get("base_sha256") != candidate["sha256"]:
        raise ValueError("整合基线不是当前已接受候选")
    prerequisite_records(root, state, unit)
    draft = inside(root, candidate["draft"])
    before = draft.read_text(encoding="utf-8")
    if digest(before) != candidate["sha256"]:
        raise ValueError("候选已被外部编辑；先重新审阅本人稿，禁止覆盖")
    formal = inside(root, target)
    formal_sha = digest(formal.read_bytes()) if formal.is_file() else None
    if "target_sha256" not in candidate or formal_sha != candidate["target_sha256"]:
        raise ValueError("正式目标已被外部编辑；旧整合基线失效")
    fragments = payload.get("fragments")
    if not isinstance(fragments, list) or not fragments:
        raise ValueError("整合须具体局部fragments")
    allowed = allowed_evidence(root, state, unit)
    after, evidence = before, []
    for fragment in fragments:
        original = text(fragment.get("old"), "待替换的精确原片段")
        new = fragment.get("new")
        if not isinstance(new, str) or original == new:
            raise ValueError("整合须实际变化new，可空表示删除")
        if before.count(original) != 1 or after.count(original) != 1:
            raise ValueError("整合原片段必须在基线中唯一；重叠/过期片段不能应用")
        text(fragment.get("locator"), "整合科学位置")
        if fragment.get("needs_owner_confirmation", True):
            text(fragment.get("owner_confirmation"), "科学owner对受影响判断的明确确认")
        refs = fragment.get("evidence")
        if not isinstance(refs, list) or not refs:
            raise ValueError("每个整合片段须实际依据")
        evidence.extend(located_item(root, item, allowed, "integration.evidence") for item in refs)
        after = after.replace(original, new, 1)
    if after == before:
        raise ValueError("整合未产生候选变化")
    working = deepcopy(state)
    result = reopen(working, ident, stage["id"], "科学owner接收有据局部diff")
    updated = find_unit(working, ident)
    updated["owner_active"] = True
    current_round(updated)["status"] = "preparing"
    evidence_versions = [preserved_version(root, state["run_id"], ident, "sources",
                                          {"path": item["path"], "sha256": item["sha256"]}, "evidence") for item in evidence]
    new_sha = digest(after)
    receipt = {"unit": ident, "target": target, "status": "preparing", "base_sha256": candidate["sha256"],
               "sha256": new_sha, "transaction_id": transaction, "final_review": "invalidated", "affected": result["affected"]}
    entry = {"request_sha256": request_sha, "agent_id": agent_id, "receipt": receipt, "fragments": fragments,
             "evidence_files": evidence_versions, "actual_owner_record": payload["actual_owner_record"], "at": utc_now()}
    updated.setdefault("integrations", {})[transaction] = entry
    # Preserve both candidate versions; only the new one can become accepted after checkpoint+final_review.
    new_version = inside(root, f".research/work/{state['run_id']}/units/{ident}/versions/{stage['id']}/integration-{new_sha}.md")
    if new_version.exists() and digest(new_version.read_bytes()) != new_sha:
        raise ValueError("内容寻址的整合版本损坏")
    if not new_version.exists():
        atomic_text(new_version, after)
    entry["candidate_version"] = snapshot(root, str(new_version.relative_to(root)))
    # Recheck live baselines immediately before the sole candidate writer commits.
    if digest(draft.read_bytes()) != candidate["sha256"] or (digest(formal.read_bytes()) if formal.is_file() else None) != formal_sha:
        raise ValueError("整合期间候选/正式目标并发变动；保留外部修改")
    atomic_text(draft, after)
    working["events"].append({"event": "candidate_integration", "event_type": "internal_checkpoint", "unit": ident,
                              "transaction_id": transaction, "agent_id": agent_id, "actual_host_call": False,
                              "candidate_sha256": new_sha, "at": entry["at"]})
    working["integration"] = None
    state.clear()
    state.update(working)
    return receipt


def compact_recovery(root: Path, state: dict, ident: str | None) -> dict:
    selected = [ident] if ident else list(state["units"])
    result = {"run_id": state["run_id"], "units": {}}
    for key in selected:
        unit = find_unit(state, key)
        try:
            current = next_packet(root, state, key)
            integrity = "fixed_files_match"
        except (ValueError, OSError, KeyError, TypeError) as exc:
            stage = current_round(unit)
            current = {"stage": stage["id"] if stage else None,
                       "status": stage["status"] if stage else "all_rounds_accepted", "action": "repair_fixed_inputs"}
            integrity = str(exc)
        result["units"][key] = {"owner": unit["owner"], "owner_active": owner_running(unit),
                               "science_state": unit.get("science_state"), "current": current,
                               "integrity": integrity, "inputs_ref": state_ref(state, key, "inputs"),
                               "accepted_ref": state_ref(state, key, "rounds")}
    return result


def candidates(root: Path, state: dict, finding: str | None = None, *, check_target_baselines: bool = True) -> dict:
    mapping = {}
    for unit in state["units"].values():
        if not complete(unit):
            raise ValueError("所有所选单元须完成接受；当前仍有未执行/待审科学轮")
        prerequisite_records(root, state, unit)
        unit_candidates = [value for stage in unit["rounds"] if stage["accepted"]
                           for value in stage["accepted"]["candidates"]]
        if unit_candidates:
            report = unit.get("final_report")
            if not report:
                raise ValueError("实质候选须owner最终report绑定当前科学记录: " + unit["id"])
            verify(root, [report])
            if read_json(inside(root, report["path"])).get("accepted_binding_sha256") != accepted_binding(unit):
                raise ValueError("owner最终report已过期: " + unit["id"])
            final = unit.get("final_review") or {}
            if (final.get("accepted_binding_sha256") != accepted_binding(unit) or
                    final.get("finding", {}).get("decision") != "accept"):
                raise ValueError("实质候选须当前绑定的实际final_review accept: " + unit["id"])
            validate_finding(unit, final["finding"], final=True)
            verify(root, [{"path": item["draft"], "sha256": item["sha256"]} for item in unit_candidates])
            for candidate in unit_candidates:
                formal = inside(root, candidate["target"])
                current_sha = digest(formal.read_bytes()) if formal.is_file() else None
                if check_target_baselines and "target_sha256" in candidate and candidate["target_sha256"] != current_sha:
                    raise ValueError("正式目标并发编辑使候选基线失效；先保护用户原文并重新合入: " + candidate["target"])
        for stage in unit["rounds"]:
            if stage["accepted"]:
                for value in stage["accepted"]["candidates"]:
                    if value["target"] in mapping:
                        raise ValueError("多个单元修改同一正式页；先顺序整合并重新接受具体候选，禁止覆盖")
                    writer = state.get("candidate_writers", {}).get(value["target"])
                    if writer and writer != unit["id"]:
                        raise ValueError("候选写者与计划绑定不符")
                    mapping[value["target"]] = value
    files = [snapshot(root, item["draft"]) for item in mapping.values()]
    result = {"status": "candidate_group", "mapping": mapping, "files": files,
              "publication_authorized_by_script": False,
              "scientific_verification": "attributed_stage_reviews; self_check_is_not_independent_review"}
    if finding:
        state["integration"] = {**result, "finding": text(finding, "成组语义链接/知识增量检查"), "at": utc_now()}
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=default_root())
    sub = parser.add_subparsers(dest="command", required=True)
    names = ("init", "status", "next", "recovery", "dispatch", "submit", "accept", "revise", "reopen",
             "tasks", "repeat", "inputs", "windows", "constraints", "interrupt", "candidates",
             "start-owner", "adopt-owner", "step", "report", "resource", "bootstrap", "checkpoint", "add-unit", "integrate")
    required_unit = {"next", "dispatch", "submit", "accept", "revise", "reopen", "tasks", "repeat", "inputs", "windows", "interrupt", "start-owner", "adopt-owner", "step", "report", "resource", "bootstrap", "checkpoint", "integrate"}
    for name in names:
        command = sub.add_parser(name)
        command.add_argument("--run", required=True)
        if name in required_unit:
            command.add_argument("--unit", required=True)
        elif name == "recovery":
            command.add_argument("--unit")
        if name in {"next", "status", "recovery"}:
            command.add_argument("--detail", choices=("compact", "full"), default="compact")
        if name in {"start-owner", "adopt-owner"}:
            command.add_argument("--event", type=Path, required=True)
        if name in {"bootstrap", "resource"}:
            command.add_argument("--refresh", action="store_true")
        if name in {"checkpoint", "integrate"}:
            command.add_argument("--agent-id", required=True)
            command.add_argument("--payload", type=Path, required=True)
        if name == "add-unit":
            command.add_argument("--plan", type=Path, required=True)
        if name == "step":
            command.add_argument("--agent-id", required=True)
            command.add_argument("--operation", choices=("begin", "awaiting-input", "submit", "accept", "revise"), required=True)
            command.add_argument("--payload", type=Path)
        if name == "report":
            group = command.add_mutually_exclusive_group()
            group.add_argument("--report", type=Path)
            group.add_argument("--finding", type=Path)
        if name == "resource":
            command.add_argument("--kind", choices=("guide", "protocol", "template"), required=True)
            command.add_argument("--index", type=int, default=0)
            command.add_argument("--section", action="append", default=[])
        for verb, option in (("init", "plan"), ("dispatch", "event"), ("submit", "result"),
                             ("accept", "finding"), ("revise", "finding"), ("tasks", "decisions"),
                             ("constraints", "update"), ("inputs", "files"), ("windows", "locations")):
            if name == verb:
                command.add_argument(f"--{option}", type=Path, required=True)
        if name in {"reopen", "repeat", "windows"}:
            command.add_argument("--stage", required=True)
        elif name == "inputs":
            command.add_argument("--stage", help="Earliest affected scientific boundary; required for replacement/premise change")
            command.add_argument("--mode", choices=("append", "replace", "changed-premise"), default="append")
        if name in {"reopen", "repeat", "inputs", "interrupt"}:
            command.add_argument("--reason", required=True)
        if name == "constraints":
            command.add_argument("--affected", action="append", default=[])
        if name == "candidates":
            command.add_argument("--finding", help="Optional concrete group-integration finding; no publication permission")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if not root.is_dir():
            raise ValueError("知识库root不存在")
        run = safe_id(args.run)
        with locked(root, f"workflow:{run}"):
            archive = archived_state_path(root, run)
            archived = archive.is_file()
            if archived and args.command not in {"status", "recovery"}:
                raise ValueError("该运行已有归档状态；仅允许status/recovery只读查看。不能继续派发、提交、改约束或重新init；新研究请使用新run")
            if args.command == "init":
                state = initialize(root, run, read_json(inside(root, args.plan)))
                value = status(root, state)
            else:
                state = read_json(archive if archived else state_path(root, run))
                if state.get("schema_version") != 2 or state.get("run_id") != run:
                    raise ValueError("workflow版本/运行ID不符；旧记录保留，不静默迁移")
                command = args.command
                if command == "status": value = status(root, state, args.detail)
                elif command == "next": value = next_packet(root, state, args.unit, args.detail)
                elif command == "recovery": value = recovery(root, state, args.unit, args.detail)
                elif command == "start-owner": value = start_owner(root, state, args.unit, read_json(inside(root, args.event)))
                elif command == "adopt-owner": value = adopt_owner(root, state, args.unit, read_json(inside(root, args.event)))
                elif command == "step": value = owner_step(root, state, args.unit, args.agent_id, args.operation,
                                                          read_json(inside(root, args.payload)) if args.payload else None)
                elif command == "report": value = final_report(root, state, args.unit,
                                                               read_json(inside(root, args.report)) if args.report else None,
                                                               read_json(inside(root, args.finding)) if args.finding else None)
                elif command == "resource": value = resource_packet(state, args.unit, args.kind, args.index, args.section, args.refresh)
                elif command == "bootstrap": value = bootstrap(root, state, args.unit, args.refresh)
                elif command == "add-unit": value = add_unit(root, state, read_json(inside(root, args.plan)))
                elif command == "integrate": value = integrate(root, state, args.unit, args.agent_id, read_json(inside(root, args.payload)))
                elif command == "checkpoint": value = checkpoint(root, state, args.unit, args.agent_id, read_json(inside(root, args.payload)))
                elif command == "dispatch": value = dispatch(root, state, args.unit, read_json(inside(root, args.event)))
                elif command == "submit": value = submit(root, state, args.unit, read_json(inside(root, args.result)))
                elif command in {"accept", "revise"}: value = review(root, state, args.unit, read_json(inside(root, args.finding)), command == "accept")
                elif command == "reopen": value = reopen(state, args.unit, args.stage, args.reason)
                elif command == "tasks": value = decide_tasks(state, args.unit, read_json(inside(root, args.decisions)))
                elif command == "repeat": value = repeat_round(state, args.unit, args.stage, args.reason)
                elif command == "inputs": value = refresh_inputs(root, state, args.unit, read_json(inside(root, args.files)), args.reason, args.stage, args.mode)
                elif command == "windows": value = locate_windows(root, state, args.unit, args.stage, read_json(inside(root, args.locations)))
                elif command == "constraints": value = update_constraints(state, read_json(inside(root, args.update)), args.affected)
                elif command == "interrupt": value = interrupt(state, args.unit, args.reason)
                else: value = candidates(root, state, args.finding)
                if archived:
                    value = archive_view(root, run, value, command)
            readonly = args.command in {"status", "next", "recovery"} or (
                args.command == "report" and not args.report and not args.finding) or (
                args.command == "candidates" and not args.finding)
            if not readonly:
                state["updated"] = utc_now()
                write_json(state_path(root, run), state)
            print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
