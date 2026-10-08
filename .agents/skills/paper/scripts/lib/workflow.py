"""Coordinator-owned round state. Records actual host calls; never calls an LLM.

Run through ``uv run .../research.py --root VAULT workflow ...``. Fields and
hashes are checked here; a coordinator must judge the scientific answers.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
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
    if len(value.get("tasks", {})) != 48:
        raise ValueError("叶任务目录须恰有48项")
    return value["tasks"]


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
    path = inside(root, f".research/work/{run}/units/{ident}/versions/{stage}/{label}-{file['sha256']}.md")
    raw = original.read_bytes()
    if digest(raw) != file["sha256"]:
        raise ValueError("保存版本时工作文件已变动，停止提交")
    if path.exists():
        if digest(path.read_bytes()) != file["sha256"]:
            raise ValueError("内容寻址的历史版本损坏，不覆盖")
    else:
        atomic_text(path, raw.decode("utf-8"))
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


def initialize(root: Path, run: str, plan: dict) -> dict:
    if archived_state_path(root, run).exists():
        raise ValueError("该运行已有归档状态；仅status/recovery可只读查看。继续研究须使用新run引用已有材料，不能重建或覆盖旧运行")
    if state_path(root, run).exists():
        raise ValueError("已有运行；用status/next/recovery接续，禁止重新初始化覆盖")
    goal = text(plan.get("goal"), "具体研究目标")
    if not isinstance(plan.get("constraints", []), list):
        raise ValueError("constraints须为用户约束记录列表")
    specs = plan.get("units")
    if not isinstance(specs, list) or not specs:
        raise ValueError("plan.units须是非空列表")
    tasks, units, outputs = catalog(), {}, set()
    limit = plan.get("max_workers", 2)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("max_workers须为正整数，默认2；提高须有实际资源/用户依据")
    if limit > 2:
        text(plan.get("worker_limit_reason"), "提高并发上限理由")
    for spec in specs:
        ident, kind = safe_id(spec["id"]), spec.get("kind")
        if ident in units or kind not in STAGES:
            raise ValueError("单元ID重复或kind不是read/compare/trace/knowledge")
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
            validate_target(root, target)
        applicable = {key for _, leaves, _ in STAGES[kind] for key in leaves}
        decisions = {key: {"status": "selected", "reason": f"当前{kind}目标需要此叶任务"} for key in applicable}
        if kind == "read":
            for key in ("B4", "B5"):
                decisions[key] = {"status": "pending", "reason": "依据固定论文的学习/理论主张在R2b前选择；不可按模板自动假定"}
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
                        "output_dir": str(output_dir.relative_to(root)), "draft_dir": str(draft_dir.relative_to(root)),
                        "draft": str(draft.relative_to(root)), "target": target,
                        "declared_input_paths": list(dict.fromkeys(spec.get("inputs", []))),
                        "source_windows": spec.get("source_windows", {}), "question": spec.get("question"),
                        "recovery_notes": [], "requested_profile": spec.get("requested_profile"),
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
            "max_workers": limit, "units": units, "task_scope": selection,
            "constraints": plan.get("constraints", []), "constraint_history": [], "events": [],
            "authorization": plan.get("authorization", {}), "integration": None,
            "semantic_verification": "coordinator_required_not_performed_by_script"}


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
                records.append(stage["accepted"])
            if stage["id"] == needed["id"]:
                break
    for stage in unit["rounds"]:
        if stage["accepted"]:
            verify(root, stage["accepted"]["files"])
            verify(root, stage["accepted"].get("evidence_files", []))
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


def short_record(record: dict) -> dict:
    return {key: record.get(key) for key in ("unit", "stage", "files", "answers", "evidence", "coverage",
                                            "judgment_changes", "critical_details", "gaps", "finding")}


def short_revision(value: dict) -> dict:
    result = {key: value[key] for key in ("decision", "reason", "finding", "at") if key in value}
    if value.get("previous_result"):
        result["previous_files"] = value["previous_result"]["files"]
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


def relevant_records(state: dict, unit: dict, stage: dict, records: list[dict]) -> tuple[list[dict], list[dict]]:
    """Select semantic inputs before packaging; other accepted versions stay addressable."""
    own_needs = {
        "R1": set(), "R2a": {"R1"}, "R2b": {"R1", "R2a"}, "R2c": {"R1", "R2a", "R2b"},
        "R3a": {"R2a", "R2b", "R2c"}, "R3b": {"R2a", "R2b", "R2c", "R3a"},
        "M1": set(), "M2": {"M1"}, "M3": {"M1", "M2"},
        "T0": set(), "T1": {"T0"}, "T2": {"T0", "T1"}, "T3": {"T1", "T2", "T3"},
        "T4": {"T1", "T2", "T3"}, "T5": {"T1", "T4"},
        "K1": set(), "K2": {"K1"}, "K3": {"K1", "K2"},
    }
    dependency_needs = {
        "M1": {"R2a", "R2b", "R2c", "R4"},
        "M2": {"R2a", "R2b", "R2c", "R3a"},
        "M3": {"R2c", "R3a", "R3b"},
        "K1": {"R4", "M4", "T6", "K4"},
        "K2": {"R4", "M4", "T6", "K4"},
    }
    composition = stage["base"] == FINAL[unit["kind"]]
    primary, addressable = [], []
    for record in records:
        base = record["stage"].split("-", 1)[0]
        own = record["unit"] == unit["id"]
        keep = (own and (composition or base in own_needs.get(stage["base"], set()))) or (
            not own and base in dependency_needs.get(stage["base"], set()))
        if keep:
            primary.append(short_record(record))
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


def next_packet(root: Path, state: dict, ident: str) -> dict:
    unit = find_unit(state, ident)
    stage = current_round(unit)
    if stage is None:
        prerequisite_records(root, state, unit)
        return {"unit": ident, "status": "all_rounds_accepted", "candidate_kind": unit["candidate_kind"],
                "draft": str(inside(root, unit["draft"])), "publication": "requires_group_review_and_actual_authorization"}
    records = prerequisite_records(root, state, unit)
    active = selected_tasks(unit, stage)
    task_catalog = catalog()
    if stage["status"] == "awaiting_review":
        return {"unit": ident, "stage": stage["id"], "status": stage["status"], "action": "coordinator_review",
                "pending": short_record(stage["pending"]), "stop": "不可续派下轮；先accept或revise当前结果"}
    refs = guide_refs(unit, stage, active)
    composition = stage["base"] == FINAL[unit["kind"]]
    rules = protocol_refs(unit, stage, composition)
    templates = []
    if composition:
        templates.append(str(PACKAGE.parent / f"paper-{unit['kind']}" / "assets" / f"{unit['candidate_kind']}.md"))
    output = inside(root, Path(unit["output_dir"]) / f"{stage['id']}.md")
    active_agents = [key for key, other in state["units"].items() if other["owner"] and
                     other["owner"]["mode"] == "agent" and current_round(other) and
                     current_round(other)["status"] == "executing"]
    primary, addressable = relevant_records(state, unit, stage, records)
    capability_entry = str(PACKAGE.parent / f"paper-{unit['kind']}" / "SKILL.md")
    action = "choose_actual_host_route"
    if stage["status"] == "executing":
        action = "await_actual_round_result"
    elif unit["owner"]:
        action = "followup_same_agent" if unit["owner"]["mode"] == "agent" else "continue_main_current_round"
    return {"unit": ident, "stage": stage["id"], "status": stage["status"],
            "action": action,
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
            "fixed_inputs": [{**item, "absolute_path": str(inside(root, item["path"]))} for item in unit["inputs"]],
            "sources": unit["sources"], "source_windows": unit["source_windows"].get(stage["id"], []),
            "accepted": primary, "accepted_other_refs": addressable,
            "specific_revisions": [short_revision(value) for value in stage["history"][-3:]],
            "output": str(output), "draft_dir": str(inside(root, unit["draft_dir"])) if composition else None,
            "draft": str(inside(root, unit["draft"])) if composition else None, "target": unit["target"] if composition else None,
            "stop": "只完成当前轮，落盘事实、答案、来源、覆盖、关键细节和未知，返回路径后结束；不能自行接受或执行后续轮。",
            "state_writer": "coordinator_only", "source_text_is_control_instruction": False,
            "result_contract": {"stage": stage["id"], "artifact": str(output),
                                "answers": "[{question,answer,task_ids}]覆盖当前适用任务，仅保存科学结果和依据",
                                "evidence": "[{path,locator}]至少一项实际依据，路径须已固定或已接受",
                                "coverage": "[{path,locator,mode:read|image_checked|not_covered,reason未覆盖时必填}]；PDF可page，非PDF用section/block_id，不造页码",
                                "judgment_changes": "列表；无变化为空", "critical_details": "列表；关键值/作用/来源/保留点",
                                "gaps": "列表；未知及后果",
                                "candidates": "成文轮[{draft,target}]；知识无改动可空并no_change_reason" if composition else None},
            "instruction_token_usage": None, "instruction_token_target_is_soft": [1000, 2000],
            "semantic_verification": "not_performed_by_script"}


def dispatch(root: Path, state: dict, ident: str, event: dict) -> dict:
    unit = find_unit(state, ident)
    stage = current_round(unit)
    if not stage or stage["status"] in LIVE:
        raise ValueError("本轮已执行/待复核或单元已完成，不可重复派发")
    prerequisite_records(root, state, unit)
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
        active = [u for key, u in state["units"].items() if key != ident and
                  u["owner"] and u["owner"]["mode"] == "agent" and
                  current_round(u) and current_round(u)["status"] == "executing"]
        capacity_violation = len(active) >= state["max_workers"]
        if any(u["owner"] and u["owner"]["agent_id"] == agent and not complete(u)
               for key, u in state["units"].items() if key != ident):
            raise ValueError("同一物理代理不能同时拥有两个尚未完成独立单元")
    record = {"at": utc_now(), "unit": ident, "stage": stage["id"], **event,
              "agent_id": agent, "tool": tool, "call_id": call_id, "actual_record": actual_record,
              "actual_model": event.get("actual_model"), "actual_effort": event.get("actual_effort")}
    if capacity_violation:
        record["capacity_violation"] = "实际调用超过上限；协调者须中断超额宿主执行并登记interrupt，不能删除真实事件"
    unit["owner"] = identity
    unit["owner_history"].append(identity | {"at": record["at"]})
    stage["dispatches"].append(record)
    stage["status"] = "executing"
    state["events"].append({"event": "actual_dispatch", **record})
    state["integration"] = None
    return {"unit": ident, "stage": stage["id"], "status": stage["status"], "record": record,
            "requires_actual_host_interrupt": capacity_violation,
            "limitation": "这是协调者提供的实际记录；脚本未调用宿主，不能独立证明其真实性"}


def allowed_evidence(root: Path, state: dict, unit: dict) -> dict[str, dict]:
    records = prerequisite_records(root, state, unit)
    files = {item["path"]: item for item in unit["inputs"]}
    for dep in unit["depends_on"]:
        other = find_unit(state, dep["unit"])
        files.update({item["path"]: item for item in other["inputs"]})
    for record in records:
        files.update({item["path"]: item for item in record["files"]})
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
    stage = current_round(unit)
    if not stage or stage["status"] != "executing" or not stage["dispatches"]:
        raise ValueError("提交必须绑定本轮已登记的实际执行事件，不能用路由偏好冒充调用")
    if result.get("stage") != stage["id"]:
        raise ValueError("只能交当前轮，不能一份结果跳过三遍或未来轮")
    active_agents = [other for other in state["units"].values() if other["owner"] and
                    other["owner"]["mode"] == "agent" and current_round(other) and
                    current_round(other)["status"] == "executing"]
    if len(active_agents) > state["max_workers"]:
        raise ValueError("实际代理调用超额，先真实中断超额执行并登记interrupt，不能以提交结果消除违规记录")
    allowed = allowed_evidence(root, state, unit)
    active = selected_tasks(unit, stage)
    artifact = snapshot(root, str(Path(unit["output_dir"]) / f"{stage['id']}.md"))
    if inside(root, artifact["path"]).parent != inside(root, unit["output_dir"]):
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
    files = [preserved_version(root, state["run_id"], ident, stage["id"], artifact, "artifact")]
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
            candidates.append({"draft": draft["path"], "target": target, "sha256": draft["sha256"]})
            files.append(preserved_version(root, state["run_id"], ident, stage["id"], draft, f"candidate-{len(candidates)}"))
            files.append(draft)
        if len({item["target"] for item in candidates}) != len(candidates):
            raise ValueError("同一单元不能对同一正式页提交多份候选")
    evidence_files = list({item["path"]: {"path": item["path"], "sha256": item["sha256"]}
                           for item in evidence + coverage}.values())
    pending = {"unit": ident, "stage": stage["id"], "files": files, "answers": answers,
               "evidence": evidence, "evidence_files": evidence_files, "coverage": coverage,
               "judgment_changes": result["judgment_changes"], "critical_details": result["critical_details"],
               "gaps": result["gaps"], "candidates": candidates, "no_change_reason": result.get("no_change_reason"),
               "dispatch": deepcopy(stage["dispatches"][-1]), "at": utc_now()}
    stage["pending"], stage["status"] = pending, "awaiting_review"
    state["integration"] = None
    return {"unit": ident, "stage": stage["id"], "status": "awaiting_coordinator_review", "files": files,
            "semantic_verification": "not_performed_by_script"}


def review(root: Path, state: dict, ident: str, finding: dict, accept: bool) -> dict:
    unit = find_unit(state, ident)
    stage = current_round(unit)
    if not stage or stage["status"] != "awaiting_review" or not stage["pending"]:
        raise ValueError("没有待复核的当前轮结果")
    text(finding.get("reviewer"), "实际协调复核者")
    text(finding.get("summary"), "具体接受/返工原因")
    checks = finding.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("finding.checks须列具体核验问题、发现和产物/来源定位")
    for check in checks:
        text(check.get("question"), "核验问题")
        text(check.get("finding"), "核验发现")
        text(check.get("locator"), "核验产物/来源定位")
    prerequisite_records(root, state, unit)
    verify(root, stage["pending"]["files"])
    verify(root, stage["pending"]["evidence_files"])
    record = deepcopy(stage["pending"]) | {"finding": finding, "reviewed_at": utc_now()}
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
            "scientific_judgment": "coordinator_recorded_not_independently_verified_by_script"}


def clear_from(unit: dict, stage: str, reason: str) -> set[str]:
    start = unit["rounds"].index(find_round(unit, stage))
    changed = set()
    for item in unit["rounds"][start:]:
        if item["status"] != "skipped":
            changed.add(item["id"])
            item["history"].append({"decision": "reopen", "reason": reason, "at": utc_now(),
                                    "previous_result": item["accepted"] or item["pending"]})
            item["accepted"], item["pending"], item["status"] = None, None, "revision_required"
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


def refresh_inputs(root: Path, state: dict, ident: str, values: list[str], reason: str, stage: str | None = None) -> dict:
    unit = find_unit(state, ident)
    if current_round(unit) and current_round(unit)["status"] in LIVE:
        raise ValueError("执行/待复核时不可改变材料，先中断或revise")
    files, sources = freeze_inputs(root, values, unit["kind"])
    reason = text(reason, "固定材料新增/替换理由")
    first = stage or next(item["id"] for item in unit["rounds"] if item["status"] != "skipped")
    result = reopen(state, ident, first, reason)
    unit["inputs"], unit["sources"] = files, sources
    return result | {"fixed_inputs": files}


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
    if stage["status"] == "awaiting_review":
        raise ValueError("已交结果应复核或revise，无需将可核对结果标为执行中断")
    record = {"event": "interrupted", "stage": stage["id"], "reason": text(reason, "实际中断原因"), "at": utc_now()}
    stage["history"].append(record)
    stage["status"] = "interrupted"
    state["events"].append(record | {"unit": ident})
    return record


def status(root: Path, state: dict) -> dict:
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
                            "blocked_by": errors, "pending_task_decisions": [key for key, value in unit["tasks"].items() if value["status"] == "pending"],
                            "accepted_stages": [item["id"] for item in unit["rounds"] if item["status"] == "accepted"],
                            "skipped": {item["id"]: item.get("skip_reasons") for item in unit["rounds"] if item["status"] == "skipped"}}
    return {"run_id": state["run_id"], "goal": state["goal"], "max_workers": state["max_workers"], "units": summaries,
            "integration": state["integration"], "semantic_verification": state["semantic_verification"]}


def recovery(root: Path, state: dict, ident: str | None) -> dict:
    units = {ident: find_unit(state, ident)} if ident else state["units"]
    result = {"run_id": state["run_id"], "goal": state["goal"], "user_constraints": state["constraints"],
              "constraint_history": state["constraint_history"], "authorization": state["authorization"], "units": {}}
    for key, unit in units.items():
        accepted = [short_record(item["accepted"]) for item in unit["rounds"] if item["accepted"]]
        try:
            prerequisite_records(root, state, unit)
            integrity = "fixed_files_match"
        except (ValueError, OSError, KeyError, TypeError) as exc:
            integrity = str(exc)
        current = current_round(unit)
        result["units"][key] = {"owner": unit["owner"], "sources": unit["sources"], "accepted": accepted,
                              "integrity": integrity, "current_stage": current["id"] if current else None,
                              "current_status": current["status"] if current else "all_rounds_accepted",
                              "pending": short_record(current["pending"]) if current and current["pending"] else None,
                              "latest_revision": [short_revision(value) for value in current["history"][-1:]] if current else [], "handover": unit["handover"],
                              "next": "先复核待审结果；否则编译当前工作包并真实续派原代理，不能重复已接受轮"}
    return result


def candidates(root: Path, state: dict, finding: str | None = None) -> dict:
    mapping = {}
    for unit in state["units"].values():
        if not complete(unit):
            raise ValueError("所有所选单元须完成接受；当前仍有未执行/待审科学轮")
        prerequisite_records(root, state, unit)
        for stage in unit["rounds"]:
            if stage["accepted"]:
                for value in stage["accepted"]["candidates"]:
                    if value["target"] in mapping:
                        raise ValueError("多个单元修改同一正式页；先顺序整合并重新接受具体候选，禁止覆盖")
                    mapping[value["target"]] = value
    files = [snapshot(root, item["draft"]) for item in mapping.values()]
    result = {"status": "candidate_group", "mapping": mapping, "files": files,
              "publication_authorized_by_script": False, "scientific_verification": "coordinator_recorded"}
    if finding:
        state["integration"] = {**result, "finding": text(finding, "成组语义链接/知识增量检查"), "at": utc_now()}
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=default_root())
    sub = parser.add_subparsers(dest="command", required=True)
    names = ("init", "status", "next", "recovery", "dispatch", "submit", "accept", "revise", "reopen",
             "tasks", "repeat", "inputs", "windows", "constraints", "interrupt", "candidates")
    required_unit = {"next", "dispatch", "submit", "accept", "revise", "reopen", "tasks", "repeat", "inputs", "windows", "interrupt"}
    for name in names:
        command = sub.add_parser(name)
        command.add_argument("--run", required=True)
        if name in required_unit:
            command.add_argument("--unit", required=True)
        elif name == "recovery":
            command.add_argument("--unit")
        for verb, option in (("init", "plan"), ("dispatch", "event"), ("submit", "result"),
                             ("accept", "finding"), ("revise", "finding"), ("tasks", "decisions"),
                             ("constraints", "update"), ("inputs", "files"), ("windows", "locations")):
            if name == verb:
                command.add_argument(f"--{option}", type=Path, required=True)
        if name in {"reopen", "repeat", "windows"}:
            command.add_argument("--stage", required=True)
        elif name == "inputs":
            command.add_argument("--stage", help="Coordinator-assessed earliest affected round; defaults to first applicable round")
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
                if command == "status": value = status(root, state)
                elif command == "next": value = next_packet(root, state, args.unit)
                elif command == "recovery": value = recovery(root, state, args.unit)
                elif command == "dispatch": value = dispatch(root, state, args.unit, read_json(inside(root, args.event)))
                elif command == "submit": value = submit(root, state, args.unit, read_json(inside(root, args.result)))
                elif command in {"accept", "revise"}: value = review(root, state, args.unit, read_json(inside(root, args.finding)), command == "accept")
                elif command == "reopen": value = reopen(state, args.unit, args.stage, args.reason)
                elif command == "tasks": value = decide_tasks(state, args.unit, read_json(inside(root, args.decisions)))
                elif command == "repeat": value = repeat_round(state, args.unit, args.stage, args.reason)
                elif command == "inputs": value = refresh_inputs(root, state, args.unit, read_json(inside(root, args.files)), args.reason, args.stage)
                elif command == "windows": value = locate_windows(root, state, args.unit, args.stage, read_json(inside(root, args.locations)))
                elif command == "constraints": value = update_constraints(state, read_json(inside(root, args.update)), args.affected)
                elif command == "interrupt": value = interrupt(state, args.unit, args.reason)
                else: value = candidates(root, state, args.finding)
                if archived:
                    value = archive_view(root, run, value, command)
            readonly = args.command in {"status", "next", "recovery"} or (args.command == "candidates" and not args.finding)
            if not readonly:
                state["updated"] = utc_now()
                write_json(state_path(root, run), state)
            print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
