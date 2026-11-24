# 农作物病害叙述与用药范围的审慎建议

这里保存本地业务联调所需的合成原始文本、对象关系和已有记录，名称及内容不对应真实主体。来源中的主张尚待使用者核实。资料口径见[资料口径](docs/materials.md)。

`data/case.json`保存原文及稳定编号，`docs`解释输入含义，`tests`检查资料完整性。当前入口仅可读取资料，不提供业务办理功能。

运行环境为Python 3.11或更新版本，无第三方依赖。读取资料：

```sh
python3 -m json.tool data/case.json
```

检查资料：

```sh
python3 -m unittest discover -s tests -v
```

检查仅验证现有资料格式和编号，不表示尚未提供的业务功能已经可用。
