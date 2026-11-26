"""命令行入口。

用法示例（全局参数 --db 须放在子命令之前）：
  python3 -m plantstation.cli --db work/case.db init
  python3 -m plantstation.cli --db work/case.db ingest
  python3 -m plantstation.cli --db work/case.db dossier --field T1
  python3 -m plantstation.cli --db work/demo.db demo
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .database import connect, init_db
from .ingestion import IngestionService, load_json
from .models import PlantStationError, RecType
from .pipeline import run_demo
from .repository import Repository
from .provenance import explain_notice, explain_recommendation, field_dossier
from .roles import load_actor
from .candidates import judge_candidate, propose_candidate
from .recommendations import (
    approve,
    draft_monitor_only,
    draft_product_application,
    validate_product_application,
)
from .notices import issue_notice, mark_executed, withdraw_notice

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REFERENCE = ROOT / "data" / "reference.json"
DEFAULT_CASE = ROOT / "data" / "case.json"


def _repo(args) -> Repository:
    conn = connect(args.db)
    init_db(conn)
    return Repository(conn)


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def cmd_init(args) -> None:
    conn = connect(args.db)
    init_db(conn)
    conn.close()
    print(f"数据库已初始化：{args.db}")


def cmd_ingest(args) -> None:
    repo = _repo(args)
    reference = load_json(args.reference)
    case = load_json(args.case)
    ingestion = IngestionService(repo, reference)
    ingestion.seed_reference()
    ingestion.seed_fields(case)
    summary = ingestion.ingest_case(case)
    _print(summary)


def cmd_dossier(args) -> None:
    repo = _repo(args)
    _print(field_dossier(repo, args.field))


def cmd_propose(args) -> None:
    repo = _repo(args)
    actor = load_actor(repo, args.actor)
    evidence_ids = [s.strip() for s in args.evidence.split(",") if s.strip()]
    cid = propose_candidate(repo, actor, args.field, args.disease,
                            evidence_ids, args.note)
    _print({"candidate_id": cid})


def cmd_judge(args) -> None:
    repo = _repo(args)
    actor = load_actor(repo, args.actor)
    status = judge_candidate(repo, actor, args.candidate,
                             args.decision == "matched", args.note)
    _print({"candidate_id": args.candidate, "status": status.value})


def cmd_validate(args) -> None:
    repo = _repo(args)
    evidence_ids = [s.strip() for s in args.evidence.split(",") if s.strip()]
    report = validate_product_application(
        repo, args.field, args.product, evidence_ids
    )
    _print({
        "ok": report.ok,
        "errors": report.errors,
        "restrictions": report.restriction_texts,
    })


def cmd_draft(args) -> None:
    repo = _repo(args)
    actor = load_actor(repo, args.actor)
    evidence_ids = [s.strip() for s in args.evidence.split(",") if s.strip()]
    if args.type == RecType.MONITOR_ONLY.value:
        rec_id = draft_monitor_only(repo, actor, args.field, evidence_ids, args.note)
    else:
        rec_id = draft_product_application(
            repo, actor, args.field, args.product, evidence_ids, args.note
        )
    _print({"rec_id": rec_id})


def cmd_approve(args) -> None:
    repo = _repo(args)
    actor = load_actor(repo, args.actor)
    report = approve(repo, actor, args.rec)
    _print({"rec_id": report.rec_id or args.rec,
            "ok": report.ok, "errors": report.errors,
            "restrictions": report.restriction_texts})


def cmd_send(args) -> None:
    repo = _repo(args)
    actor = load_actor(repo, args.actor)
    notice_id = issue_notice(repo, actor, args.rec)
    _print({"notice_id": notice_id})


def cmd_execute(args) -> None:
    repo = _repo(args)
    actor = load_actor(repo, args.actor)
    mark_executed(repo, actor, args.notice)
    _print({"notice_id": args.notice, "status": "executed"})


def cmd_withdraw(args) -> None:
    repo = _repo(args)
    actor = load_actor(repo, args.actor)
    withdraw_notice(repo, actor, args.notice)
    _print({"notice_id": args.notice, "status": "withdrawn"})


def cmd_explain_rec(args) -> None:
    repo = _repo(args)
    _print(explain_recommendation(repo, args.rec))


def cmd_explain_notice(args) -> None:
    repo = _repo(args)
    _print(explain_notice(repo, args.notice))


def cmd_demo(args) -> None:
    conn = connect(args.db)
    init_db(conn)
    repo = Repository(conn)
    result = run_demo(repo, args.reference, args.case)
    _print(result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plantstation",
        description="县级植保站自然语言记录审慎处理（合成演练，无第三方依赖）",
    )
    parser.add_argument("--db", default="work/plantstation.db", help="SQLite 路径")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="初始化数据库").set_defaults(func=cmd_init)

    p = sub.add_parser("ingest", help="摄取参考数据与案件记录")
    p.add_argument("--reference", default=str(DEFAULT_REFERENCE))
    p.add_argument("--case", default=str(DEFAULT_CASE))
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("dossier", help="查看田块档案（证据/候选/通知/生育期）")
    p.add_argument("--field", required=True)
    p.set_defaults(func=cmd_dossier)

    p = sub.add_parser("propose", help="植保人员登记病害候选")
    p.add_argument("--actor", default="U-STAFF")
    p.add_argument("--field", required=True)
    p.add_argument("--disease", required=True)
    p.add_argument("--evidence", required=True, help="逗号分隔的证据 ID")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_propose)

    p = sub.add_parser("judge", help="植保人员判断候选相符/不符（不确诊）")
    p.add_argument("--actor", default="U-STAFF")
    p.add_argument("--candidate", required=True)
    p.add_argument("--decision", choices=["matched", "mismatched"], required=True)
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_judge)

    p = sub.add_parser("validate", help="农艺师核对施药建议（不落库）")
    p.add_argument("--field", required=True)
    p.add_argument("--product", required=True)
    p.add_argument("--evidence", required=True)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("draft", help="农艺师起草建议")
    p.add_argument("--actor", default="U-AGRO")
    p.add_argument("--field", required=True)
    p.add_argument("--type", choices=[t.value for t in RecType], required=True)
    p.add_argument("--product")
    p.add_argument("--evidence", required=True)
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_draft)

    p = sub.add_parser("approve", help="农艺师批准建议（内部强制复核）")
    p.add_argument("--actor", default="U-AGRO")
    p.add_argument("--rec", required=True)
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("send", help="服务人员发送通知（顶替未执行旧通知）")
    p.add_argument("--actor", default="U-SVC")
    p.add_argument("--rec", required=True)
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("execute", help="登记通知已执行（冻结依据快照）")
    p.add_argument("--actor", default="U-SVC")
    p.add_argument("--notice", required=True)
    p.set_defaults(func=cmd_execute)

    p = sub.add_parser("withdraw", help="撤回未执行通知")
    p.add_argument("--actor", default="U-SVC")
    p.add_argument("--notice", required=True)
    p.set_defaults(func=cmd_withdraw)

    p = sub.add_parser("explain-rec", help="溯源建议的全部观察与标签依据")
    p.add_argument("--rec", required=True)
    p.set_defaults(func=cmd_explain_rec)

    p = sub.add_parser("explain-notice", help="查看通知及其冻结依据快照")
    p.add_argument("--notice", required=True)
    p.set_defaults(func=cmd_explain_notice)

    p = sub.add_parser("demo", help="端到端演练并输出完整结果")
    p.add_argument("--reference", default=str(DEFAULT_REFERENCE))
    p.add_argument("--case", default=str(DEFAULT_CASE))
    p.set_defaults(func=cmd_demo)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except PlantStationError as exc:
        print(f"业务规则拦截：{exc}", file=sys.stderr)
        return 2
    except (ValueError, KeyError) as exc:
        print(f"输入错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
