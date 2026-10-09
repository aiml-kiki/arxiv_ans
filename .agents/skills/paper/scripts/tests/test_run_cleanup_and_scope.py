"""Focused regression checks for user-requested interrupted-run cleanup and review scope."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.notes import check, check_changes
from lib.publishing import finish, publish, stage


def note(ident, body):
    return f"---\nid: {ident}\nkind: knowledge\ntags: []\nanalysis: complete\nupdated: 2026-10-09\n---\n\n# {ident}\n\n{body}\n"


class ReviewScope(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "知识").mkdir()
        (self.root / "知识/A.md").write_text(note("a", "content ^stable"))
        (self.root / "知识/B.md").write_text(note("b", "[[A#^stable]]"))
        (self.root / "知识/Old.md").write_text("unrelated legacy error")

    def test_legacy_debt_remains_visible(self):
        report = check_changes(self.root, {"知识/A.md": note("a", "new content ^stable")})
        self.assertEqual(report["errors"], 0)
        self.assertEqual(report["historical_errors"], 1)
        self.assertEqual(check(self.root)["errors"], 1)

    def test_candidate_error_and_affected_inbound_link_block(self):
        for body in ("[[missing]] ^stable", "removed anchor"):
            with self.subTest(body=body):
                self.assertGreater(check_changes(self.root, {"知识/A.md": note("a", body)})["errors"], 0)
        self.assertGreater(check_changes(self.root, {"知识/A.md": None})["errors"], 0)

    def test_publish_and_actual_postwrite_check(self):
        content = note("a", "new content ^stable")
        preview = stage(self.root, {"知识/A.md": content}, "probe")
        publish(self.root, "probe", preview["digest"], "Explicit test authorization")
        self.assertEqual((self.root / "知识/A.md").read_text(), content)
        baseline = check(self.root)
        (self.root / "知识/A.md").write_text(note("a", "external [[missing]]"))
        self.assertGreater(check_changes(self.root, {"知识/A.md": content}, baseline, actual=True)["errors"], 0)


class InterruptedCleanup(unittest.TestCase):
    def fixture(self, root, status, active=False):
        work = root / ".research/work/probe"
        artifact = work / "units/p/results/R1.md"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("accepted source-grounded result")
        state = {"schema_version": 2, "run_id": "probe", "units": {
            "p": {"id": "p", "owner_active": active, "rounds": [{"id": "R1", "status": status}],
                  "artifact": {"path": str(artifact.relative_to(root)),
                               "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}}}}
        (work / "workflow.json").write_text(json.dumps(state))
        return work

    def test_live_owners_and_pending_reviews_are_protected(self):
        for status, active in (("executing", False), ("awaiting_review", False), ("ready", True)):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as td:
                root = Path(td); work = self.fixture(root, status, active)
                with self.assertRaises(ValueError):
                    finish(root, "probe", abandon_reason="User explicitly requests restart")
                self.assertTrue(work.exists())
                self.assertFalse((root / ".research/records/runs/probe").exists())

    def test_interruption_and_references_survive_archive(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); work = self.fixture(root, "interrupted")
            finish(root, "probe", abandon_reason="User explicitly requests restart")
            self.assertFalse(work.exists())
            archived = json.loads((root / ".research/records/runs/probe/workflow.json").read_text())
            unit = archived["units"]["p"]
            self.assertEqual(unit["rounds"][0]["status"], "interrupted")
            self.assertEqual((root / unit["artifact"]["path"]).read_text(), "accepted source-grounded result")
            self.assertEqual(finish(root, "probe")["status"], "already_finished")

    def test_missing_authorization_or_unknown_work_is_protected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); self.fixture(root, "interrupted")
            with self.assertRaises(ValueError):
                finish(root, "probe", abandon_reason="  ")
            with self.assertRaises(ValueError):
                finish(root, "unknown", abandon_reason="restart")


if __name__ == "__main__":
    unittest.main()
