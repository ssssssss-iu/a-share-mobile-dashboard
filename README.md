# A股双通道研究看板（GitHub 零成本版）

这是一个只生成静态页面的 A 股研究看板。GitHub Actions 在上海时间的五个盘面节点抓取公开行情，执行主板硬过滤和 A/B/C/D/E/G 评分，然后把结果发布到 GitHub Pages。

它不连接券商、不自动下单、不承诺收益。页面中的触发价、失效位和评分只用于条件化研究。

## 本地运行

```bash
python3 -m pip install -r requirements.txt
python3 scripts/build_dashboard.py
python3 -m http.server 8080 --directory site
```

浏览器打开 `http://127.0.0.1:8080`。

## GitHub 发布

1. 新建一个空 GitHub 仓库，把本目录作为仓库根目录推送。
2. 进入仓库 `Settings → Pages`，将 Source 设为 `GitHub Actions`。
3. 进入 `Actions → 更新并发布A股研究看板 → Run workflow`，先手动运行一次。
4. 运行成功后，Pages 地址会出现在工作流的 deploy 步骤与仓库 Pages 设置页。

定时节点为上海时间 09:25、10:30、13:00、14:30、15:05。GitHub 定时任务可能延迟，页面始终显示行情时间和实际生成时间。

## 公开数据边界

仓库只保存静态看板与公开行情结果。不要把持仓、成本价、交易笔记、SQLite 数据库或任何密钥加入这个公开仓库。

评分规则位于 `config/scoring_candidate.json`。它是把已锁定模块权重转成机器可执行条件的试运行参数，不修改原策略；页面会明确显示参数状态。修改阈值后应重新进行前向验证。
