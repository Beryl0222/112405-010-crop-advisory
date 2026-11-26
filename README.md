# 农作物病害叙述与用药范围的审慎建议

县级植保站自然语言记录处理系统（合成演练）：持续接收农户短消息、技术员田间记录、农资标签摘录与检验结果，经三类角色分层把关后，才向具体农户发送可执行建议。名称及内容不对应真实主体，不提供真实农药或诊疗建议。资料口径见 [资料口径](docs/materials.md)，规则落位见 [业务规则与角色分工](docs/business-rules.md)。

## 角色与边界

1. **植保人员**判断观察现象与病害候选是否相符——相符不等于确诊，确诊只能来自阳性检验；
2. **农艺师**核对防治建议的作物、施用时点与现场条件（含标签"花期禁用"等限制）；
3. **服务人员**决定是否向具体农户发送，并管理通知的顶替、执行与撤回。

邻田/转发经验标记为传闻，不能作本田依据；阴性检验只排除对应病害，不自动确诊其他病害；建议更新只顶替仍未执行的通知，已完成田间操作保留当时依据快照；每条建议都可溯源到具体观察、检验与标签原句。

## 资料

- `data/case.json`：合成案件原文与稳定编号（CP-10～CP-27），保留原句；
- `data/reference.json`：合成作物、病害、药剂标签、症状关键词与传闻标记表。

## 运行

环境为 Python 3.11 或更新版本，无第三方依赖，数据落 SQLite。

```sh
# 检查（含资料完整性与全部业务规则测试）
python3 -m unittest discover -s tests -v

# 端到端演练：摄取 → 候选判断 → 检验排除 → 施药被标签拦截 → 观察建议 → 通知顶替
python3 -m plantstation.cli --db work/demo.db demo

# 分步办理（全局参数 --db 须在子命令之前）
python3 -m plantstation.cli --db work/case.db init
python3 -m plantstation.cli --db work/case.db ingest
python3 -m plantstation.cli --db work/case.db dossier --field T1
python3 -m plantstation.cli --db work/case.db validate --field T1 --product PRD-QN --evidence EV-xxxx
python3 -m plantstation.cli --db work/case.db explain-notice --notice N2
```

子命令一览：`init / ingest / dossier / propose / judge / validate / draft / approve / send / execute / withdraw / explain-rec / explain-notice / demo`。角色 ID 固定为 `U-STAFF`、`U-AGRO`、`U-SVC`，越权操作会被业务规则拦截并以非零码退出。

## 代码结构

```
plantstation/
  parser.py          规则化中文解析（传闻识别、症状抽取、标签条款切分）
  ingestion.py       资料摄取与既存通知快照
  models.py          枚举、数据类与业务异常
  database.py        SQLite schema
  repository.py      仓储层
  roles.py           角色加载与权限校验
  candidates.py      植保人员工作流（候选/符合性/检验应用）
  recommendations.py 农艺师工作流（起草/核对/批准/修订）
  notices.py         服务人员工作流（发送/顶替/执行/撤回/快照）
  provenance.py      溯源与田块档案
  pipeline.py        端到端演练编排
  cli.py             命令行入口
tests/               资料完整性、解析规则、工作流硬规则测试
```
