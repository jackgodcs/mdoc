# Contributing

公共仓库必须保持产品中立。不得提交真实工作区、`workspace.local.yaml`、`task.local.yaml`、截图、PDF、绝对 Windows 路径、用户产品名或凭据。

修改行为时以 `skill/mdoc/scripts/mdoc.py` 和相关公开脚本为测试接缝，按红—绿纵向切片开发。提交前运行：

```powershell
python -m unittest discover -s skill/mdoc/scripts -p "test_*.py" -v
python scripts/release_check.py
```

使用聚焦提交信息，例如 `feat(cli): add active-book status`。不要自动提交用户正式手册仓库。
